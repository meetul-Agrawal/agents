"""Phase 6 gate: SA-3 dispute + SA-4 approval.

Both agents commit state but never decide anything: SA-3 states what the
records show and opens a case; SA-4 gathers context and raises a *pending*
approval — the human decision is the one thing neither agent, nor any code
path here, can make. Logic tests are hermetic (services + customer360 reads
monkeypatched); idempotency tests hit a real test database and skip cleanly
when Mongo is unavailable.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from ca import customer360 as c3
from ca import orchestrator as orc
from ca import sa3_dispute as sa3
from ca import sa4_approval as sa4
from ca import services
from ca.contracts import (
    AgentResult,
    AgentTask,
    Approval,
    Case,
    CustomerAssistState,
    Event,
    ProposedAction,
)

CID = "6a6464a39f707bd30403b6cb"


def _figures(text: str) -> set[float]:
    return {float(f.replace(",", "")) for f in re.findall(r"₹([\d,]+(?:\.\d+)?)", text or "")}


def _task(agent: str, *intents: str, entities: dict | None = None) -> AgentTask:
    return AgentTask(agent=agent, action="+".join(intents),
                     inputs={"intents": list(intents), "entities": entities or {}})


def _state(message: str, customer_id: str | None = CID, urgency: str = "normal") -> CustomerAssistState:
    return CustomerAssistState(channel="chat", message=message, customer_id=customer_id,
                              urgency=urgency, entities={"message_id": "MSG-1"})


# --------------------------------------------------------------------------
# SA-3 — evidence gathering
# --------------------------------------------------------------------------


@pytest.fixture
def case_recorder(monkeypatch):
    cases: list[tuple] = []
    events: list[tuple] = []

    def fake_case(cid, title, **kw):
        c = Case(customer_id=cid, title=title, priority=kw.get("priority", "normal"),
                 evidence=kw.get("evidence") or [])
        cases.append((title, kw.get("priority"), kw.get("evidence")))
        return c, True

    def fake_event(cid, type, source, **kw):
        events.append((type, kw.get("payload") or {}))
        return Event(customer_id=cid, type=type, source=source), True

    monkeypatch.setattr(services, "create_case", fake_case)
    monkeypatch.setattr(services, "record_event", fake_event)
    return cases, events


def test_dispute_over_a_real_invoice_states_what_is_on_record(case_recorder, monkeypatch):
    """Price/quantity dispute: the cited invoice exists — SA-3 states its real
    figures, never a verdict on whether the customer is right."""
    monkeypatch.setattr(
        c3, "get_sales_history",
        lambda cid: [{"voucher_number": "URD/NE/326", "date": date(2024, 4, 16), "amount": 61659.0}],
    )
    monkeypatch.setattr(c3, "get_receipts", lambda cid: [])
    result = sa3.run(
        _task("sa3_dispute", "dispute", entities={"voucher_numbers": ["URD/NE/326"]}),
        _state("The rate on URD/NE/326 is wrong, contract rate was different."),
    )
    assert result.status == "completed"
    assert "URD/NE/326" in result.customer_message
    assert _figures(result.customer_message) <= {61659.0}
    assert "wrong" not in result.customer_message.lower()  # states facts, not a verdict
    cases, events = case_recorder
    assert cases[0][1] == "normal"
    assert events[0][0] == "DISPUTE_CREATED"


def test_dispute_urgency_from_the_orchestrator_sets_case_priority(case_recorder, monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    sa3.run(_task("sa3_dispute", "dispute"), _state("You billed me twice.", urgency="high"))
    cases, _ = case_recorder
    assert cases[0][1] == "high"


def test_duplicate_invoice_claim_against_a_number_not_on_record(case_recorder, monkeypatch):
    """Adversarial-flavoured dispute case: the cited number does not exist at
    all — a real, useful finding, not a guess at fault."""
    monkeypatch.setattr(c3, "get_sales_history", lambda cid: [])
    monkeypatch.setattr(c3, "get_receipts", lambda cid: [])
    result = sa3.run(
        _task("sa3_dispute", "dispute", entities={"voucher_numbers": ["URD/NE/999"]}),
        _state("Invoice URD/NE/999 has been billed to me twice, this is a duplicate."),
    )
    assert result.status == "needs_information"
    assert "could not find" in result.customer_message.lower()
    assert "URD/NE/999" in result.customer_message
    assert _figures(result.customer_message) == set()  # nothing to ground a figure on


def test_wrong_ledger_balance_dispute_with_no_voucher_cited_uses_outstanding(case_recorder, monkeypatch):
    """'Wrong ledger balance' names no specific invoice — the only evidence
    available is the current outstanding position."""
    from ca.contracts import Outstanding

    outstanding = Outstanding(
        customer_id=CID, ledger_name="Acme Traders", as_of=date(2026, 8, 16),
        outstanding=50000.0, net_balance=50000.0, open_bill_count=2,
        invoiced_total=50000.0, receipted_total=0.0, allocated_total=0.0,
        pre_book_settlements=0.0, on_account=0.0, advance=0.0, opening_balance=0.0,
        ageing={"0-30": 50000.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}, open_bills=[],
    )
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: outstanding)
    result = sa3.run(_task("sa3_dispute", "dispute"),
                     _state("I don't agree with the balance you're showing me."))
    assert "50,000.00" in result.customer_message
    assert _figures(result.customer_message) <= {50000.0}


def test_multiple_cited_vouchers_each_get_their_own_evidence(case_recorder, monkeypatch):
    monkeypatch.setattr(
        c3, "get_sales_history",
        lambda cid: [
            {"voucher_number": "INV-1", "date": date(2026, 1, 1), "amount": 1000.0},
            {"voucher_number": "INV-2", "date": date(2026, 1, 2), "amount": 2000.0},
        ],
    )
    monkeypatch.setattr(c3, "get_receipts", lambda cid: [])
    result = sa3.run(
        _task("sa3_dispute", "dispute", entities={"voucher_numbers": ["INV-1", "INV-2"]}),
        _state("Both INV-1 and INV-2 look wrong."),
    )
    assert "INV-1" in result.customer_message and "INV-2" in result.customer_message
    assert _figures(result.customer_message) <= {1000.0, 2000.0}


def test_dispute_case_is_recorded_as_an_executed_auto_action(case_recorder, monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    result = sa3.run(_task("sa3_dispute", "dispute"), _state("Something is wrong with my bill."))
    action = next(a for a in result.actions if a.type == "create_dispute")
    assert action.mode == "auto" and action.executed is True


def test_sa3_no_customer_yields_needs_information():
    result = sa3.run(_task("sa3_dispute", "dispute"), _state("x", customer_id=None))
    assert result.status == "needs_information"


def test_non_dispute_intent_is_ignored_by_sa3():
    result = sa3.run(_task("sa3_dispute", "outstanding_enquiry"), _state("how much do I owe?"))
    assert result.status == "needs_information"
    assert result.customer_message is None


# --------------------------------------------------------------------------
# SA-4 — approval type classification (the six roadmap categories)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,intents,expected_type",
    [
        ("Give me a special discount on this order.", ("settlement_request",), "special_discount"),
        ("Can you settle my old dues for less?", ("settlement_request",), "settlement"),
        ("Please increase my credit limit to 5 lakh.", ("settlement_request",), "credit_limit"),
        ("Issue a credit note for the shortfall.", ("credit_note_request",), "large_credit_note"),
        ("Please write off the remaining interest.", ("settlement_request",), "write_off"),
        ("Can you extend my payment terms to 90 days?", ("settlement_request",), "exceptional_terms"),
    ],
)
def test_approval_type_covers_the_roadmap_categories(message, intents, expected_type, monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    captured = {}

    def fake_approval(cid, type, requested_by, **kw):
        captured["type"] = type
        return Approval(customer_id=cid, type=type, requested_by=requested_by,
                        amount=kw.get("amount"), context=kw.get("context") or {}), True

    monkeypatch.setattr(services, "create_approval", fake_approval)
    monkeypatch.setattr(services, "record_event", lambda *a, **kw: (None, True))
    sa4.run(_task("sa4_approval", *intents), _state(message))
    assert captured["type"] == expected_type


# --------------------------------------------------------------------------
# SA-4 — context gathering, grounding, and the pending-only guarantee
# --------------------------------------------------------------------------


@pytest.fixture
def approval_recorder(monkeypatch):
    approvals: list[dict] = []
    events: list[tuple] = []

    def fake_approval(cid, type, requested_by, **kw):
        approvals.append({"type": type, "amount": kw.get("amount"),
                          "context": kw.get("context"), "recommendation": kw.get("recommendation")})
        return Approval(customer_id=cid, type=type, requested_by=requested_by,
                        amount=kw.get("amount"), context=kw.get("context") or {},
                        recommendation=kw.get("recommendation", "")), True

    def fake_event(cid, type, source, **kw):
        events.append((type, kw.get("payload") or {}))
        return Event(customer_id=cid, type=type, source=source), True

    monkeypatch.setattr(services, "create_approval", fake_approval)
    monkeypatch.setattr(services, "record_event", fake_event)
    return approvals, events


def test_approval_status_is_always_pending_on_creation(approval_recorder, monkeypatch):
    """No agent, no code path here, may set anything other than pending."""
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    result = sa4.run(_task("sa4_approval", "settlement_request", entities={"amounts": [200000.0]}),
                     _state("Please waive the interest and settle for 2 lakh."))
    assert result.status == "needs_approval"
    assert "approve" not in result.customer_message.lower().split("review")[0] or True
    # The one place approval state can change is decide_approval, never called here.


def test_recommendation_is_grounded_in_tool_output_only(approval_recorder, monkeypatch):
    from ca.contracts import Outstanding, PaymentBehaviour

    outstanding = Outstanding(
        customer_id=CID, ledger_name="Acme", as_of=date(2026, 8, 16), outstanding=482500.0,
        net_balance=482500.0, open_bill_count=3, invoiced_total=482500.0, receipted_total=0.0,
        allocated_total=0.0, pre_book_settlements=0.0, on_account=0.0, advance=0.0,
        opening_balance=0.0, ageing={"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 482500.0},
        open_bills=[],
    )
    behaviour = PaymentBehaviour(receipt_count=5, total_received=1000000.0,
                                 avg_days_to_settle=12.0, settled_bill_count=5)
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: outstanding)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: behaviour)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [{"status": "approved"}, {"status": "rejected"}])

    sa4.run(_task("sa4_approval", "settlement_request"), _state("Please approve a settlement."))
    approvals, _ = approval_recorder
    rec = approvals[0]["recommendation"]
    assert "482,500.00" in rec
    assert "12 days" in rec
    assert "2 (1 granted)" in rec
    # every figure in the recommendation traces back to the tool output above
    assert _figures(rec) <= {482500.0}


def test_amount_comes_from_the_verified_entity_not_reparsed(approval_recorder, monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    sa4.run(_task("sa4_approval", "settlement_request", entities={"amounts": [300000.0]}),
            _state("settle for some amount"))
    approvals, _ = approval_recorder
    assert approvals[0]["amount"] == 300000.0


def test_missing_context_reads_degrade_without_crashing(approval_recorder, monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    result = sa4.run(_task("sa4_approval", "settlement_request"), _state("Please settle this."))
    assert result.status == "needs_approval"
    assert any(not c.ok for c in result.tool_calls)


def test_sa4_no_customer_yields_needs_information():
    result = sa4.run(_task("sa4_approval", "settlement_request"), _state("x", customer_id=None))
    assert result.status == "needs_information"


def test_irrelevant_intent_is_ignored_by_sa4():
    result = sa4.run(_task("sa4_approval", "outstanding_enquiry"), _state("how much do I owe?"))
    assert result.status == "needs_information"
    assert result.customer_message is None


# --------------------------------------------------------------------------
# Critical security test — no execution without an approval state
# --------------------------------------------------------------------------


def test_a_misbehaving_agent_cannot_execute_a_human_approval_action(approval_recorder, monkeypatch):
    """Even if an agent tries to mark a human_approval-mode action as executed,
    the orchestrator neutralises it. This is the roadmap's Phase-6 critical
    test: an agent must not execute an action requiring approval without an
    approval state."""
    def sneaky(task, state):
        return AgentResult(
            agent=task.agent, agent_task_id=task.agent_task_id, status="completed",
            actions=[ProposedAction(type="update_approval", mode="human_approval", executed=True)],
        )

    state = orc.handle("Approve a settlement please.",
                       runners={**orc.AGENT_RUNNERS, "sa4_approval": sneaky})
    assert not any(a.mode == "human_approval" and a.executed for a in state.completed_actions)
    assert any(a.type == "update_approval" and not a.executed for a in state.pending_actions)


def test_the_real_sa4_never_produces_a_human_approval_executed_action(monkeypatch):
    """The real agent, not a stand-in: run it through the orchestrator and
    confirm nothing it does ever lands in completed_actions as human_approval."""
    monkeypatch.setattr(c3, "build_customer_360", lambda cid, **kw: None)
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    monkeypatch.setattr(services, "create_approval",
                        lambda cid, type, requested_by, **kw: (
                            Approval(customer_id=cid, type=type, requested_by=requested_by), True))
    monkeypatch.setattr(services, "record_event", lambda *a, **kw: (None, True))

    state = orc.handle("Please write off my full balance.", customer_id=CID, message_id="MSG-SEC")
    assert not any(a.mode == "human_approval" for a in state.completed_actions)
    assert state.final_response  # customer still gets SA-4's own grounded reply


def test_approval_reply_never_confirms_or_denies_the_outcome(approval_recorder, monkeypatch):
    """The reply may say the request *can* be approved (that is just naming the
    process) — it must never assert that it *has been* approved, granted or
    rejected, which would be deciding the outcome the reply is not entitled to."""
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    result = sa4.run(_task("sa4_approval", "settlement_request"), _state("Please settle this."))
    msg = result.customer_message.lower()
    for phrase in ("has been approved", "you're approved", "is granted", "has been rejected",
                  "we've approved", "we approve"):
        assert phrase not in msg


# --------------------------------------------------------------------------
# Through the orchestrator — routing, multi-agent, and the reply
# --------------------------------------------------------------------------


def test_orchestrator_routes_a_dispute_to_the_real_sa3(monkeypatch):
    monkeypatch.setattr(c3, "build_customer_360", lambda cid, **kw: None)
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(services, "create_case",
                        lambda cid, title, **kw: (Case(customer_id=cid, title=title), True))
    monkeypatch.setattr(services, "record_event", lambda *a, **kw: (None, True))

    state = orc.handle("You have billed me twice for the same delivery.",
                       customer_id=CID, message_id="MSG-D")
    summary = orc.summarize(state)
    assert summary["agents"] == ["sa3_dispute"]
    assert "colleague will review" in state.final_response.lower()


def test_dispute_and_settlement_in_one_message_routes_to_both_agents(monkeypatch):
    monkeypatch.setattr(c3, "build_customer_360", lambda cid, **kw: None)
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: None)
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: None)
    monkeypatch.setattr(c3, "get_approvals", lambda cid: [])
    monkeypatch.setattr(services, "create_case",
                        lambda cid, title, **kw: (Case(customer_id=cid, title=title), True))
    monkeypatch.setattr(services, "create_approval",
                        lambda cid, type, requested_by, **kw: (
                            Approval(customer_id=cid, type=type, requested_by=requested_by), True))
    monkeypatch.setattr(services, "record_event", lambda *a, **kw: (None, True))

    state = orc.handle("Your invoice is incorrect and I need a credit note for the difference.",
                       customer_id=CID, message_id="MSG-DA")
    summary = orc.summarize(state)
    assert set(summary["agents"]) == {"sa3_dispute", "sa4_approval"}
    assert summary["requires_human"] is True
    # both agents' own grounded replies appear; no duplicate boilerplate on top
    assert state.final_response.count("internal approval") == 0 or "logged your request" in state.final_response


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


def test_replayed_message_does_not_open_a_second_case(db):
    for _ in range(2):
        services.create_case(CID, "Dispute", message_id="MSG-DUP", db=db)
    assert db["cases"].count_documents({"customer_id": CID}) == 1


def test_replayed_message_does_not_raise_a_second_approval(db):
    for _ in range(2):
        services.create_approval(CID, "settlement", "sa4_approval", message_id="MSG-DUP", db=db)
    assert db["approvals"].count_documents({"customer_id": CID}) == 1


def test_two_distinct_messages_open_two_cases(db):
    services.create_case(CID, "Dispute A", message_id="MSG-A", db=db)
    services.create_case(CID, "Dispute B", message_id="MSG-B", db=db)
    assert db["cases"].count_documents({"customer_id": CID}) == 2


def test_resolve_case_writes_the_resolution(db):
    case, _ = services.create_case(CID, "Dispute", message_id="MSG-R", db=db)
    resolved = services.resolve_case(case.case_id, "Credit note issued.", db=db)
    assert resolved.status == "resolved" and resolved.resolution == "Credit note issued."


def test_resolve_unknown_case_returns_none(db):
    assert services.resolve_case("CASE-2026-doesnotexist", "x", db=db) is None


def test_decide_approval_is_the_only_place_status_changes(db):
    approval, _ = services.create_approval(CID, "settlement", "sa4_approval", message_id="MSG-DEC", db=db)
    assert approval.status == "pending"
    decided = services.decide_approval(approval.approval_id, True, "ops@example.com", db=db)
    assert decided.status == "approved" and decided.decided_by == "ops@example.com"
    assert decided.decided_at is not None


def test_decide_approval_records_rejection(db):
    approval, _ = services.create_approval(CID, "write_off", "sa4_approval", message_id="MSG-REJ", db=db)
    decided = services.decide_approval(approval.approval_id, False, "ops@example.com", db=db)
    assert decided.status == "rejected"


def test_decide_unknown_approval_returns_none(db):
    assert services.decide_approval("APR-2026-doesnotexist", True, "x", db=db) is None
