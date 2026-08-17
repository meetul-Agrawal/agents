"""Phase 5 gate: SA-2 recovery — deterministic dates, verified claims, recorded
promises/events, idempotency, and the missed-promise transition.

Logic tests are hermetic: the write layer (`services`) and the clock are
monkeypatched, so no MongoDB and no LLM. One idempotency test hits a real test
database and skips when Mongo is unavailable.
"""

from __future__ import annotations

import datetime as dt
from datetime import date

import pytest

from ca import customer360 as c3
from ca import orchestrator as orc
from ca import sa1_general as sa1
from ca import sa2_recovery as sa2
from ca import services
from ca.contracts import AgentTask, CustomerAssistState, Event, PaymentPromise, Task

CID = "6a6464a39f707bd30403b6cb"
TODAY = date(2026, 8, 16)


# --------------------------------------------------------------------------
# Deterministic due-date parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("I'll pay 2 lakh by 20 August", date(2026, 8, 20)),
        ("payment by 20th August", date(2026, 8, 20)),
        ("will clear on August 20", date(2026, 8, 20)),
        ("by 2026-08-25", date(2026, 8, 25)),
        ("paying on 25/08", date(2026, 8, 25)),
        ("25/08/2026 for sure", date(2026, 8, 25)),
        ("25-08-2026", date(2026, 8, 25)),
        ("tomorrow", date(2026, 8, 17)),
        ("day after tomorrow", date(2026, 8, 18)),
        ("in 3 days", date(2026, 8, 19)),
        ("next week", date(2026, 8, 23)),
        ("by end of month", date(2026, 8, 31)),
        ("I'll pay by 10 January", date(2027, 1, 10)),  # past this year -> next year
        ("sometime soon", None),
        ("", None),
    ],
)
def test_parse_due_date(text, expected):
    assert sa2.parse_due_date(text, TODAY) == expected


def test_next_weekday_is_always_in_the_future():
    due = sa2.parse_due_date("I'll pay next Friday", TODAY)
    assert due.weekday() == 4 and due > TODAY


# --------------------------------------------------------------------------
# Missed-promise transition (pure)
# --------------------------------------------------------------------------


def _promise(**kw) -> PaymentPromise:
    base = dict(customer_id=CID, amount=100000.0, due_date=date(2026, 8, 10))
    return PaymentPromise(**{**base, **kw})


def test_is_missed_only_when_past_due_and_unpaid():
    assert services.is_missed(_promise(), date(2026, 8, 16))          # overdue, unpaid
    assert not services.is_missed(_promise(), date(2026, 8, 9))       # not yet due
    assert not services.is_missed(_promise(paid_amount=100000.0), date(2026, 8, 16))  # paid
    assert not services.is_missed(_promise(status="paid"), date(2026, 8, 16))         # already settled


# --------------------------------------------------------------------------
# SA-2 behaviour — services + clock stubbed
# --------------------------------------------------------------------------


@pytest.fixture
def recorder(monkeypatch):
    """Capture what SA-2 would commit, without a database, and pin the clock."""
    promises: list[tuple] = []
    events: list[tuple] = []
    tasks: list[tuple] = []

    def fake_promise(cid, amount, due, **kw):
        p = PaymentPromise(customer_id=cid, amount=amount, due_date=due,
                           conversation_id=kw.get("conversation_id"))
        kind = kw.pop("_kind", "created")
        promises.append((amount, due, kind))
        return p, kind

    def fake_event(cid, type, source, **kw):
        events.append((type, kw.get("payload") or {}))
        return Event(customer_id=cid, type=type, source=source), True

    def fake_task(cid, kind, title, **kw):
        tasks.append((kind, kw.get("due_date")))
        return Task(customer_id=cid, kind=kind, title=title, due_date=kw.get("due_date")), True

    monkeypatch.setattr(services, "record_promise", fake_promise)
    monkeypatch.setattr(services, "record_event", fake_event)
    monkeypatch.setattr(services, "create_task", fake_task)
    monkeypatch.setattr(sa2, "utcnow", lambda: dt.datetime(2026, 8, 16, tzinfo=dt.timezone.utc))
    return promises, events, tasks


def _task(*intents, entities=None):
    return AgentTask(agent="sa2_recovery", action="+".join(intents),
                     inputs={"intents": list(intents), "entities": entities or {}})


def _state(message, customer_id=CID):
    return CustomerAssistState(channel="chat", message=message, customer_id=customer_id,
                              entities={"message_id": "MSG-1"})


def test_promise_records_amount_and_parsed_date(recorder):
    promises, events, tasks = recorder
    result = sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
                     _state("I'll pay 2 lakh by 20 August."))
    assert promises == [(200000.0, date(2026, 8, 20), "created")]
    assert events[0][0] == "PAYMENT_PROMISE_CREATED"
    assert events[0][1]["amount"] == 200000.0 and events[0][1]["due_date"] == "2026-08-20"
    assert "200,000.00" in result.customer_message and "20 Aug 2026" in result.customer_message
    assert any(a.type == "create_payment_promise" and a.executed for a in result.actions)


def test_promise_without_a_date_asks_for_one_and_records_nothing(recorder):
    promises, events, tasks = recorder
    result = sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
                     _state("I will pay 2 lakh soon."))
    assert promises == []
    assert result.status == "needs_information"
    assert "by when" in result.customer_message.lower()
    assert events[0][0] == "RECOVERY_CONTACTED"


def test_unable_to_pay_records_contact_not_a_promise(recorder):
    promises, events, tasks = recorder
    result = sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
                     _state("I cannot pay right now, no money this month."))
    assert promises == []
    assert events[0][1]["outcome"] == "unable_to_pay"
    assert "unable" in result.customer_message.lower()


def test_promise_modification_emits_a_modified_event(recorder, monkeypatch):
    promises, events, tasks = recorder

    def fake_modify(cid, amount, due, **kw):
        return PaymentPromise(customer_id=cid, amount=amount, due_date=due), "modified"

    monkeypatch.setattr(services, "record_promise", fake_modify)
    result = sa2.run(_task("payment_promise", entities={"amounts": [150000.0]}),
                     _state("Actually I'll pay 1.5 lakh by 22 August instead."))
    assert events[0][0] == "PAYMENT_PROMISE_MODIFIED"
    assert "updated" in result.customer_message.lower()


# --------------------------------------------------------------------------
# Payment claim — verified against receipts, never thanked on faith
# --------------------------------------------------------------------------


def test_unverifiable_payment_claim_is_not_confirmed(recorder, monkeypatch):
    """Adversarial: 'I paid 2 lakh' with no matching receipt must ask for a
    reference, not thank the customer."""
    monkeypatch.setattr(c3, "get_receipts", lambda cid, limit=20: [])
    result = sa2.run(_task("payment_claim", entities={"amounts": [200000.0]}),
                     _state("I paid 2 lakh yesterday, please clear my account."))
    msg = result.customer_message.lower()
    assert "could not" in msg and "reference" in msg
    assert "thank you" not in msg


def test_claim_with_no_amount_does_not_confirm_an_unrelated_receipt(recorder, monkeypatch):
    """Regression: 'I paid, please check' with no amount extracted must not
    match the first receipt in the list regardless of what it is — that
    confirms an unrelated old receipt as if it settled the claim."""
    monkeypatch.setattr(
        c3, "get_receipts",
        lambda cid, limit=20: [{"voucher_number": "RCT/OLD", "date": date(2026, 1, 1), "amount": 999.0}],
    )
    result = sa2.run(_task("payment_claim", entities={}),
                     _state("I already paid, please check my account."))
    msg = result.customer_message.lower()
    assert "could not" in msg and "reference" in msg
    assert "rct/old" not in msg


def test_matching_receipt_confirms_the_claim(recorder, monkeypatch):
    monkeypatch.setattr(
        c3, "get_receipts",
        lambda cid, limit=20: [{"voucher_number": "RCT/88", "date": date(2026, 8, 15), "amount": 200000.0}],
    )
    result = sa2.run(_task("payment_claim", entities={"amounts": [200000.0]}),
                     _state("I paid 2 lakh yesterday."))
    assert "RCT/88" in result.customer_message
    assert "200,000.00" in result.customer_message


def test_no_customer_yields_needs_information(recorder):
    result = sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
                     _state("I'll pay 2 lakh by 20 August.", customer_id=None))
    assert result.status == "needs_information"


# --------------------------------------------------------------------------
# Follow-up tasks
# --------------------------------------------------------------------------


def test_recorded_promise_creates_a_reminder_task(recorder):
    promises, events, tasks = recorder
    sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
            _state("I'll pay 2 lakh by 20 August."))
    assert tasks == [("reminder", date(2026, 8, 20))]


def test_unable_to_pay_creates_a_followup_task(recorder):
    promises, events, tasks = recorder
    sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
            _state("I cannot pay right now, no money this month."))
    assert tasks == [("recovery_followup", date(2026, 8, 19))]


def test_unverifiable_claim_creates_a_trace_task(recorder, monkeypatch):
    promises, events, tasks = recorder
    monkeypatch.setattr(c3, "get_receipts", lambda cid, limit=20: [])
    sa2.run(_task("payment_claim", entities={"amounts": [200000.0]}),
            _state("I paid 2 lakh yesterday."))
    assert tasks == [("payment_trace", date(2026, 8, 18))]


def test_verified_claim_creates_no_task(recorder, monkeypatch):
    promises, events, tasks = recorder
    monkeypatch.setattr(
        c3, "get_receipts",
        lambda cid, limit=20: [{"voucher_number": "RCT/88", "date": date(2026, 8, 15), "amount": 200000.0}],
    )
    sa2.run(_task("payment_claim", entities={"amounts": [200000.0]}), _state("I paid 2 lakh."))
    assert tasks == []


# --------------------------------------------------------------------------
# Shared LLM phrasing pass — verified against the template like SA-1
# --------------------------------------------------------------------------


def test_grounded_rewrite_is_used(recorder, monkeypatch):
    warm = "Thanks! We've noted you'll pay ₹200,000.00 by 20 Aug 2026. A reminder will follow."
    monkeypatch.setattr(sa1, "_llm_phrase", lambda template: warm)
    result = sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
                     _state("I'll pay 2 lakh by 20 August."))
    assert result.customer_message == warm


def test_rewrite_that_invents_a_figure_is_rejected(recorder, monkeypatch):
    monkeypatch.setattr(sa1, "_llm_phrase", lambda template: template + " You also owe ₹999,999.00.")
    result = sa2.run(_task("payment_promise", entities={"amounts": [200000.0]}),
                     _state("I'll pay 2 lakh by 20 August."))
    assert "999,999.00" not in result.customer_message
    assert "200,000.00" in result.customer_message


# --------------------------------------------------------------------------
# Through the orchestrator
# --------------------------------------------------------------------------


def test_orchestrator_routes_a_promise_to_the_real_sa2(recorder, monkeypatch):
    monkeypatch.setattr(c3, "build_customer_360", lambda cid, **kw: None)
    state = orc.handle("I'll pay 2 lakh by 20 August.", customer_id=CID, message_id="MSG-9")
    summary = orc.summarize(state)
    assert summary["agents"] == ["sa2_recovery"]
    assert summary["statuses"] == ["completed"]
    assert "200,000.00" in state.final_response and "20 Aug 2026" in state.final_response


# --------------------------------------------------------------------------
# Idempotency — real database, skipped when Mongo is unavailable
# --------------------------------------------------------------------------


@pytest.fixture
def db():
    from pymongo.errors import PyMongoError

    from ca.config import _client

    name = "customer_assist_test"
    try:
        database = _client()[name]
        database.command("ping")
    except PyMongoError as exc:
        pytest.skip(f"MongoDB unavailable: {exc}")
    _client().drop_database(name)
    yield database
    _client().drop_database(name)


def test_replayed_message_does_not_create_a_second_promise(db):
    for _ in range(2):
        services.record_promise(CID, 200000.0, date(2026, 8, 20), message_id="MSG-DUP", db=db)
    assert db["payment_promises"].count_documents({"customer_id": CID}) == 1


def test_second_message_modifies_the_open_promise(db):
    _, k1 = services.record_promise(CID, 200000.0, date(2026, 8, 20), message_id="MSG-A", db=db)
    p2, k2 = services.record_promise(CID, 150000.0, date(2026, 8, 22), message_id="MSG-B", db=db)
    assert k1 == "created" and k2 == "modified"
    assert db["payment_promises"].count_documents({"customer_id": CID}) == 1
    assert p2.amount == 150000.0


def test_replayed_message_does_not_create_a_second_task(db):
    for _ in range(2):
        services.create_task(CID, "reminder", "Collect due", due_date=date(2026, 8, 20),
                             message_id="MSG-T", db=db)
    assert db["tasks"].count_documents({"customer_id": CID}) == 1


def test_sweep_flips_a_past_due_promise_and_emits_an_event(db):
    services.record_promise(CID, 200000.0, date(2026, 8, 10), message_id="MSG-OLD", db=db)
    flipped = services.sweep_missed_promises(CID, as_of=date(2026, 8, 16), db=db)
    assert len(flipped) == 1
    assert db["payment_promises"].find_one({"customer_id": CID})["status"] == "missed"
    assert db["events"].count_documents({"type": "PAYMENT_PROMISE_MISSED"}) == 1
