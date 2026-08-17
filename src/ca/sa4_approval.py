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

from typing import Any

from . import customer360 as c3
from . import services
from .contracts import (
    AgentResult,
    AgentTask,
    Approval,
    CustomerAssistState,
    ProposedAction,
    ToolCall,
    APPROVAL_TYPES,
)
from .sa1_general import _inr, _phrase, _read, compose_grounded


def _approval_type(intents: list[str], entities: dict[str, Any]) -> str:
    """The model classifies which of the six categories this is (Request.
    approval_type, extracted in orchestrator.entities_from and validated there
    against APPROVAL_TYPES). credit_note_request has exactly one matching
    category, so that mapping is a direct fact, not a guess. `settlement` is
    the safe default when the model didn't name one — the most generic of the
    six, never a wrong specific category."""
    claimed = entities.get("approval_type")
    if claimed in APPROVAL_TYPES:
        return claimed
    if "credit_note_request" in intents:
        return "large_credit_note"
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
    """A grounded restatement of the facts already read from tools — for the
    human reviewer, not the customer. Never a verdict on whether to approve."""
    facts = dict(context)
    if "outstanding" in facts:
        facts["outstanding"] = _inr(facts["outstanding"])
    composed = compose_grounded(
        "Summarize these account facts in one or two short sentences for a "
        "human reviewer deciding an approval request. State only the facts "
        "given — no recommendation, no decision.",
        facts,
    )
    if composed:
        return composed
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
    approval_type = _approval_type(intents, entities)
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

    facts: dict[str, Any] = {"approval_type": approval_type, "reference": approval.approval_id}
    if amount is not None:
        facts["amount"] = _inr(amount)
    composed = compose_grounded(
        "Write a short reply telling the customer we've logged their request "
        "and it needs review before it can be approved.",
        facts,
    )
    amount_text = f" of {_inr(amount)}" if amount is not None else ""
    message = composed or _phrase(
        f"Thank you — we've logged your request{amount_text} (reference {approval.approval_id}). "
        "This needs review by our team before it can be approved; we'll come back "
        "to you with a decision."
    )
    return result("needs_approval", message, calls, actions)


def decision_message(approval: Approval, approved: bool, note: str = "") -> str:
    """The follow-up sent once a human decides.

    The verdict sentence is fixed in code, driven by the `approved` bool that
    already came from `services.decide_approval` — never phrased by the
    model. See `compose_grounded`'s docstring: this model measurably states
    the opposite decision inside an otherwise-correct reply, so the one fact
    that must never be wrong is not entrusted to free text. Any `note` is
    elaborated by the model and grounding-checked; it is shown no outcome
    fact, so it has nothing to contradict."""
    amount_text = f" of {_inr(approval.amount)}" if approval.amount is not None else ""
    if approved:
        anchor = f"Good news — your request{amount_text} (reference {approval.approval_id}) has been approved."
    else:
        anchor = (
            f"We've reviewed your request{amount_text} (reference {approval.approval_id}) "
            "and are unable to approve it at this time."
        )
    if not note:
        return anchor
    extra = compose_grounded(
        "Write one short, warm closing sentence for a customer message, "
        "incorporating this note. Do not mention approval, rejection, or any "
        "decision — that has already been said elsewhere in the message.",
        {"note": note},
    )
    return f"{anchor} {extra}" if extra else f"{anchor} {note}"
