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
* the **due date** is parsed here deterministically (`parse_due_date`) from the
  message text — our arithmetic, never the model's guess;
* the **receipts** come from `customer360`.

No LLM produces a figure that reaches the customer or the store.

ponytail: reply is templated (no phrasing pass) and no follow-up `create_task`
is emitted — the next action is stated in the reply. Add either if a Phase-5
eval asks for it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from . import customer360 as c3
from . import services
from .contracts import AgentResult, AgentTask, CustomerAssistState, ProposedAction, ToolCall
from .contracts import utcnow
from .sa1_general import _inr, _fmt_date, _phrase, _read  # reuse, don't reimplement


# --------------------------------------------------------------------------
# Deterministic due-date parsing — our arithmetic, never the model's
# --------------------------------------------------------------------------

_MONTHS: dict[str, int] = {
    m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1)
}
_MONTHS.update({name[:3]: i for name, i in list(_MONTHS.items())})  # jan..dec
_WEEKDAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}

_UNABLE = re.compile(
    r"\b(unable to pay|can'?t pay|cannot pay|not able to pay|no money|"
    r"don'?t have (the )?money|struggling to pay|out of funds)\b", re.I)


def _roll(d: date, today: date) -> date:
    """A bare day/month with no year means the next such date, not one in the
    past: '10 January' asked in August is next January."""
    return d.replace(year=d.year + 1) if d < today else d


def parse_due_date(text: str, today: date) -> date | None:
    """Best-effort parse of a promise deadline. Handles the phrasings this desk
    actually sees; returns None when it cannot be sure.

    ponytail: hand-rolled, not dateutil — the vocabulary is small and adding a
    dependency for it is not worth it. Add dateutil if free-text dates broaden.
    """
    t = (text or "").lower()

    if re.search(r"\bday after tomorrow\b", t):
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    if re.search(r"\btoday\b", t):
        return today
    m = re.search(r"\bin (\d{1,3}) days?\b", t)
    if m:
        return today + timedelta(days=int(m.group(1)))
    if re.search(r"\bnext week\b", t):
        return today + timedelta(days=7)
    if re.search(r"\b(end of (the )?month|month[- ]end)\b", t):
        first_next = date(today.year + (today.month == 12), (today.month % 12) + 1, 1)
        return first_next - timedelta(days=1)
    m = re.search(r"\b(?:by |next )?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
    if m:
        delta = (_WEEKDAYS[m.group(1)] - today.weekday()) % 7
        return today + timedelta(days=delta or 7)  # the next one, never today

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)  # ISO
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None

    # Scan every "20 August" / "August 20" candidate, not just the first token
    # pair — "2 lakh by 20 August" leads with a non-month "2 lakh".
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\b", t):  # 20 August
        if m.group(2)[:3] in _MONTHS:
            try:
                return _roll(date(today.year, _MONTHS[m.group(2)[:3]], int(m.group(1))), today)
            except ValueError:
                pass
    for m in re.finditer(r"\b([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", t):  # August 20
        if m.group(1)[:3] in _MONTHS:
            try:
                return _roll(date(today.year, _MONTHS[m.group(1)[:3]], int(m.group(2))), today)
            except ValueError:
                pass

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)  # 25/08 or 25-08-2026
    if m:
        day, month, year = int(m[1]), int(m[2]), m[3]
        try:
            if year:
                y = int(year)
                return date(y + 2000 if y < 100 else y, month, day)
            return _roll(date(today.year, month, day), today)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def _amount(entities: dict[str, Any]) -> float | None:
    amounts = entities.get("amounts") or []
    return float(amounts[0]) if amounts else None


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

    amount = _amount(entities)
    due = parse_due_date(message, utcnow().date())

    if amount is None or due is None:
        services.record_event(
            cid, "RECOVERY_CONTACTED", "sa2_recovery",
            conversation_id=meta["conversation_id"], message_id=meta["message_id"],
            payload={"outcome": "incomplete_promise"},
        )
        calls.append(ToolCall(tool="create_event"))
        if amount is not None:
            ask = f"By when will you make the payment of {_inr(amount)}?"
        elif due is not None:
            ask = f"How much are you planning to pay by {_fmt_date(due)}?"
        else:
            ask = "How much will you pay, and by when?"
        return f"Thanks for letting us know. {ask}", "needs_information"

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

    match = None
    for row in receipts or []:
        if amount is None or abs(float(row.get("amount") or 0) - amount) <= 1.0:
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
