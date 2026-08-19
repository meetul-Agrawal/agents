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

`_summarize` drafts a one-line summary for the reviewer's list view; `_verify`
and `MAX_VERIFY_ATTEMPTS` (below) exist here to be reused, not called from
this module. SA-9 (`sa9_verifier.py`) runs as this agent's dependent — the
orchestrator schedules it right after this task and hands it this run's own
`AgentResult` — and does the actual self-check + redraft loop against the
approval this function just raised. That keeps the split honest: this module
still only ever raises a `pending` request and never decides; SA-9 only ever
improves what the human reviewer sees, never gates whether they see it.
"""

from __future__ import annotations

import json
from typing import Any

from . import customer360 as c3
from . import services
from .contracts import (
    AgentResult,
    AgentTask,
    Approval,
    CustomerAssistState,
    ModelOutput,
    ProposedAction,
    ToolCall,
    APPROVAL_TYPES,
    utcnow,
)
from .sa1_general import _inr, _fmt_date, _phrase, _read, compose_grounded, resolve_date_fields


def _approval_type(intents: list[str], entities: dict[str, Any]) -> str:
    """The model classifies which of the six categories this is (Request.
    approval_type, extracted in orchestrator.entities_from and validated there
    against APPROVAL_TYPES). credit_note_request has exactly one matching
    category, so that mapping is a direct fact, not a guess — checked first,
    ahead of the free-form `claimed` entity, because the entity extractor has
    been observed to mislabel an explicit "credit note" ask as write_off or
    special_discount (evals/reports/dispute_approval_scenarios.md, Finding 3)
    even when the intent classifier correctly tagged credit_note_request.
    `settlement` is the safe default when neither says anything more specific
    — the most generic of the six, never a wrong specific category.

    call_schedule_request is its own intent with its own single category —
    same direct-fact shape as credit_note_request, checked first."""
    if "call_schedule_request" in intents:
        return "call_schedule"
    if "credit_note_request" in intents:
        return "large_credit_note"
    claimed = entities.get("approval_type")
    if claimed in APPROVAL_TYPES:
        return claimed
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


def _summarize(approval_type: str, amount: float | None, feedback: str = "") -> str:
    """One glance-length sentence for a human reviewer's list view — distinct
    from `_recommendation`'s multi-fact account context. `feedback` is the
    prior verify attempt's objection, fed back in as redraft *guidance* — in
    the instruction text, not `facts` — so a retry addresses it without
    narrating the retry itself. Putting it in `facts` (as tried before) let
    the model turn an internal QA note into an invented customer-facing claim
    like "due to previous rejection of..." — nothing in the conversation ever
    said that (evals/reports/dispute_approval_scenarios.md, Finding 2, S8)."""
    facts: dict[str, Any] = {"approval_type": approval_type}
    if amount is not None:
        facts["amount"] = _inr(amount)
    instruction = (
        "Write ONE short sentence summarizing this approval request, for a "
        "human reviewer glancing at a list of pending requests. State only "
        "the facts given."
    )
    if feedback:
        instruction += (
            f" A prior draft of this same sentence was rejected for this reason: "
            f"{feedback!r} — write a new sentence that fixes it, but never mention "
            "a rejection, a retry, or any prior attempt; state only the approval "
            "request's own facts."
        )
    composed = compose_grounded(instruction, facts)
    if composed:
        return composed
    amount_text = f" for {_inr(amount)}" if amount is not None else ""
    return f"{approval_type.replace('_', ' ').title()} request{amount_text}."


class _VerifyResult(ModelOutput):
    ok: bool = True
    feedback: str = ""


_VERIFY_SYSTEM = (
    "You check a drafted approval request against the customer message it is "
    "based on. Does the request's type, amount and summary actually reflect "
    "what the customer asked for? If yes, set ok=true. If something is off — "
    "wrong type, wrong amount, a summary that misstates the ask — set ok=false "
    "and say what's wrong in 'feedback' (one short sentence) so it can be "
    "redrafted. Return only the fields asked for."
)

MAX_VERIFY_ATTEMPTS = 3


def _verify(message: str, approval_type: str, amount: float | None, summary: str) -> tuple[bool, str]:
    """Self-check: does this request match what the customer actually asked
    for? Never gates whether a human sees it — `run()` still always raises a
    `pending` approval either way (see module docstring); this only decides
    whether the draft gets one more redraft first. No provider configured ->
    nothing to check against, so don't spend retries guessing: treat as
    verified and let the human reviewer judge it directly."""
    import os

    from . import llm

    if os.getenv("CA_PHRASE", "on").lower() == "off" or not llm.available():
        return True, ""
    facts: dict[str, Any] = {"customer_message": message, "approval_type": approval_type, "summary": summary}
    if amount is not None:
        facts["amount"] = _inr(amount)
    try:
        out = llm.complete_structured(
            _VerifyResult, _VERIFY_SYSTEM, json.dumps(facts, default=str),
            capability="summarization", example={"ok": True, "feedback": ""},
        )
    except llm.LLMUnavailable:
        return True, ""
    return out.ok, out.feedback


def _amount(entities: dict[str, Any]) -> float | None:
    amounts = entities.get("amounts") or []
    return float(amounts[0]) if amounts else None


# --------------------------------------------------------------------------
# call_schedule_request — date, time and reason, one structured call.
#
# Same shape as sa2_recovery's due-date extraction: the customer's phrasing is
# open-ended, so the model reads it, but only into structured fields
# (day count / weekday / day-month-year) that `resolve_date_fields` (shared
# with SA-2) turns into a real date — a hallucinated date has nowhere to
# enter. `time_text` and `reason` carry no such risk (nothing downstream does
# arithmetic on them, and a human reviewer reads them before anyone acts), so
# they are taken as the model's own paraphrase, same trust level as SA-3's
# `issue_label` (contracts.Request docstring).
#
# The model is handed the recent conversation history in the same call, so it
# can read a confirmation ("yes", "haan") against a reason it — or a colleague
# — already asked the customer to confirm, rather than that needing a second
# round trip.
# --------------------------------------------------------------------------


class _CallScheduleExtract(ModelOutput):
    relative_days: int | None = None
    weekday: str | None = None
    end_of_month: bool = False
    day: int | None = None
    month: int | None = None
    year: int | None = None
    time_text: str | None = None
    reason: str | None = None


_CALL_SCHEDULE_SYSTEM = (
    "Extract the details of a customer's request to schedule a phone call with "
    "our sales/accounts team, from their current message and (if given) the "
    "recent conversation history. Return ONLY the fields that apply; leave the "
    "rest null/false.\n"
    "- relative_days: an offset in days from today. 'today'=0, 'tomorrow'=1, "
    "'day after tomorrow'=2, 'in N days'/'after N days'/'within N days'=N, "
    "'next week'=7. A COUNT of days, never a day-of-month.\n"
    "- weekday: one of monday..sunday, if a weekday is named. Always the NEXT "
    "such day, never today.\n"
    "- end_of_month: true only for 'end of month' / 'month-end'.\n"
    "- day: an explicit day-of-month ('the 26th', 'on 20', '20 August' -> day=20).\n"
    "- month: 1-12, only if a month name or a slash/ISO date names one.\n"
    "- year: only if a 4-digit year is explicitly stated.\n"
    "- time_text: the customer's own phrasing of a preferred time of day "
    "('3pm', 'after lunch', 'morning'), copied verbatim. Null if no time is "
    "stated anywhere.\n"
    "- reason: a short phrase for why they want the call. Prefer their own "
    "stated reason in the current message. If the current message gives no "
    "reason but the conversation history shows we already asked the customer "
    "to confirm a specific reason (e.g. an open dispute or a pending order) "
    "and the current message agrees ('yes', 'haan', 'correct', 'right'), set "
    "reason to that confirmed topic. Never invent a reason nobody stated or "
    "agreed to.\n"
    "Never guess a field that isn't stated in the text."
)


def _extract_call_schedule(text: str, history: str) -> _CallScheduleExtract | None:
    import os

    from . import llm

    if os.getenv("CA_PHRASE", "on").lower() == "off" or not llm.available():
        return None
    payload: dict[str, Any] = {"customer_message": text}
    if history:
        payload["recent_conversation_history"] = history
    try:
        return llm.complete_structured(
            _CallScheduleExtract, _CALL_SCHEDULE_SYSTEM, json.dumps(payload),
            capability="summarization",
            example={"relative_days": 2, "weekday": None, "end_of_month": False,
                     "day": None, "month": None, "year": None,
                     "time_text": "3pm", "reason": "discuss the pending dispute"},
        )
    except llm.LLMUnavailable:
        return None


def _history_text(state: CustomerAssistState) -> str:
    lines: list[str] = []
    for m in state.conversation_context[-20:]:
        text = (m.text or "").strip()
        if not text:
            continue
        speaker = "Customer" if m.direction == "inbound" else "Assistant"
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


def _run_call_schedule(task: AgentTask, state: CustomerAssistState, message_id: str | None) -> AgentResult:
    cid = state.customer_id
    calls: list[ToolCall] = []

    def result(status: str, message: str | None, actions: list[ProposedAction] | None = None) -> AgentResult:
        return AgentResult(
            agent="sa4_approval", agent_task_id=task.agent_task_id, status=status,
            customer_message=message, tool_calls=calls, actions=actions or [],
            summary="handled call_schedule_request",
        )

    extract = _extract_call_schedule(state.message, _history_text(state))
    call_date = resolve_date_fields(
        relative_days=extract.relative_days if extract else None,
        weekday=extract.weekday if extract else None,
        end_of_month=extract.end_of_month if extract else False,
        day=extract.day if extract else None,
        month=extract.month if extract else None,
        year=extract.year if extract else None,
        today=utcnow().date(),
    ) if extract else None
    time_text = (extract.time_text or "").strip() if extract else ""
    reason = (extract.reason or "").strip() if extract else ""

    missing = []
    if call_date is None:
        missing.append("a preferred date")
    if not time_text:
        missing.append("a preferred time")
    if not reason:
        missing.append("the reason for the call")

    if missing:
        facts: dict[str, Any] = {"missing": missing}
        have = {k: v for k, v in (("date", _fmt_date(call_date) if call_date else None),
                                   ("time", time_text or None), ("reason", reason or None)) if v}
        if have:
            facts["already_have"] = have
        composed = compose_grounded(
            "Write a short reply asking the customer for the missing details "
            "needed to schedule their call with our sales team. Ask only for "
            "what is listed as missing; do not ask again for anything already given.",
            facts,
        )
        ask = composed or _phrase(f"Sure — to schedule your call, could you share {', '.join(missing)}?")
        return result("needs_information", ask)

    context = {"scheduled_date": call_date.isoformat(), "preferred_time": time_text, "reason": reason}
    summary_facts = {"date": _fmt_date(call_date), "time": time_text, "reason": reason}
    summary = compose_grounded(
        "Write ONE short sentence summarizing this call-scheduling request, for "
        "a human reviewer glancing at a list of pending requests. State only "
        "the facts given.",
        summary_facts,
    ) or f"Call requested for {_fmt_date(call_date)} ({time_text}) — {reason}."

    approval, created = services.create_approval(
        cid, "call_schedule", "sa4_approval", context=context,
        recommendation=f"Customer-requested call: {summary_facts['date']}, {time_text}. Reason: {reason}.",
        summary=summary, conversation_id=state.conversation_id, message_id=message_id,
    )
    calls.append(ToolCall(tool="create_approval", arguments={"approval_id": approval.approval_id}))
    actions = [ProposedAction(
        type="create_approval", mode="auto", executed=True,
        payload={"approval_id": approval.approval_id, "type": "call_schedule"},
    )]
    if created:
        services.record_event(
            cid, "SALES_CALL_CREATED", "sa4_approval", conversation_id=state.conversation_id,
            message_id=message_id, payload={"approval_id": approval.approval_id, **context},
        )
        calls.append(ToolCall(tool="create_event"))

    composed_reply = compose_grounded(
        "Write a short reply telling the customer we've logged their call "
        "request and someone from the team will confirm it.",
        summary_facts,
    )
    message = composed_reply or _phrase(
        f"Thank you — we've logged your request for a call on {summary_facts['date']} around "
        f"{time_text} regarding {reason} (reference {approval.approval_id}). Someone from our "
        "team will confirm the exact time with you shortly."
    )
    return result("needs_approval", message, actions)


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

    relevant = {"settlement_request", "credit_note_request", "call_schedule_request"} & set(intents)
    if not relevant or not state.customer_id:
        return result("needs_information", None, [])

    # call_schedule_request is a distinct, multi-turn gather-then-raise flow
    # (date/time/reason may take a turn or two to complete) — handled on its
    # own rather than folded into the immediate-raise path below. As with
    # settlement/credit_note today, a message combining this with one of them
    # is handled as call_schedule_request alone this turn; only one approval
    # is ever raised per turn regardless of how many of these intents fire.
    if "call_schedule_request" in intents:
        return _run_call_schedule(task, state, message_id)

    cid = state.customer_id
    calls: list[ToolCall] = []
    approval_type = _approval_type(intents, entities)
    context = _gather_context(cid, calls)
    recommendation = _recommendation(context)
    amount = _amount(entities)
    summary = _summarize(approval_type, amount)

    approval, created = services.create_approval(
        cid, approval_type, "sa4_approval", amount=amount, context=context,
        recommendation=recommendation, summary=summary, conversation_id=state.conversation_id,
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
        "Write a short reply telling the customer we've logged their request"
        + (" of the amount specified in facts" if amount is not None else "")
        + " and it needs review before it can be approved.",
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
    that must never be wrong is not entrusted to free text.

    `note` is ops' own words for the customer — appended verbatim, not run
    through the model. Measured `compose_grounded` silently replacing a
    substantive note ("more than 30 days, can't refund") with vague filler
    ("we appreciate your patience") — `_grounded` only blocks a new *number*,
    it does not check the note's content survived, so a paraphrase step here
    has a real chance of deleting the actual reason ops wrote."""
    if approval.type == "call_schedule":
        ctx = approval.context or {}
        when = ", ".join(v for v in (ctx.get("scheduled_date"), ctx.get("preferred_time")) if v)
        if approved:
            anchor = f"Confirmed — our team will call you{f' on {when}' if when else ''} (reference {approval.approval_id})."
        else:
            anchor = (
                f"We're unable to schedule a call{f' on {when}' if when else ''} (reference "
                f"{approval.approval_id}) at this time."
            )
        return f"{anchor} {note}" if note else anchor

    amount_text = f" of {_inr(approval.amount)}" if approval.amount is not None else ""
    if approved:
        anchor = f"Good news — your request{amount_text} (reference {approval.approval_id}) has been approved."
    else:
        anchor = (
            f"We've reviewed your request{amount_text} (reference {approval.approval_id}) "
            "and are unable to approve it at this time."
        )
    return f"{anchor} {note}" if note else anchor
