"""Phase 6 — SA-3, the dispute agent.

Understands a customer's dispute, gathers evidence from the records that
actually exist for them, opens a case, and tells the customer what was found —
never who is right. Determining fault is a human call; SA-3's job stops at
"here is what our records show."

Evidence is gathered only from the same read tools SA-1 already uses
(`get_sales_history`, `get_receipts`, `get_outstanding`) — no raw voucher scan,
same domain-tool boundary every other agent respects.

A case is only opened once there is something concrete to attach to it:

* The message names a specific invoice (`entities["voucher_numbers"]`, already
  extracted and verified upstream). SA-3 looks each one up in this customer's
  own sales and receipts and reports what it finds — including the useful
  negative: an invoice number that does not exist on this account at all, which
  is itself evidence for a "wrong bill" or duplicate-invoice claim. When the
  found invoice has more than one line item and the message does not clearly
  name one, SA-3 asks which item is affected rather than opening a case that
  cannot say what is actually wrong.
* No invoice is named, but the complaint is about the account balance itself
  (the message says so — "balance", "ledger", "outstanding"). The current
  outstanding position is the relevant evidence here, so SA-3 uses it.
* No invoice is named and the complaint is not about the balance (damaged
  goods, wrong item, short supply, an unspecified issue). There is nothing SA-3
  can check yet, so it asks for the invoice number, the item, and what went
  wrong — no case, no evidence dump, no unrelated figure. Dumping the account's
  entire outstanding balance here was the exact failure this replaced: it
  answered a damaged-goods complaint with an unrelated ₹1+ crore balance across
  262 invoices.

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

import re
from typing import Any

from . import customer360 as c3
from . import services
from .contracts import AgentResult, AgentTask, Case, CustomerAssistState, ProposedAction, ToolCall
from .sa1_general import _fmt_date, _inr, _match_product, _phrase, _read

# What the complaint is actually about — used to (a) label the case so a human
# reviewing the queue can see the issue at a glance, and (b) decide whether the
# account balance is relevant evidence at all. Reuses the same vocabulary
# `orchestrator.INTENT_RULES`/`sa4_approval` already treat as domain signal,
# not phrasing invented for any particular test message.
_ISSUE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("damaged or defective goods", re.compile(
        r"\b(damage|damaged|defective|spoil(ed)?|broken|leak(ing)?|expired|faulty|quality)\b", re.I)),
    ("short or missing supply", re.compile(
        r"\b(short\s*(supply|shipped|delivered)?|shortage|missing|not\s+received|"
        r"never\s+received|less\s+(qty|quantity|pieces|units))\b", re.I)),
    ("wrong item supplied", re.compile(r"\b(wrong\s+(item|product|goods)|different\s+item)\b", re.I)),
    ("duplicate billing", re.compile(r"\b(duplicate|billed\s+twice|charged\s+twice)\b", re.I)),
    ("incorrect rate or charge", re.compile(
        r"\b(rate|price|overcharg\w*|excess\s+charg\w*|wrong\s+(bill|invoice|amount|charge))\b", re.I)),
]
# Only this one makes the account balance itself the relevant evidence.
_BALANCE_DISPUTE = re.compile(r"\b(balance|ledger|outstanding|statement|hisab)\b", re.I)


def _issue_kind(message: str) -> str | None:
    for label, pattern in _ISSUE_PATTERNS:
        if pattern.search(message or ""):
            return label
    return "account balance" if _BALANCE_DISPUTE.search(message or "") else None


def _find(rows: list[dict[str, Any]], voucher_number: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("voucher_number") == voucher_number:
            return row
    return None


def _voucher_evidence(
    cid: str, voucher_numbers: list[str], message: str, calls: list[ToolCall]
) -> list[dict[str, Any]]:
    """One evidence entry per cited voucher: what our own records show for it —
    including its line items, so "which stock item" has an answer — or the fact
    that it does not appear on this account at all."""
    sales = _read(calls, "get_sales_history", lambda: c3.get_sales_history(cid), customer_id=cid) or []
    receipts = _read(calls, "get_receipts", lambda: c3.get_receipts(cid), customer_id=cid) or []

    evidence: list[dict[str, Any]] = []
    for number in voucher_numbers:
        sale = _find(sales, number)
        receipt = _find(receipts, number)
        if sale:
            item_names = [it.get("name") for it in (sale.get("items") or []) if it.get("name")]
            matched = _match_product(message, item_names) if len(item_names) > 1 else item_names
            evidence.append({
                "type": "invoice_on_record", "voucher_number": number,
                "amount": sale.get("amount"), "date": str(sale.get("date") or ""),
                "items": item_names, "matched_items": matched,
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


def _summarize(evidence: list[dict[str, Any]]) -> tuple[str, bool]:
    """Plain-language, grounded restatement of the evidence list — every figure
    and reference here already exists in `evidence`. Returns (text, needs_more)
    — `needs_more` is set when a found invoice has more than one item and none
    is clearly the one the customer meant."""
    lines: list[str] = []
    needs_more = False
    for item in evidence:
        kind = item["type"]
        if kind == "invoice_on_record":
            items = item.get("items") or []
            line = (
                f"Invoice {item['voucher_number']} is on your account, dated "
                f"{_fmt_date(item['date']) if item['date'] else 'unknown'}, for {_inr(item['amount'] or 0)}"
            )
            matched = item.get("matched_items") or []
            if len(items) == 1:
                line += f" (item: {items[0]})."
            elif len(items) > 1 and len(matched) == 1:
                line += f". We've noted this as the {matched[0]} on that invoice."
            elif len(items) > 1:
                line += f". This invoice has more than one item ({', '.join(items)})."
                needs_more = True
            else:
                line += "."
            lines.append(line)
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
    return " ".join(lines), needs_more


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
    issue = _issue_kind(state.message)

    # Nothing concrete to check: no invoice cited, and the complaint is not
    # about the balance itself (where the balance IS the relevant evidence).
    # Ask for the specifics instead of opening an empty case or answering with
    # an unrelated figure — this is the fix for the ₹1+ crore balance dump.
    if not voucher_numbers and issue != "account balance":
        ask = (
            "Thanks for letting us know — to look into this, could you share the invoice "
            "number, which item was affected, and a short description of the issue (for "
            "example: damaged, wrong item, or short quantity)? Once we have that we'll open "
            "a case and take a look."
        )
        return result("needs_information", _phrase(ask), calls)

    evidence = _voucher_evidence(cid, voucher_numbers, state.message, calls) if voucher_numbers else \
        _outstanding_evidence(cid, calls)

    priority = "high" if state.urgency == "high" else "normal"
    label = issue or (', '.join(voucher_numbers) if voucher_numbers else 'account query')
    title = f"Dispute — {label}"
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

    summary, ambiguous_item = _summarize(evidence)
    unfound = [e["voucher_number"] for e in evidence if e["type"] == "voucher_not_found"]
    if unfound:
        clarify = " Could you double-check the invoice number?"
    elif ambiguous_item:
        clarify = " Which item on that invoice did you mean?"
    else:
        clarify = ""

    message = (
        f"Thank you for flagging this — we've opened case {case.case_id} to look into it. "
        f"{summary}{clarify} A colleague will review the details and get back to you."
    )
    needs_more = bool(unfound) or ambiguous_item
    return result(
        "needs_information" if needs_more else "completed", _phrase(message), calls, case.case_id, actions,
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
