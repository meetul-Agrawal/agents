"""Phase 6 — SA-3, the dispute agent.

Understands a customer's dispute, gathers evidence from the records that
actually exist for them, opens a case, and tells the customer what was found —
never who is right. Determining fault is a human call; SA-3's job stops at
"here is what our records show."

Evidence is gathered only from the same read tools SA-1 already uses
(`get_sales_history`, `get_receipts`, `get_outstanding`) — no raw voucher scan,
same domain-tool boundary every other agent respects. Two cases:

* The message names a specific invoice (`entities["voucher_numbers"]`, already
  extracted and verified upstream). SA-3 looks each one up in this customer's
  own sales and receipts and reports what it finds — including the useful
  negative: an invoice number that does not exist on this account at all, which
  is itself evidence for a "wrong bill" or duplicate-invoice claim.
* No invoice is named. SA-3 records the current outstanding position as
  context — there is nothing more specific to check.

This book has no credit-note or order vouchers (`customer360.capabilities()`),
so evidence about a return being "not yet reflected" the way the vision doc's
example describes cannot be checked here — SA-3 states what the records show
and nothing it cannot verify.

A case is resolved by a human (`services.resolve_case`, driven from the ops UI
— see `scripts/ui_server.py`), never by this agent. `resolution_message` builds
the customer-facing follow-up for that decision; it is templated and grounded
the same way every other reply here is, and it is the caller's job to actually
deliver it (`services.send_customer_message`) once a conversation is known.
"""

from __future__ import annotations

from typing import Any

from . import customer360 as c3
from . import services
from .contracts import AgentResult, AgentTask, Case, CustomerAssistState, ProposedAction, ToolCall
from .sa1_general import _fmt_date, _inr, _phrase, _read


def _find(rows: list[dict[str, Any]], voucher_number: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("voucher_number") == voucher_number:
            return row
    return None


def _voucher_evidence(cid: str, voucher_numbers: list[str], calls: list[ToolCall]) -> list[dict[str, Any]]:
    """One evidence entry per cited voucher: what our own records show for it,
    or the fact that it does not appear on this account at all."""
    sales = _read(calls, "get_sales_history", lambda: c3.get_sales_history(cid), customer_id=cid) or []
    receipts = _read(calls, "get_receipts", lambda: c3.get_receipts(cid), customer_id=cid) or []

    evidence: list[dict[str, Any]] = []
    for number in voucher_numbers:
        sale = _find(sales, number)
        receipt = _find(receipts, number)
        if sale:
            evidence.append({
                "type": "invoice_on_record", "voucher_number": number,
                "amount": sale.get("amount"), "date": str(sale.get("date") or ""),
            })
        if receipt:
            evidence.append({
                "type": "receipt_on_record", "voucher_number": number,
                "amount": receipt.get("amount"), "date": str(receipt.get("date") or ""),
                "against_bills": receipt.get("against_bills"),
            })
        if not sale and not receipt:
            evidence.append({"type": "voucher_not_found", "voucher_number": number})
    return evidence


def _outstanding_evidence(cid: str, calls: list[ToolCall]) -> list[dict[str, Any]]:
    o = _read(calls, "get_outstanding", lambda: c3.get_outstanding(cid), customer_id=cid)
    if o is None:
        return []
    return [{
        "type": "outstanding_snapshot", "outstanding": o.outstanding,
        "open_bill_count": o.open_bill_count,
    }]


def _summarize(evidence: list[dict[str, Any]]) -> str:
    """Plain-language, grounded restatement of the evidence list — every figure
    and reference here already exists in `evidence`."""
    lines: list[str] = []
    for item in evidence:
        kind = item["type"]
        if kind == "invoice_on_record":
            lines.append(
                f"Invoice {item['voucher_number']} is on your account, dated "
                f"{_fmt_date(item['date']) if item['date'] else 'unknown'}, for {_inr(item['amount'] or 0)}."
            )
        elif kind == "receipt_on_record":
            lines.append(
                f"A receipt against {item['voucher_number']} is on record for {_inr(item['amount'] or 0)}."
            )
        elif kind == "voucher_not_found":
            lines.append(f"We could not find {item['voucher_number']} on your account at all.")
        elif kind == "outstanding_snapshot":
            lines.append(
                f"Your current outstanding is {_inr(item['outstanding'])} across "
                f"{item['open_bill_count']} invoice(s)."
            )
    return " ".join(lines)


def _intents_of(task: AgentTask) -> list[str]:
    named = task.inputs.get("intents")
    if isinstance(named, list) and named:
        return named
    return [part for part in task.action.split("+") if part]


def run(task: AgentTask, state: CustomerAssistState) -> AgentResult:
    intents = _intents_of(task)
    entities = task.inputs.get("entities") or state.entities or {}
    message_id = state.entities.get("message_id")

    def result(status: str, message: str | None, calls: list[ToolCall],
               case_id: str | None = None, actions: list[ProposedAction] | None = None) -> AgentResult:
        return AgentResult(
            agent="sa3_dispute", agent_task_id=task.agent_task_id, status=status,
            summary=f"opened case {case_id}" if case_id else "no dispute case opened",
            customer_message=message, tool_calls=calls, actions=actions or [],
        )

    if "dispute" not in intents or not state.customer_id:
        return result("needs_information", None, [])

    cid = state.customer_id
    calls: list[ToolCall] = []
    voucher_numbers = entities.get("voucher_numbers") or []

    evidence = _voucher_evidence(cid, voucher_numbers, calls) if voucher_numbers else \
        _outstanding_evidence(cid, calls)

    priority = "high" if state.urgency == "high" else "normal"
    title = f"Dispute: {', '.join(voucher_numbers) if voucher_numbers else 'account query'}"
    case, created = services.create_case(
        cid, title, priority=priority, evidence=evidence,
        conversation_id=state.conversation_id, message_id=message_id,
    )
    calls.append(ToolCall(tool="create_dispute", arguments={"case_id": case.case_id}))
    actions = [ProposedAction(
        type="create_dispute", mode="auto", executed=True,
        payload={"case_id": case.case_id, "priority": priority},
    )]
    if created:
        services.record_event(
            cid, "DISPUTE_CREATED", "sa3_dispute", conversation_id=state.conversation_id,
            message_id=message_id, payload={"case_id": case.case_id},
        )
        calls.append(ToolCall(tool="create_event"))

    summary = _summarize(evidence)
    unfound = [e["voucher_number"] for e in evidence if e["type"] == "voucher_not_found"]
    clarify = " Could you double-check the invoice number?" if unfound else ""

    message = (
        f"Thank you for flagging this — we've opened case {case.case_id} to look into it. "
        f"{summary}{clarify} A colleague will review the details and get back to you."
    )
    return result(
        "needs_information" if unfound else "completed", _phrase(message), calls, case.case_id, actions,
    )


def resolution_message(case: Case, outcome: str, note: str = "") -> str:
    """The follow-up sent once a human resolves a case. Templated and grounded
    like every other reply here — never a fresh, ungrounded LLM composition."""
    if outcome == "solved":
        base = (
            f"Update on your case {case.case_id} ({case.title}): this has been resolved."
        )
    else:
        base = (
            f"Update on your case {case.case_id} ({case.title}): after review, we found no "
            "further action is needed."
        )
    if note:
        base += f" {note}"
    return _phrase(base)
