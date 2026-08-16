"""Phase 4 gate: SA-1 answers only from the read services, never invents a
figure, refuses cross-customer requests, and degrades instead of crashing.

Every test is hermetic: the Customer-360 reads are monkeypatched, so the suite
needs neither MongoDB nor an LLM.
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest

from ca import customer360 as c3
from ca import orchestrator as orc
from ca import sa1_general as sa1
from ca.contracts import (
    AgentTask,
    CustomerAssistState,
    OpenBill,
    Outstanding,
    PaymentBehaviour,
)
from ca.registry import AGENTS, get_agent

CID = "6a6464a39f707bd30403b6cb"

# Distinct figures so a hallucinated number would stand out.
OUTSTANDING = Outstanding(
    customer_id=CID,
    ledger_name="Acme Traders",
    as_of=date(2026, 8, 16),
    outstanding=200000.0,
    open_bill_count=1,
    invoiced_total=200000.0,
    receipted_total=0.0,
    allocated_total=0.0,
    pre_book_settlements=0.0,
    on_account=0.0,
    advance=0.0,
    opening_balance=0.0,
    ageing={"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 200000.0},
    open_bills=[
        OpenBill(
            voucher_number="URD/NE/327",
            invoice_date=date(2026, 1, 1),
            invoice_amount=200000.0,
            allocated=0.0,
            outstanding=200000.0,
            age_days=227,
            bucket="90+",
        )
    ],
)


def _task(*intents: str, entities: dict | None = None) -> AgentTask:
    return AgentTask(
        agent="sa1_general",
        action="+".join(intents),
        inputs={"intents": list(intents), "entities": entities or {}},
    )


def _state(customer_id: str | None = CID, message: str = "?") -> CustomerAssistState:
    return CustomerAssistState(channel="chat", message=message, customer_id=customer_id)


def _figures(text: str) -> set[float]:
    return {float(f.replace(",", "")) for f in re.findall(r"₹([\d,]+(?:\.\d+)?)", text)}


# --------------------------------------------------------------------------
# Grounding — the number always comes from the tool
# --------------------------------------------------------------------------


def test_outstanding_answer_states_the_service_figure(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    result = sa1.run(_task("outstanding_enquiry"), _state())
    assert result.status == "completed"
    assert "Acme Traders" in result.customer_message  # answer names the account it is for
    assert "200,000.00" in result.customer_message
    assert "URD/NE/327" in result.customer_message


def test_reply_never_contains_a_figure_the_tool_did_not_return(monkeypatch):
    """The core Phase-4 guarantee: no number reaches the customer that did not
    come out of a read service."""
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    result = sa1.run(_task("outstanding_enquiry"), _state())

    allowed = (
        {OUTSTANDING.outstanding}
        | set(OUTSTANDING.ageing.values())
        | {b.outstanding for b in OUTSTANDING.open_bills}
        | {b.invoice_amount for b in OUTSTANDING.open_bills}
    )
    assert _figures(result.customer_message) <= allowed


def test_settled_account_is_reported_plainly(monkeypatch):
    settled = OUTSTANDING.model_copy(update={"outstanding": 0.0, "open_bill_count": 0, "open_bills": []})
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: settled)
    result = sa1.run(_task("outstanding_enquiry"), _state())
    assert "fully settled" in result.customer_message
    assert _figures(result.customer_message) == set()


# --------------------------------------------------------------------------
# Tool calls — recorded, permitted, and reviewed clean
# --------------------------------------------------------------------------


def test_reads_are_recorded_as_permitted_tool_calls(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    result = sa1.run(_task("outstanding_enquiry"), _state())

    allowed = set(get_agent("sa1_general").tools)
    assert result.tool_calls
    assert all(call.tool in allowed for call in result.tool_calls)


def test_recorded_tools_pass_the_orchestrator_review(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    result = sa1.run(_task("outstanding_enquiry"), _state())
    reviewed = orc.review(_state().model_copy(update={"agent_results": [result]}))
    assert "review_problems" not in reviewed.get("entities", {})


def test_get_outstanding_is_a_registered_tool_sa1_may_call():
    assert "get_outstanding" in AGENTS["sa1_general"].tools


# --------------------------------------------------------------------------
# Security — cross-customer requests are refused, not answered
# --------------------------------------------------------------------------


def test_cross_customer_request_is_refused_and_reads_nothing(monkeypatch):
    def forbidden(cid):
        raise AssertionError("SA-1 must not read for a cross-customer request")

    monkeypatch.setattr(c3, "get_outstanding", forbidden)
    result = sa1.run(_task("cross_customer_request"), _state())
    assert result.status == "completed"
    assert "only share information about your own account" in result.customer_message
    assert result.tool_calls == []


def test_refusal_wins_even_alongside_a_normal_enquiry(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    result = sa1.run(_task("cross_customer_request", "outstanding_enquiry"), _state())
    assert "200,000.00" not in (result.customer_message or "")
    assert result.tool_calls == []


# --------------------------------------------------------------------------
# Degrading, not crashing
# --------------------------------------------------------------------------


def test_a_failing_read_degrades_and_is_flagged(monkeypatch):
    def boom(cid):
        raise c3.CustomerNotFoundError(cid)

    monkeypatch.setattr(c3, "get_outstanding", boom)
    result = sa1.run(_task("outstanding_enquiry"), _state())
    assert result.status in {"completed", "needs_information"}
    assert result.tool_calls and result.tool_calls[0].ok is False
    assert "CustomerNotFoundError" in result.tool_calls[0].error


def test_no_customer_yields_needs_information():
    result = sa1.run(_task("outstanding_enquiry"), _state(customer_id=None))
    assert result.status == "needs_information"
    assert result.customer_message is None


def test_ambiguous_reference_is_left_to_the_orchestrator(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    result = sa1.run(_task("ambiguous_reference"), _state())
    assert result.customer_message is None
    assert result.tool_calls == []


# --------------------------------------------------------------------------
# Other read intents
# --------------------------------------------------------------------------


def test_payment_history_answer(monkeypatch):
    behaviour = PaymentBehaviour(
        receipt_count=3, total_received=450000.0, last_receipt=date(2026, 7, 1),
        avg_days_to_settle=18.0,
    )
    monkeypatch.setattr(c3, "get_payment_history", lambda cid: behaviour)
    result = sa1.run(_task("payment_history_enquiry"), _state())
    assert "3 payment(s)" in result.customer_message
    assert "450,000.00" in result.customer_message
    assert _figures(result.customer_message) <= {450000.0}


def test_sales_history_lists_invoices(monkeypatch):
    rows = [
        {"voucher_number": "URD/NE/500", "date": date(2026, 6, 1), "amount": 12000.0},
        {"voucher_number": "URD/NE/499", "date": date(2026, 5, 1), "amount": 8000.0},
    ]
    monkeypatch.setattr(c3, "get_sales_history", lambda cid, limit=5: rows)
    result = sa1.run(_task("sales_history_enquiry"), _state())
    assert "URD/NE/500" in result.customer_message
    assert _figures(result.customer_message) <= {12000.0, 8000.0}


# --------------------------------------------------------------------------
# Product price enquiry — looks at the customer's own line items, asks when unsure
# --------------------------------------------------------------------------

_ATTA_HISTORY = [
    {"voucher_number": "INV-9", "date": date(2026, 4, 22), "amount": 5000.0,
     "items": [{"name": "Aashirvaad Atta 5kg", "qty": 10, "rate": 250.0, "amount": 2500.0}]},
    {"voucher_number": "INV-8", "date": date(2026, 4, 1), "amount": 4800.0,
     "items": [{"name": "Aashirvaad Atta 5kg", "qty": 10, "rate": 240.0, "amount": 2400.0}]},
]


def test_product_price_reports_last_rate_from_line_items(monkeypatch):
    monkeypatch.setattr(c3, "get_sales_history", lambda cid, limit=None: _ATTA_HISTORY)
    result = sa1.run(_task("sales_history_enquiry"),
                     _state(message="I want the last price of atta 5kg"))
    assert result.status == "completed"
    assert "Aashirvaad Atta 5kg" in result.customer_message
    assert "250.00" in result.customer_message and "INV-9" in result.customer_message
    assert "240" not in result.customer_message  # only the latest price, not the older one


def test_product_price_is_grounded_in_the_line_item(monkeypatch):
    monkeypatch.setattr(c3, "get_sales_history", lambda cid, limit=None: _ATTA_HISTORY)
    result = sa1.run(_task("sales_history_enquiry"),
                     _state(message="last price of atta 5kg"))
    assert _figures(result.customer_message) <= {250.0}


def test_ambiguous_product_asks_which_one(monkeypatch):
    history = [{"voucher_number": "INV-1", "date": date(2026, 4, 22), "amount": 730.0, "items": [
        {"name": "Atta 5kg", "qty": 1, "rate": 250.0, "amount": 250.0},
        {"name": "Atta 10kg", "qty": 1, "rate": 480.0, "amount": 480.0},
    ]}]
    monkeypatch.setattr(c3, "get_sales_history", lambda cid, limit=None: history)
    result = sa1.run(_task("sales_history_enquiry"), _state(message="what's the price of atta?"))
    assert result.status == "needs_information"
    assert "Atta 5kg" in result.customer_message and "Atta 10kg" in result.customer_message
    assert "which" in result.customer_message.lower()


def test_named_product_not_in_orders_asks_to_confirm(monkeypatch):
    history = [{"voucher_number": "INV-1", "date": date(2026, 4, 22), "amount": 60.0,
                "items": [{"name": "Sugar 1kg", "qty": 1, "rate": 60.0, "amount": 60.0}]}]
    monkeypatch.setattr(c3, "get_sales_history", lambda cid, limit=None: history)
    result = sa1.run(_task("sales_history_enquiry"), _state(message="last price of atta 5kg"))
    assert result.status == "needs_information"
    assert "couldn't find" in result.customer_message.lower()


def test_no_product_named_still_lists_recent_invoices(monkeypatch):
    monkeypatch.setattr(c3, "get_sales_history", lambda cid, limit=None: _ATTA_HISTORY)
    result = sa1.run(_task("sales_history_enquiry"), _state(message="show me my purchase history"))
    assert result.status == "completed"
    assert "INV-9" in result.customer_message and "most recent invoice" in result.customer_message


def test_document_request_is_acknowledged_not_fabricated():
    result = sa1.run(
        _task("document_request", entities={"voucher_numbers": ["URD/NE/327"]}), _state()
    )
    assert "URD/NE/327" in result.customer_message
    assert result.tool_calls == []


def test_unknown_intent_gets_a_helpful_prompt():
    result = sa1.run(_task("unknown"), _state())
    assert "balance" in result.customer_message
    assert result.status == "completed"


def test_two_read_intents_combine_into_one_reply(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    monkeypatch.setattr(
        c3, "get_sales_history",
        lambda cid, limit=5: [{"voucher_number": "URD/NE/500", "date": date(2026, 6, 1), "amount": 12000.0}],
    )
    result = sa1.run(_task("outstanding_enquiry", "sales_history_enquiry"), _state())
    assert "200,000.00" in result.customer_message
    assert "URD/NE/500" in result.customer_message
    assert {call.tool for call in result.tool_calls} == {"get_outstanding", "get_sales_history"}


# --------------------------------------------------------------------------
# Through the orchestrator — SA-1 is now the live sa1_general runner
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Optional LLM phrasing — verified against the template, never trusted blindly
# --------------------------------------------------------------------------


def test_grounded_rewrite_is_used(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    warm = "You currently owe ₹200,000.00 on 1 open bill, URD/NE/327 from 01 Jan 2026 (90+ days)."
    monkeypatch.setattr(sa1, "_llm_phrase", lambda template: warm)
    result = sa1.run(_task("outstanding_enquiry"), _state())
    assert result.customer_message == warm


def test_rewrite_that_invents_a_figure_is_rejected(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    # Adds ₹9,99,999 that no tool returned — must fall back to the template.
    monkeypatch.setattr(sa1, "_llm_phrase", lambda template: template + " Also you owe ₹999,999.00 more.")
    result = sa1.run(_task("outstanding_enquiry"), _state())
    assert "999,999.00" not in result.customer_message
    assert _figures(result.customer_message) <= {200000.0}


def test_rewrite_that_alters_a_voucher_is_rejected(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    monkeypatch.setattr(
        sa1, "_llm_phrase",
        lambda template: "You owe ₹200,000.00 on bill ABC/XY/327.",
    )
    result = sa1.run(_task("outstanding_enquiry"), _state())
    assert "ABC/XY/327" not in result.customer_message
    assert "URD/NE/327" in result.customer_message


def test_grounded_allows_dropping_detail_but_not_adding_it():
    template = "Outstanding ₹200,000.00 across 1 bill URD/NE/327."
    assert sa1._grounded(template, "You owe ₹2,00,000 on URD/NE/327.")  # Indian grouping, same value
    assert sa1._grounded(template, "You have one open bill.")           # dropped figures — fine
    assert not sa1._grounded(template, "You owe ₹200,000.00 and ₹5,000.00.")  # new figure
    assert not sa1._grounded(template, "See bill XYZ/AA/999.")          # new voucher
    assert not sa1._grounded(template, "")                              # empty


# --------------------------------------------------------------------------
# Tool-selection fallback — LLM picks a vetted read, answer grounded in it
# --------------------------------------------------------------------------


def test_tool_fallback_answers_an_unhandled_question(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    monkeypatch.setattr(sa1, "_plan_tools", lambda message: ["outstanding"])
    answer = "You currently owe ₹200,000.00 across 1 open bill."
    monkeypatch.setattr(sa1, "_compose_answer", lambda message, data: answer)
    result = sa1.run(_task("unknown"), _state(message="give me a quick summary of my account"))
    assert result.customer_message == answer
    assert any(c.tool == "get_outstanding" for c in result.tool_calls)


def test_tool_fallback_rejects_a_hallucinated_answer(monkeypatch):
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    monkeypatch.setattr(sa1, "_plan_tools", lambda message: ["outstanding"])
    monkeypatch.setattr(sa1, "_compose_answer", lambda message, data: "You owe ₹50,000,000.00.")
    result = sa1.run(_task("unknown"), _state(message="summary please"))
    assert "50,000,000" not in (result.customer_message or "")
    assert result.customer_message == sa1._HELP  # grounding rejected it -> help


def test_tool_fallback_absent_falls_back_to_help(monkeypatch):
    monkeypatch.setattr(sa1, "_plan_tools", lambda message: None)
    result = sa1.run(_task("unknown"), _state(message="anything"))
    assert result.customer_message == sa1._HELP


def test_tool_results_are_json_serialisable():
    blob = sa1._to_jsonable(OUTSTANDING)
    json.dumps(blob)  # must not raise
    assert blob["outstanding"] == 200000.0


def test_orchestrator_routes_outstanding_to_the_real_sa1(monkeypatch):
    monkeypatch.setattr(c3, "build_customer_360", lambda cid, **kw: None)
    monkeypatch.setattr(c3, "get_outstanding", lambda cid: OUTSTANDING)
    state = orc.handle("How much do I owe?", customer_id=CID)
    summary = orc.summarize(state)
    assert summary["agents"] == ["sa1_general"]
    assert summary["statuses"] == ["completed"]
    assert "200,000.00" in state.final_response
