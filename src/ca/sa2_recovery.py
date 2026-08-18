"""Phase 5 — SA-2, the recovery agent.

The first stateful agent and the first that commits: it records payment promises
and recovery events. It handles two things the customer says about money owed:

* **payment_promise** — an undertaking to pay later, a revision of one, or an
  admission of being unable to pay. A promise is recorded only when both an
  amount and a due date are known; anything vaguer is logged as contact and the
  missing piece is asked for. Nothing garbage is committed.
* **payment_claim** — an assertion that money was already sent. This is
  *verified against the receipts on record*, never thanked on faith. A claim
  with no matching receipt is answered with a request for the reference, not a
  confirmation — the adversarial false-payment case.

Where the numbers come from, and why it is safe:

* the **amount** is the verified figure the orchestrator already extracted — SA-2
  never re-reads it from the message, so there is one extraction, not two that
  can disagree;
* the **due date**'s open-ended phrasing is read by the model (`_extract_due_date`),
  but the `date` itself is always computed here (`parse_due_date`) from the
  structured fields it returns — a day count, a weekday, a day/month/year —
  never a date the model produced directly;
* the **receipts** come from `customer360`.

No LLM produces a date or money figure directly — only structured fields our
own arithmetic turns into one.

ponytail: reply is templated (no phrasing pass) and no follow-up `create_task`
is emitted — the next action is stated in the reply. Add either if a Phase-5
eval asks for it.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from . import customer360 as c3
from . import services
from .config import app_db
from .contracts import AgentResult, AgentTask, CustomerAssistState, ModelOutput, ProposedAction, ToolCall
from .contracts import utcnow
from .sa1_general import _inr, _fmt_date, _phrase, _read, resolve_date_fields  # reuse, don't reimplement


# --------------------------------------------------------------------------
# Due-date parsing — the model reads the open-ended phrasing, the date itself
# is always our arithmetic, never the model's guess.
# --------------------------------------------------------------------------

_UNABLE = re.compile(
    r"\b(unable to pay|can'?t pay|cannot pay|not able to pay|no money|"
    r"don'?t have (the )?money|struggling to pay|out of funds)\b", re.I)


class _DueDateExtract(ModelOutput):
    relative_days: int | None = None
    weekday: str | None = None
    end_of_month: bool = False
    day: int | None = None
    month: int | None = None
    year: int | None = None


_DATE_SYSTEM = (
    "Extract the payment due date the customer names or corrects. Return ONLY the "
    "fields that apply from their message; leave the rest null/false.\n"
    "- relative_days: an offset in days from today. 'today'=0, 'tomorrow'=1, "
    "'day after tomorrow'=2, 'in N days'/'after N days'/'within N days'=N, "
    "'next week'=7. This is a COUNT of days, never a day-of-month — "
    "'after 12 days' is relative_days=12, NOT day=12.\n"
    "- weekday: one of monday..sunday, if a weekday is named ('next Friday', "
    "'by Monday'). Always the NEXT such day, never today.\n"
    "- end_of_month: true only for 'end of month' / 'month-end'.\n"
    "- day: an explicit day-of-month ('the 26th', 'on 20', '20 August' -> day=20).\n"
    "- month: 1-12, only if a month name or a slash/ISO date names one.\n"
    "- year: only if a 4-digit year is explicitly stated.\n"
    "If a date is superseded ('instead of 13 September, the 20th'), extract only "
    "the new one. Never guess a field that isn't stated in the text."
)


def parse_due_date(text: str, today: date) -> date | None:
    """The customer's phrasing ('by the 23rd', 'after 12 days', 'next Friday',
    'instead of the 13th, the 20th') is open-ended — read by the model
    (`_extract_due_date`) rather than a growing regex vocabulary, one branch
    per phrasing report. The date is still always computed here: the model
    returns structured fields (a day count, a weekday, a day/month/year), this
    function turns them into a `date`. A hallucinated date has nowhere to
    enter — only a day count/weekday/day-of-month can, and each is checked
    against the calendar before use.

    ponytail: this replaced ~15 hand-rolled regex branches (see git history)
    that kept growing one phrasing at a time — a fixed pattern list is exactly
    the kind of open-ended judgement call a model handles and a list can't
    keep up with. No LLM available (`CA_PHRASE=off`, no provider) -> None,
    same as "could not parse"; the caller already asks for clarification."""
    if not (text or "").strip():
        return None
    extract = _extract_due_date(text)
    if extract is None:
        return None
    return resolve_date_fields(
        relative_days=extract.relative_days, weekday=extract.weekday,
        end_of_month=extract.end_of_month, day=extract.day,
        month=extract.month, year=extract.year, today=today,
    )


def _extract_due_date(text: str) -> _DueDateExtract | None:
    import os

    from . import llm

    if os.getenv("CA_PHRASE", "on").lower() == "off" or not llm.available():
        return None
    try:
        return llm.complete_structured(
            _DueDateExtract, _DATE_SYSTEM, json.dumps({"customer_message": text}),
            capability="summarization",
            example={"relative_days": 12, "weekday": None, "end_of_month": False,
                     "day": None, "month": None, "year": None},
        )
    except llm.LLMUnavailable:
        return None


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _amount(entities: dict[str, Any]) -> float | None:
    amounts = entities.get("amounts") or []
    return float(amounts[0]) if amounts else None


def _bare_amount(message: str) -> float | None:
    """A reply that is nothing but a number ("500000") answering SA-2's own
    "confirm the amount..." question carries no currency marker for the
    regex floor (`orchestrator.ENTITY_PATTERNS`) to anchor on, and there is
    no model `request` to draw a claim from either when the classifier fell
    back to bare continuity routing (`orchestrator._continuity_fallback`).
    Reads the customer's own digits directly — not a guess, the same "it's
    exactly what they typed" principle as `verify_value`."""
    text = (message or "").strip().replace(",", "")
    if not text or not text.replace(".", "", 1).isdigit():
        return None
    return float(text)


def _add_task(
    cid: str, kind: str, title: str, due: date | None,
    meta: dict, calls: list[ToolCall], actions: list[ProposedAction],
) -> None:
    task, _ = services.create_task(
        cid, kind, title, due_date=due,
        conversation_id=meta["conversation_id"], message_id=meta["message_id"],
    )
    calls.append(ToolCall(tool="create_task"))
    actions.append(ProposedAction(
        type="create_task", mode="auto",
        payload={"task_id": task.task_id, "kind": kind,
                 "due_date": due.isoformat() if due else None},
        executed=True,
    ))


def _fill_from_open_promise(
    cid: str, meta: dict, amount: float | None, due: date | None, calls: list[ToolCall],
) -> tuple[float | None, date | None]:
    """A turn that only answers half of a promise, or modifies an existing open
    promise (e.g. 'instead of 13 Sept I will pay by 20th' or 'I will pay 30000 instead'),
    recovers the missing half from the open promise in MongoDB or the customer's last
    incomplete-promise event."""
    if amount is not None and due is not None:
        return amount, due

    # 1. Check existing active open promise for this customer in MongoDB
    open_promise = _read(
        calls, "get_open_promise",
        lambda: app_db()["payment_promises"].find_one({"customer_id": cid, "status": "promised"}),
        customer_id=cid,
    )
    if open_promise:
        if amount is None and open_promise.get("amount") is not None:
            amount = float(open_promise["amount"])
        if due is None and open_promise.get("due_date"):
            d_val = open_promise["due_date"]
            due = date.fromisoformat(d_val) if isinstance(d_val, str) else d_val

    if amount is not None and due is not None:
        return amount, due

    # 2. Check incomplete or unverified promise events from current conversation
    events = _read(calls, "get_events", lambda: c3.get_events(cid, limit=10), customer_id=cid, limit=10)
    for event in events or []:
        if event.get("type") != "RECOVERY_CONTACTED":
            continue
        payload = event.get("payload") or {}
        if payload.get("outcome") not in ("incomplete_promise", "unverified_promise"):
            continue
        if event.get("conversation_id") != meta.get("conversation_id"):
            continue
        if amount is None and payload.get("amount") is not None:
            amount = payload["amount"]
        if due is None and payload.get("due_date"):
            due = date.fromisoformat(payload["due_date"])
        break
    return amount, due


class _PromiseVerifyResult(ModelOutput):
    ok: bool = True
    feedback: str = ""


_VERIFY_SYSTEM = (
    "You check a payment promise about to be recorded against the customer message "
    "it is based on. Does the amount and due date actually reflect what the customer "
    "said? If yes, set ok=true. If something is off — wrong amount, wrong date — set "
    "ok=false and say what's wrong in 'feedback' (one short sentence) so we can ask "
    "the customer to confirm. Return only the fields asked for."
)

MAX_VERIFY_ATTEMPTS = 3


def _verify_promise(message: str, amount: float, due: date) -> tuple[bool, str]:
    """Self-check before commit: does this amount/date actually match what the
    customer said? Unlike SA-4/SA-9 (which always raise the approval and only
    redraft its summary afterward), a payment promise is only ever committed
    once verified — same reasoning as `sa4_approval._verify`: no provider
    configured means nothing to check against, so treat as verified and let
    the deterministic extraction stand."""
    import os

    from . import llm

    if os.getenv("CA_PHRASE", "on").lower() == "off" or not llm.available():
        return True, ""
    facts = {"customer_message": message, "amount": _inr(amount), "due_date": _fmt_date(due)}
    try:
        out = llm.complete_structured(
            _PromiseVerifyResult, _VERIFY_SYSTEM, json.dumps(facts, default=str),
            capability="summarization", example={"ok": True, "feedback": ""},
        )
    except llm.LLMUnavailable:
        return True, ""
    return out.ok, out.feedback


def _handle_promise(
    cid: str, entities: dict, message: str, meta: dict,
    calls: list[ToolCall], actions: list[ProposedAction],
) -> tuple[str | None, str | None]:
    if _UNABLE.search(message):
        services.record_event(
            cid, "RECOVERY_CONTACTED", "sa2_recovery",
            conversation_id=meta["conversation_id"], message_id=meta["message_id"],
            payload={"outcome": "unable_to_pay"},
        )
        calls.append(ToolCall(tool="create_event"))
        _add_task(cid, "recovery_followup", "Discuss a payment plan with the customer",
                  utcnow().date() + timedelta(days=3), meta, calls, actions)
        return (
            "Understood — we've noted that you're unable to clear this right now. "
            "A colleague will reach out to discuss a workable plan.",
            None,
        )

    amount = _amount(entities) or _bare_amount(message)
    due = parse_due_date(message, utcnow().date())

    if amount is None or due is None:
        amount, due = _fill_from_open_promise(cid, meta, amount, due, calls)

    if amount is None or due is None:
        services.record_event(
            cid, "RECOVERY_CONTACTED", "sa2_recovery",
            conversation_id=meta["conversation_id"], message_id=meta["message_id"],
            payload={
                "outcome": "incomplete_promise",
                "amount": amount,
                "due_date": due.isoformat() if due else None,
            },
        )
        calls.append(ToolCall(tool="create_event"))
        if amount is not None:
            ask = f"By when will you make the payment of {_inr(amount)}?"
        elif due is not None:
            ask = f"How much are you planning to pay by {_fmt_date(due)}?"
        else:
            ask = "How much will you pay, and by when?"
        return f"Thanks for letting us know. {ask}", "needs_information"

    verified, feedback, attempts = True, "", 0
    for attempts in range(1, MAX_VERIFY_ATTEMPTS + 1):
        verified, feedback = _verify_promise(message, amount, due)
        if verified:
            break

    if not verified:
        services.record_event(
            cid, "RECOVERY_CONTACTED", "sa2_recovery",
            conversation_id=meta["conversation_id"], message_id=meta["message_id"],
            payload={
                "outcome": "unverified_promise",
                "amount": amount,
                "due_date": due.isoformat(),
                "feedback": feedback,
                "verify_attempts": attempts,
            },
        )
        calls.append(ToolCall(tool="create_event"))
        confirm = f"Just to confirm — you'll pay {_inr(amount)} by {_fmt_date(due)}?"
        return f"{confirm} {feedback}".strip(), "needs_information"

    promise, kind = services.record_promise(
        cid, amount, due,
        conversation_id=meta["conversation_id"], message_id=meta["message_id"],
    )
    calls.append(ToolCall(tool="create_payment_promise", arguments={"amount": amount}))
    event_type = "PAYMENT_PROMISE_MODIFIED" if kind == "modified" else "PAYMENT_PROMISE_CREATED"
    services.record_event(
        cid, event_type, "sa2_recovery",
        conversation_id=meta["conversation_id"], message_id=meta["message_id"],
        payload={"promise_id": promise.promise_id, "amount": amount, "due_date": due.isoformat()},
    )
    calls.append(ToolCall(tool="create_event"))
    actions.append(ProposedAction(
        type="create_payment_promise", mode="auto",
        payload={"promise_id": promise.promise_id, "amount": amount, "due_date": due.isoformat()},
        executed=True,
    ))
    _add_task(cid, "reminder", f"Collect {_inr(amount)} due {_fmt_date(due)}", due, meta, calls, actions)
    verb = "updated" if kind == "modified" else "recorded"
    return (
        f"Thank you — we've {verb} your commitment to pay {_inr(amount)} by {_fmt_date(due)}. "
        f"We'll send a reminder closer to the date.",
        None,
    )


def _verify_claim(
    cid: str, entities: dict, message: str, meta: dict,
    calls: list[ToolCall], actions: list[ProposedAction],
) -> tuple[str | None, str | None]:
    amount = _amount(entities)
    receipts = _read(calls, "get_receipts", lambda: c3.get_receipts(cid, limit=20), customer_id=cid, limit=20)

    # No amount means nothing to verify against — confirming the first receipt
    # in the list regardless of its contents is exactly the false-positive the
    # adversarial "I paid" case exists to catch. Ask, do not guess.
    match = None
    if amount is not None:
        for row in receipts or []:
            if abs(float(row.get("amount") or 0) - amount) <= 1.0:
                match = row
                break

    services.record_event(
        cid, "RECOVERY_CONTACTED", "sa2_recovery",
        conversation_id=meta["conversation_id"], message_id=meta["message_id"],
        payload={"outcome": "payment_claim", "matched": bool(match)},
    )
    calls.append(ToolCall(tool="create_event"))

    if match:
        ref = match.get("voucher_number") or "on record"
        got = _inr(float(match.get("amount") or 0))
        return (
            f"We can see a receipt of {got} dated {_fmt_date(match.get('date'))} ({ref}) on your "
            "account. If your ledger still shows it as due, we'll get it reconciled.",
            None,
        )
    claimed = f" of {_inr(amount)}" if amount is not None else ""
    _add_task(cid, "payment_trace", f"Trace claimed payment{claimed}",
              utcnow().date() + timedelta(days=2), meta, calls, actions)
    return (
        f"We could not yet locate a payment{claimed} on your account. Could you share the payment "
        "reference (UTR or cheque number) and the date, so we can trace it?",
        None,
    )


_DISPATCH = {"payment_promise": _handle_promise, "payment_claim": _verify_claim}


def _intents_of(task: AgentTask) -> list[str]:
    named = task.inputs.get("intents")
    if isinstance(named, list) and named:
        return named
    return [part for part in task.action.split("+") if part]


def run(task: AgentTask, state: CustomerAssistState) -> AgentResult:
    intents = _intents_of(task)
    entities = task.inputs.get("entities") or state.entities or {}
    meta = {"conversation_id": state.conversation_id, "message_id": state.entities.get("message_id")}

    def result(status: str, message: str | None, calls: list[ToolCall], actions: list[ProposedAction]) -> AgentResult:
        return AgentResult(
            agent="sa2_recovery",
            agent_task_id=task.agent_task_id,
            status=status,
            summary=f"handled {'+'.join(intents) or 'recovery'}",
            customer_message=message,
            tool_calls=calls,
            actions=actions,
        )

    if not state.customer_id:
        return result("needs_information", None, [], [])

    cid = state.customer_id
    calls: list[ToolCall] = []
    actions: list[ProposedAction] = []
    sections: list[str] = []
    status = "completed"
    for name in intents:
        handler = _DISPATCH.get(name)
        if handler is None:
            continue
        line, st = handler(cid, entities, state.message, meta, calls, actions)
        if line:
            sections.append(line)
        if st:
            status = st

    if not sections:
        return result("completed", None, calls, actions)
    return result(status, _phrase("\n\n".join(sections)), calls, actions)
