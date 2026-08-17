"""Phase 6 — SA-4, the approval agent.

Gathers context and raises a pending approval request. It never decides the
outcome — that is a human's call, and the one thing this whole module protects
is that nothing here can make that decision for them.

Two things keep that true structurally, not just by convention:

* `services.create_approval` always writes `status="pending"`. There is no
  parameter that lets a caller set it to anything else, and the only function
  that can (`services.decide_approval`) is never called from this file.
* `create_approval` is an auto-mode tool in `registry.py` — raising the request
  is safe by design — while `update_approval` (recording a decision) is
  human_approval-mode and appears nowhere in this module's tool calls. The
  orchestrator's `execute()` also neutralises any human_approval-mode action
  this agent might try to mark executed, as defense in depth.

The recommendation attached to the approval is a grounded restatement of
figures already read from the tools — outstanding, settlement speed, and how
many approvals this customer has had before — never a verdict on whether to
approve it.
"""

from __future__ import annotations

import re
from typing import Any

from . import customer360 as c3
from . import services
from .contracts import AgentResult, AgentTask, Approval, CustomerAssistState, ProposedAction, ToolCall
from .sa1_general import _inr, _phrase, _read

# The same domain vocabulary INTENT_RULES already uses to detect these asks in
# the first place — refining *which kind* of approval this is, not inventing
# new signal.
_CREDIT_LIMIT = re.compile(r"\bcredit\s+limit\b", re.I)
_WRITE_OFF = re.compile(r"\b(write\s*off|waive|waiver)\b", re.I)
_TERMS = re.compile(r"\b(payment\s+terms|credit\s+terms|\d+\s*day\s+(credit|terms))\b", re.I)
_DISCOUNT = re.compile(r"\bspecial\s+(price|rate|discount)\b", re.I)

_LABELS = {
    "large_credit_note": "a credit note",
    "credit_limit": "a credit limit change",
    "write_off": "a waiver / write-off",
    "exceptional_terms": "revised payment terms",
    "special_discount": "a special price or discount",
    "settlement": "a settlement",
}


def _approval_type(intents: list[str], message: str) -> str:
    if "credit_note_request" in intents:
        return "large_credit_note"
    if _CREDIT_LIMIT.search(message or ""):
        return "credit_limit"
    if _WRITE_OFF.search(message or ""):
        return "write_off"
    if _TERMS.search(message or ""):
        return "exceptional_terms"
    if _DISCOUNT.search(message or ""):
        return "special_discount"
    return "settlement"


def _gather_context(cid: str, calls: list[ToolCall]) -> dict[str, Any]:
    """Every value here is read from a tool. `recommendation` below states
    these facts and nothing else — no verdict, no invented figure."""
    o = _read(calls, "get_outstanding", lambda: c3.get_outstanding(cid), customer_id=cid)
    b = _read(calls, "get_payment_history", lambda: c3.get_payment_history(cid), customer_id=cid)
    prior = _read(calls, "get_approvals", lambda: c3.get_approvals(cid), customer_id=cid) or []

    context: dict[str, Any] = {}
    if o is not None:
        context["outstanding"] = o.outstanding
        context["open_bill_count"] = o.open_bill_count
    if b is not None:
        context["receipt_count"] = b.receipt_count
        context["avg_days_to_settle"] = b.avg_days_to_settle
    context["prior_approvals"] = len(prior)
    context["prior_approvals_granted"] = sum(1 for p in prior if p.get("status") == "approved")
    return context


def _recommendation(context: dict[str, Any]) -> str:
    parts: list[str] = []
    if "outstanding" in context:
        parts.append(f"Outstanding: {_inr(context['outstanding'])} across {context['open_bill_count']} invoice(s).")
    if context.get("avg_days_to_settle") is not None:
        parts.append(
            f"Average settlement time: {context['avg_days_to_settle']:.0f} days over "
            f"{context['receipt_count']} receipt(s)."
        )
    parts.append(
        f"Prior approvals on record: {context['prior_approvals']} "
        f"({context['prior_approvals_granted']} granted)."
    )
    return " ".join(parts)


def _amount(entities: dict[str, Any]) -> float | None:
    amounts = entities.get("amounts") or []
    return float(amounts[0]) if amounts else None


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
               actions: list[ProposedAction] | None = None) -> AgentResult:
        return AgentResult(
            agent="sa4_approval", agent_task_id=task.agent_task_id, status=status,
            customer_message=message, tool_calls=calls, actions=actions or [],
            summary=f"handled {'+'.join(intents) or 'approval request'}",
        )

    relevant = {"settlement_request", "credit_note_request"} & set(intents)
    if not relevant or not state.customer_id:
        return result("needs_information", None, [])

    cid = state.customer_id
    calls: list[ToolCall] = []
    approval_type = _approval_type(intents, state.message)
    context = _gather_context(cid, calls)
    recommendation = _recommendation(context)
    amount = _amount(entities)

    approval, created = services.create_approval(
        cid, approval_type, "sa4_approval", amount=amount, context=context,
        recommendation=recommendation, conversation_id=state.conversation_id,
        message_id=message_id,
    )
    calls.append(ToolCall(tool="create_approval", arguments={"approval_id": approval.approval_id}))
    # "auto" mode: raising the request is safe by design (registry.py). The
    # approval's own status stays "pending" — nothing here executes the thing
    # being requested.
    actions = [ProposedAction(
        type="create_approval", mode="auto", executed=True,
        payload={"approval_id": approval.approval_id, "type": approval_type},
    )]
    if created:
        services.record_event(
            cid, "APPROVAL_CREATED", "sa4_approval", conversation_id=state.conversation_id,
            message_id=message_id, payload={"approval_id": approval.approval_id, "type": approval_type},
        )
        calls.append(ToolCall(tool="create_event"))

    label = _LABELS[approval_type]
    amount_text = f" of {_inr(amount)}" if amount is not None else ""
    message = (
        f"Thank you — we've logged your request for {label}{amount_text} (reference "
        f"{approval.approval_id}). This needs review by our team before it can be approved; "
        "we'll come back to you with a decision."
    )
    return result("needs_approval", _phrase(message), calls, actions)


def decision_message(approval: Approval, approved: bool, note: str = "") -> str:
    """The follow-up sent once a human decides. Templated and grounded like
    every other reply here — the decision itself came from `decide_approval`,
    never from this function or from an agent."""
    label = _LABELS.get(approval.type, approval.type)
    amount_text = f" of {_inr(approval.amount)}" if approval.amount is not None else ""
    if approved:
        base = (
            f"Good news — your request for {label}{amount_text} (reference "
            f"{approval.approval_id}) has been approved."
        )
    else:
        base = (
            f"We've reviewed your request for {label}{amount_text} (reference "
            f"{approval.approval_id}) and are unable to approve it at this time."
        )
    if note:
        base += f" {note}"
    return _phrase(base)
