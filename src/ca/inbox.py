"""Phase 2 — input adapter and conversation layer.

Email, chat and webhook all collapse into one `Message` on one `Conversation`.
Nothing downstream should ever branch on the channel again.

    conversation, message, created = ingest("email", raw_rfc822_string)

Two properties matter more than anything else here, because everything later
depends on them:

* **Idempotency.** The same delivery arriving twice produces one message. A
  retried webhook or a re-polled mailbox must not create a second promise, a
  second dispute or a second reply. Enforced by a unique index on
  `(channel, external_id)`, not by an in-process check.
* **Thread continuity.** A reply lands on the conversation it answers, even
  when the customer writes from a different address — the thread carries the
  customer, not the sender line.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from email import message_from_string
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any, Literal

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from .config import app_db
from .contracts import Channel, Conversation, Message, utcnow

# How far back a subject line alone may re-open a thread.
SUBJECT_THREAD_WINDOW = timedelta(days=30)

_REPLY_PREFIX = re.compile(r"^\s*(re|fw|fwd|aw|antwort)\s*(\[\d+\])?\s*:\s*", re.I)
_QUOTE_MARKER = re.compile(
    r"^\s*(>|on .{0,80}\bwrote:|-{2,}\s*original message|from:\s)", re.I
)


class InputError(ValueError):
    """The payload cannot be turned into a message."""


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------


def normalize_subject(subject: str | None) -> str:
    """`Re: Fwd: RE: Invoice 12` -> `invoice 12`. Used as a threading key."""
    text = (subject or "").strip()
    while True:
        stripped = _REPLY_PREFIX.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return re.sub(r"\s+", " ", text).strip().lower()


def strip_quoted(text: str) -> str:
    """Drop the quoted history so intent classification sees only what the
    customer just wrote. Falls back to the full text if that would empty it."""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if _QUOTE_MARKER.match(line):
            break
        lines.append(line)
    trimmed = "\n".join(lines).strip()
    return trimmed or (text or "").strip()


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            try:
                return _as_utc(parsedate_to_datetime(value))
            except (TypeError, ValueError):
                pass
    return utcnow()


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


# --------------------------------------------------------------------------
# Parsers — payload in, normalized dict out
# --------------------------------------------------------------------------


def parse_email(raw: str | dict[str, Any]) -> dict[str, Any]:
    """Accepts a raw RFC-822 string or an already-split dict from a mail API."""
    if isinstance(raw, dict):
        headers = {k.lower(): v for k, v in raw.items()}
        sender = headers.get("from") or ""
        body = headers.get("text") or headers.get("body") or ""
        references = headers.get("references") or ""
        attachments = raw.get("attachments") or []
    else:
        parsed = message_from_string(raw)
        headers = {k.lower(): v for k, v in parsed.items()}
        sender = headers.get("from") or ""
        references = headers.get("references") or ""
        body, attachments = _email_body(parsed)

    if not isinstance(references, str):
        references = " ".join(references)
    addresses = [a for _, a in getaddresses([sender]) if a]

    return {
        "channel": "email",
        "text": strip_quoted(body),
        "raw_text": body,
        "timestamp": _as_utc(headers.get("date")),
        "external_id": (headers.get("message-id") or "").strip() or None,
        "subject": headers.get("subject"),
        "sender": addresses[0] if addresses else None,
        "attachments": attachments,
        "metadata": {
            "in_reply_to": (headers.get("in-reply-to") or "").strip() or None,
            "references": references.split(),
            "to": headers.get("to"),
        },
    }


def _email_body(parsed: Any) -> tuple[str, list[dict[str, Any]]]:
    """Prefer text/plain; keep attachment metadata only, never the bytes."""
    if not parsed.is_multipart():
        return parsed.get_payload(decode=False) or "", []

    text = html = ""
    attachments: list[dict[str, Any]] = []
    for part in parsed.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        filename = part.get_filename()
        if filename or part.get_content_disposition() == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {"filename": filename, "content_type": content_type, "size": len(payload)}
            )
        elif content_type == "text/plain" and not text:
            text = part.get_payload(decode=False) or ""
        elif content_type == "text/html" and not html:
            html = part.get_payload(decode=False) or ""
    return text or re.sub(r"<[^>]+>", " ", html), attachments


def parse_chat(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("text") or payload.get("message") or ""
    return {
        "channel": "chat",
        "text": text.strip(),
        "raw_text": text,
        "timestamp": _as_utc(payload.get("timestamp") or payload.get("sent_at")),
        "external_id": payload.get("message_id") or payload.get("id"),
        "subject": None,
        "sender": payload.get("phone") or payload.get("email") or payload.get("user"),
        "attachments": payload.get("attachments") or [],
        "metadata": {
            "session_id": payload.get("session_id") or payload.get("thread_id"),
            "conversation_id": payload.get("conversation_id"),
            "customer_id": payload.get("customer_id"),
        },
    }


def parse_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_chat(payload)
    parsed["channel"] = "webhook"
    parsed["external_id"] = (
        payload.get("delivery_id") or payload.get("event_id") or parsed["external_id"]
    )
    parsed["metadata"]["source"] = payload.get("source")
    return parsed


PARSERS = {"email": parse_email, "chat": parse_chat, "webhook": parse_webhook}


def parse(channel: Channel, payload: Any) -> dict[str, Any]:
    try:
        parser = PARSERS[channel]
    except KeyError:
        raise InputError(f"unsupported channel: {channel!r}") from None
    parsed = parser(payload)
    if not parsed["text"].strip() and not parsed["attachments"]:
        raise InputError("message has neither text nor attachments")
    if not parsed["external_id"]:
        # No delivery id: derive a stable one so retries still deduplicate.
        seed = f"{channel}|{parsed['sender']}|{parsed['timestamp']}|{parsed['raw_text']}"
        parsed["external_id"] = "sha1:" + hashlib.sha1(seed.encode()).hexdigest()
        parsed["metadata"]["synthetic_external_id"] = True
    return parsed


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def ensure_indexes(db: Any | None = None) -> None:
    """Idempotency lives in the database, not in the process."""
    db = db if db is not None else app_db()
    db["messages"].create_index(
        [("channel", ASCENDING), ("external_id", ASCENDING)], unique=True, name="dedupe"
    )
    db["messages"].create_index([("conversation_id", ASCENDING), ("timestamp", ASCENDING)])
    db["conversations"].create_index([("channel", ASCENDING), ("thread_key", ASCENDING)])
    db["conversations"].create_index([("customer_id", ASCENDING), ("updated_at", ASCENDING)])


def _store(db: Any | None) -> Any:
    return db if db is not None else app_db()


# --------------------------------------------------------------------------
# Customer resolution
# --------------------------------------------------------------------------


def resolve_sender(parsed: dict[str, Any]) -> tuple[str | None, str]:
    """(customer_id, how). Never guesses: an ambiguous identifier resolves to
    None so the orchestrator can ask, rather than mailing one customer another
    customer's ledger."""
    from . import customer360 as c3

    hinted = parsed["metadata"].get("customer_id")
    if hinted:
        try:
            return c3.get_customer(hinted).customer_id, "hint"
        except c3.CustomerNotFoundError:
            return None, "hint_invalid"

    sender = parsed.get("sender")
    if not sender:
        return None, "no_sender"

    how = "email" if "@" in str(sender) else "mobile" if len(_digits(sender)) >= 10 else "name"
    try:
        return c3.resolve_customer(str(sender)).customer_id, how
    except c3.AmbiguousCustomerError:
        return None, f"{how}_ambiguous"
    except c3.CustomerNotFoundError:
        return None, f"{how}_unknown"


# --------------------------------------------------------------------------
# Conversation resolution
# --------------------------------------------------------------------------


def thread_key_for(parsed: dict[str, Any]) -> str | None:
    meta = parsed["metadata"]
    if parsed["channel"] == "email":
        subject = normalize_subject(parsed["subject"])
        return f"subject:{subject}" if subject else None
    session = meta.get("session_id")
    return f"session:{session}" if session else None


def _load_conversation(db: Any, query: dict[str, Any]) -> Conversation | None:
    doc = db["conversations"].find_one(query, sort=[("updated_at", -1)])
    return Conversation.model_validate(_clean(doc)) if doc else None


def _clean(doc: dict[str, Any]) -> dict[str, Any]:
    """Strip `_id` and re-attach UTC. BSON has no timezone, so every datetime
    comes back naive; comparing one against a fresh `utcnow()` would raise."""
    return {
        k: _as_utc(v) if isinstance(v, datetime) else v
        for k, v in doc.items()
        if k != "_id"
    }


def resolve_conversation(
    parsed: dict[str, Any], customer_id: str | None, *, db: Any | None = None
) -> tuple[Conversation, str]:
    """(conversation, how). Tried in order of how much the signal is trusted."""
    db = _store(db)
    channel = parsed["channel"]
    meta = parsed["metadata"]

    explicit = meta.get("conversation_id")
    if explicit:
        found = _load_conversation(db, {"conversation_id": explicit})
        if found:
            return found, "explicit"

    # An email reply names the message it answers; that message knows its thread.
    refs = [r for r in ([meta.get("in_reply_to")] + list(meta.get("references") or [])) if r]
    if refs:
        parent = db["messages"].find_one({"external_id": {"$in": refs}})
        if parent:
            found = _load_conversation(db, {"conversation_id": parent["conversation_id"]})
            if found:
                return found, "in_reply_to"

    key = thread_key_for(parsed)
    if key:
        query: dict[str, Any] = {"channel": channel, "thread_key": key, "status": {"$ne": "closed"}}
        if key.startswith("subject:"):
            # A subject line is a weak signal — same customer and recent only.
            query["customer_id"] = customer_id
            query["updated_at"] = {"$gte": parsed["timestamp"] - SUBJECT_THREAD_WINDOW}
        found = _load_conversation(db, query)
        if found:
            return found, "thread_key"

    conversation = Conversation(
        customer_id=customer_id,
        channel=channel,
        subject=parsed.get("subject"),
        thread_key=key,
        created_at=parsed["timestamp"],
        updated_at=parsed["timestamp"],
    )
    db["conversations"].insert_one(conversation.model_dump(mode="python"))
    return conversation, "new"


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------


def ingest(
    channel: Channel,
    payload: Any,
    *,
    direction: Literal["inbound", "outbound"] = "inbound",
    db: Any | None = None,
) -> tuple[Conversation, Message, bool]:
    """Normalize, resolve, persist. Returns (conversation, message, created).

    `created` is False when this delivery was already ingested — the caller must
    then do nothing else, or a retry becomes a duplicate action.
    """
    db = _store(db)
    parsed = parse(channel, payload)

    existing = db["messages"].find_one(
        {"channel": channel, "external_id": parsed["external_id"]}
    )
    if existing:
        message = Message.model_validate(_clean(existing))
        conversation = _load_conversation(db, {"conversation_id": message.conversation_id})
        assert conversation is not None, "message without its conversation"
        return conversation, message, False

    customer_id, how = resolve_sender(parsed)
    conversation, thread_how = resolve_conversation(parsed, customer_id, db=db)

    # The thread is the stronger identity signal: a customer writing from a new
    # address keeps the customer the thread was already about.
    if conversation.customer_id and not customer_id:
        customer_id = conversation.customer_id
        how = f"{how}->thread"

    message = Message(
        conversation_id=conversation.conversation_id,
        customer_id=customer_id,
        channel=channel,
        direction=direction,
        text=parsed["text"],
        timestamp=parsed["timestamp"],
        attachments=parsed["attachments"],
        external_id=parsed["external_id"],
        metadata={
            **parsed["metadata"],
            "subject": parsed.get("subject"),
            "sender": parsed.get("sender"),
            "resolved_by": how,
            "thread_resolved_by": thread_how,
        },
    )

    try:
        db["messages"].insert_one(message.model_dump(mode="python"))
    except DuplicateKeyError:
        # Two deliveries raced. The database decided; re-read the winner.
        winner = db["messages"].find_one(
            {"channel": channel, "external_id": parsed["external_id"]}
        )
        message = Message.model_validate(_clean(winner))
        conversation = _load_conversation(db, {"conversation_id": message.conversation_id})
        return conversation, message, False

    update: dict[str, Any] = {"updated_at": max(conversation.updated_at, message.timestamp)}
    if customer_id and not conversation.customer_id:
        update["customer_id"] = customer_id  # an unknown sender identified later
    if not conversation.subject and parsed.get("subject"):
        update["subject"] = parsed["subject"]
    db["conversations"].update_one(
        {"conversation_id": conversation.conversation_id}, {"$set": update}
    )
    conversation = conversation.model_copy(update=update)

    return conversation, message, True


def conversation_messages(conversation_id: str, *, db: Any | None = None) -> list[Message]:
    """Oldest first, regardless of the order deliveries arrived in."""
    docs = _store(db)["messages"].find({"conversation_id": conversation_id}).sort("timestamp", 1)
    return [Message.model_validate(_clean(d)) for d in docs]


def customer_conversations(customer_id: str, *, db: Any | None = None) -> list[Conversation]:
    docs = _store(db)["conversations"].find({"customer_id": customer_id}).sort("updated_at", -1)
    return [Conversation.model_validate(_clean(d)) for d in docs]


__all__ = [
    "InputError",
    "conversation_messages",
    "customer_conversations",
    "ensure_indexes",
    "ingest",
    "normalize_subject",
    "parse",
    "parse_chat",
    "parse_email",
    "parse_webhook",
    "resolve_conversation",
    "resolve_sender",
    "strip_quoted",
]
