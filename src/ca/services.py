"""Phase 5 — business-service write layer.

The first code that *commits* state. Agents decide; these functions validate the
shape (via the contracts) and write it to the app database. Two rules hold
everything together:

* **Idempotency.** Every write carries an `_idempotency` key derived from the
  message that caused it. A replayed message re-finds its own record instead of
  creating a second — "no duplicate promise/event" is enforced here, once, not
  in every caller.
* **Dates as ISO strings.** `PaymentPromise.due_date` is a `date`, which BSON
  cannot store. Persisting with `mode="json"` writes ISO strings, and because
  `YYYY-MM-DD` sorts lexicographically the missed-promise sweep can still range
  the field in Mongo.

ponytail: idempotency is find-then-insert, not a unique index — a single
conversation writes serially. Add a unique sparse index on `_idempotency` if
concurrent writers ever race the same message.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .config import app_db
from .contracts import Approval, Case, Event, EventType, Message, PaymentPromise, Task, utcnow


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def record_promise(
    customer_id: str,
    amount: float,
    due_date: date,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
    db: Any | None = None,
) -> tuple[PaymentPromise, str]:
    """Create or revise the customer's payment promise. Returns (promise, kind)
    with kind in {"created", "modified", "replay"}:

    * replay   — this exact message already produced a promise; return it.
    * modified — the customer already has an open promise from another message;
                 revise it in place (PAYMENT_PROMISE_MODIFIED).
    * created  — no open promise; make one (PAYMENT_PROMISE_CREATED).
    """
    db = db if db is not None else app_db()
    coll = db["payment_promises"]
    key = f"promise:{message_id}" if message_id else None

    if key:
        prior = coll.find_one({"_idempotency": key})
        if prior:
            return PaymentPromise.model_validate(_clean(prior)), "replay"

    open_prior = coll.find_one({"customer_id": customer_id, "status": "promised"})
    if open_prior:
        promise = PaymentPromise.model_validate(_clean(open_prior)).model_copy(
            update={"amount": amount, "due_date": due_date, "updated_at": utcnow()}
        )
        doc = promise.model_dump(mode="json")
        if key:
            doc["_idempotency"] = key
        coll.replace_one({"promise_id": promise.promise_id}, doc)
        return promise, "modified"

    promise = PaymentPromise(
        customer_id=customer_id, amount=amount, due_date=due_date,
        conversation_id=conversation_id,
    )
    doc = promise.model_dump(mode="json")
    if key:
        doc["_idempotency"] = key
    coll.insert_one(doc)
    return promise, "created"


def record_event(
    customer_id: str,
    type: EventType,
    source: str,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
    agent_run_id: str | None = None,
    payload: dict[str, Any] | None = None,
    db: Any | None = None,
) -> tuple[Event, bool]:
    """Append to the event store. Returns (event, created); a replayed message
    re-finds the same event rather than doubling it."""
    db = db if db is not None else app_db()
    coll = db["events"]
    key = f"{type}:{message_id}" if message_id else None

    if key:
        prior = coll.find_one({"_idempotency": key})
        if prior:
            return Event.model_validate(_clean(prior)), False

    event = Event(
        customer_id=customer_id, type=type, source=source,
        conversation_id=conversation_id, agent_run_id=agent_run_id, payload=payload or {},
    )
    doc = event.model_dump(mode="json")
    if key:
        doc["_idempotency"] = key
    coll.insert_one(doc)
    return event, True


def create_task(
    customer_id: str,
    kind: str,
    title: str,
    *,
    due_date: date | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    source: str = "sa2_recovery",
    db: Any | None = None,
) -> tuple[Task, bool]:
    """Record a follow-up. Idempotent on (kind, message_id): the same message
    cannot spawn two of the same follow-up."""
    db = db if db is not None else app_db()
    coll = db["tasks"]
    key = f"task:{kind}:{message_id}" if message_id else None

    if key:
        prior = coll.find_one({"_idempotency": key})
        if prior:
            return Task.model_validate(_clean(prior)), False

    task = Task(
        customer_id=customer_id, kind=kind, title=title, due_date=due_date,
        conversation_id=conversation_id, source=source,
    )
    doc = task.model_dump(mode="json")
    if key:
        doc["_idempotency"] = key
    coll.insert_one(doc)
    return task, True


def create_case(
    customer_id: str,
    title: str,
    *,
    type: str = "dispute",
    priority: str = "normal",
    evidence: list[dict[str, Any]] | None = None,
    conversation_id: str | None = None,
    message_id: str | None = None,
    db: Any | None = None,
) -> tuple[Case, bool]:
    """Open a dispute case. Idempotent on message_id: a replayed message
    re-finds its own case rather than opening a second.

    Also dedupes across messages within the same conversation: a case already
    open here (e.g. from a bare complaint) gets its evidence extended and
    title refreshed as later turns add the invoice/item — same shape as
    `create_approval`'s conversation-scoped revision — rather than leaving a
    reviewer two disconnected cases for one complaint (seen live rerunning
    evals/reports/dispute_approval_scenarios.md, S1: a case opened before the
    invoice number arrived, then a second case for the same complaint once it
    did)."""
    db = db if db is not None else app_db()
    coll = db["cases"]
    key = f"case:{message_id}" if message_id else None

    if key:
        prior = coll.find_one({"_idempotency": key})
        if prior:
            return Case.model_validate(_clean(prior)), False

    if conversation_id:
        open_prior = coll.find_one({
            "conversation_id": conversation_id, "type": type,
            "status": {"$in": ["open", "investigating", "waiting"]},
        })
        if open_prior:
            merged_evidence = open_prior.get("evidence") or []
            merged_evidence = merged_evidence + [e for e in (evidence or []) if e not in merged_evidence]
            case = Case.model_validate(_clean(open_prior)).model_copy(update={
                "title": title or open_prior.get("title", ""),
                "evidence": merged_evidence,
                "updated_at": utcnow(),
            })
            doc = case.model_dump(mode="json")
            if key:
                doc["_idempotency"] = key
            coll.replace_one({"case_id": case.case_id}, doc)
            return case, False

    case = Case(
        customer_id=customer_id, conversation_id=conversation_id, type=type,
        priority=priority, title=title, evidence=evidence or [],
    )
    doc = case.model_dump(mode="json")
    if key:
        doc["_idempotency"] = key
    coll.insert_one(doc)
    return case, True


# Case.status has no literal "dropped" value — a dropped dispute is recorded as
# "closed" (no further action) rather than growing the schema for a label that
# only changes the customer-facing wording, which resolution_message() owns.
RESOLUTION_STATUS = {"solved": "resolved", "dropped": "closed"}


def resolve_case(
    case_id: str, resolution: str, *, outcome: str = "solved", db: Any | None = None
) -> Case | None:
    """Close a case with its resolution. `outcome` is "solved" or "dropped"."""
    db = db if db is not None else app_db()
    coll = db["cases"]
    doc = coll.find_one({"case_id": case_id})
    if not doc:
        return None
    case = Case.model_validate(_clean(doc)).model_copy(
        update={"status": RESOLUTION_STATUS[outcome], "resolution": resolution, "updated_at": utcnow()}
    )
    coll.replace_one({"case_id": case_id}, case.model_dump(mode="json"))
    return case


def create_approval(
    customer_id: str,
    type: str,
    requested_by: str,
    *,
    amount: float | None = None,
    context: dict[str, Any] | None = None,
    recommendation: str = "",
    summary: str = "",
    conversation_id: str | None = None,
    message_id: str | None = None,
    db: Any | None = None,
) -> tuple[Approval, bool]:
    """Raise a pending approval request. Idempotent on message_id.

    Also dedupes across messages within the same conversation: a vague ask
    ("I want to settle") followed by a concrete one ("settle for Rs.79") of
    the same type revises the same pending approval rather than leaving a
    reviewer two disconnected records for one customer ask — same shape as
    `record_promise`'s open-promise revision, just scoped to `type` too since
    a customer can have more than one approval type pending at once.

    This never decides the outcome — status is always "pending" on creation.
    `create_approval` is an auto-mode tool (registry.py): raising the request is
    safe by design. Only `decide_approval` may move it off "pending", and that
    is the one human_approval-mode boundary this whole module protects.
    """
    db = db if db is not None else app_db()
    coll = db["approvals"]
    key = f"approval:{message_id}" if message_id else None

    if key:
        prior = coll.find_one({"_idempotency": key})
        if prior:
            return Approval.model_validate(_clean(prior)), False

    if conversation_id:
        open_prior = coll.find_one({
            "conversation_id": conversation_id, "type": type, "status": "pending",
        })
        if open_prior:
            approval = Approval.model_validate(_clean(open_prior)).model_copy(update={
                "amount": amount if amount is not None else open_prior.get("amount"),
                "context": context or open_prior.get("context") or {},
                "recommendation": recommendation or open_prior.get("recommendation", ""),
                "summary": summary or open_prior.get("summary", ""),
            })
            doc = approval.model_dump(mode="json")
            if key:
                doc["_idempotency"] = key
            coll.replace_one({"approval_id": approval.approval_id}, doc)
            return approval, False

    approval = Approval(
        customer_id=customer_id, conversation_id=conversation_id, type=type,
        requested_by=requested_by, amount=amount, context=context or {},
        recommendation=recommendation, summary=summary,
    )
    doc = approval.model_dump(mode="json")
    if key:
        doc["_idempotency"] = key
    coll.insert_one(doc)
    return approval, True


def update_approval_draft(
    approval_id: str, *, summary: str | None = None, context: dict[str, Any] | None = None,
    db: Any | None = None,
) -> Approval | None:
    """Patch the draft `summary`/`context` a verifier redrafted. Never touches
    `status` or `decided_by` — same boundary as `create_approval`: this can
    only change how the request is *described*, never whether it is approved,
    so it stays `auto`-mode (registry.py) like raising the request itself."""
    db = db if db is not None else app_db()
    coll = db["approvals"]
    doc = coll.find_one({"approval_id": approval_id})
    if not doc:
        return None
    updates: dict[str, Any] = {}
    if summary is not None:
        updates["summary"] = summary
    if context is not None:
        updates["context"] = context
    if not updates:
        return Approval.model_validate(_clean(doc))
    approval = Approval.model_validate(_clean(doc)).model_copy(update=updates)
    coll.replace_one({"approval_id": approval_id}, approval.model_dump(mode="json"))
    return approval


def decide_approval(
    approval_id: str, approved: bool, decided_by: str, *, db: Any | None = None
) -> Approval | None:
    """Record a human's decision. The only place `Approval.status` can leave
    "pending" — no agent calls this.

    ponytail: no human-workflow trigger yet — call this manually until Phase 11
    wires an actual approval queue.
    """
    db = db if db is not None else app_db()
    coll = db["approvals"]
    doc = coll.find_one({"approval_id": approval_id})
    if not doc:
        return None
    approval = Approval.model_validate(_clean(doc)).model_copy(
        update={
            "status": "approved" if approved else "rejected",
            "decided_by": decided_by,
            "decided_at": utcnow(),
        }
    )
    coll.replace_one({"approval_id": approval_id}, approval.model_dump(mode="json"))
    return approval


def send_customer_message(
    customer_id: str, conversation_id: str | None, text: str, *, db: Any | None = None
) -> Message | None:
    """Deliver an outbound reply — the follow-up after a human decides an
    approval or resolves a dispute. Returns None (delivers nothing) when there
    is no conversation to put it on; the caller decides how to surface that.
    """
    if not conversation_id:
        return None
    db = db if db is not None else app_db()
    message = Message(
        conversation_id=conversation_id, customer_id=customer_id,
        channel="chat", direction="outbound", text=text,
    )
    db["messages"].insert_one(message.model_dump(mode="python"))
    db["conversations"].update_one(
        {"conversation_id": conversation_id}, {"$set": {"updated_at": utcnow()}}
    )
    return message


def is_missed(promise: PaymentPromise, as_of: date) -> bool:
    """A promise is missed once its due date has passed with money still owed.
    Pure — the sweep below is just this decision applied over the store."""
    return (
        promise.status == "promised"
        and promise.due_date < as_of
        and promise.paid_amount < promise.amount
    )


def sweep_missed_promises(
    customer_id: str, *, as_of: date | None = None, db: Any | None = None
) -> list[str]:
    """Flip every past-due open promise to `missed` and emit its event. Returns
    the ids flipped. Meant to run from a scheduled job; unwired for now.

    ponytail: no cron here — call it from a script when the missed-promise sweep
    is scheduled. The decision (`is_missed`) is what Phase 5 has to get right.
    """
    db = db if db is not None else app_db()
    as_of = as_of or utcnow().date()
    coll = db["payment_promises"]
    flipped: list[str] = []
    for doc in coll.find({"customer_id": customer_id, "status": "promised"}):
        promise = PaymentPromise.model_validate(_clean(doc))
        if is_missed(promise, as_of):
            coll.update_one(
                {"promise_id": promise.promise_id},
                {"$set": {"status": "missed", "updated_at": utcnow().isoformat()}},
            )
            record_event(
                customer_id, "PAYMENT_PROMISE_MISSED", "sa2_recovery",
                payload={"promise_id": promise.promise_id, "amount": promise.amount}, db=db,
            )
            flipped.append(promise.promise_id)
    return flipped
