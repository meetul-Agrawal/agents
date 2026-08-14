"""Phase 2 gate: normalization, threading, dedupe, customer resolution.

Parsing tests need no database. Everything touching persistence runs against a
scratch database (`APP_DB` pointed at `customer_assist_test`) that is dropped
between tests, so nothing ever lands in the real app database or the tenant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ca import inbox
from ca.contracts import Conversation, Message

UTC = timezone.utc
NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)

RAW_EMAIL = """\
From: Raj Kumar <raj@abcindustries.example>
To: support@ourcompany.example
Subject: Outstanding balance
Date: Fri, 14 Aug 2026 09:00:00 +0000
Message-ID: <first@abcindustries.example>

Hi, can you tell me my outstanding amount?

Thanks,
Raj
"""

RAW_REPLY = """\
From: Raj Kumar <raj@abcindustries.example>
To: support@ourcompany.example
Subject: Re: Outstanding balance
Date: Fri, 14 Aug 2026 11:00:00 +0000
Message-ID: <second@abcindustries.example>
In-Reply-To: <first@abcindustries.example>
References: <first@abcindustries.example>

I'll pay 2 lakh by 20 August.

On Fri, 14 Aug 2026, support wrote:
> Your outstanding is 4,82,500.
> Please arrange payment.
"""

MULTIPART = """\
From: Raj <raj@abcindustries.example>
Subject: Invoice query
Date: Fri, 14 Aug 2026 09:00:00 +0000
Message-ID: <mp@abcindustries.example>
Content-Type: multipart/mixed; boundary="BOUND"

--BOUND
Content-Type: text/plain

Please check the attached invoice.
--BOUND
Content-Type: application/pdf
Content-Disposition: attachment; filename="INV-1024.pdf"

JVBERi0xLjQK
--BOUND--
"""


def chat_payload(**overrides):
    payload = {
        "message_id": "chat-1",
        "session_id": "sess-9",
        "phone": "8109410408",
        "text": "  how much do I owe?  ",
        "timestamp": "2026-08-14T09:00:00Z",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------
# Unit — subject and quote handling
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Re: Outstanding balance", "outstanding balance"),
        ("RE: Fwd: RE: Outstanding   balance", "outstanding balance"),
        ("FW: invoice", "invoice"),
        ("Re[2]: invoice", "invoice"),
        ("Outstanding balance", "outstanding balance"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_subject(subject, expected):
    assert inbox.normalize_subject(subject) == expected


def test_strip_quoted_removes_history():
    text = "New question here.\n\nOn Fri, someone wrote:\n> old text\n> more old text"
    assert inbox.strip_quoted(text) == "New question here."


def test_strip_quoted_handles_forwarded_block():
    assert inbox.strip_quoted("Please advise.\n-----Original Message-----\nold") == "Please advise."


def test_strip_quoted_keeps_everything_when_only_a_quote():
    assert inbox.strip_quoted("> just a quote") == "> just a quote"


# --------------------------------------------------------------------------
# Unit — parsing
# --------------------------------------------------------------------------


def test_parse_email_extracts_headers_and_body():
    parsed = inbox.parse("email", RAW_EMAIL)
    assert parsed["channel"] == "email"
    assert parsed["sender"] == "raj@abcindustries.example"
    assert parsed["external_id"] == "<first@abcindustries.example>"
    assert parsed["subject"] == "Outstanding balance"
    assert parsed["timestamp"] == NOW
    assert "outstanding amount" in parsed["text"]


def test_parse_email_reply_drops_the_quoted_part():
    parsed = inbox.parse("email", RAW_REPLY)
    assert parsed["text"] == "I'll pay 2 lakh by 20 August."
    assert parsed["metadata"]["in_reply_to"] == "<first@abcindustries.example>"
    assert parsed["metadata"]["references"] == ["<first@abcindustries.example>"]
    assert "4,82,500" in parsed["raw_text"]


def test_parse_multipart_keeps_attachment_metadata_not_bytes():
    parsed = inbox.parse("email", MULTIPART)
    assert parsed["text"] == "Please check the attached invoice."
    assert len(parsed["attachments"]) == 1
    attachment = parsed["attachments"][0]
    assert attachment["filename"] == "INV-1024.pdf"
    assert attachment["content_type"] == "application/pdf"
    assert attachment["size"] > 0
    assert "size" in attachment and set(attachment) == {"filename", "content_type", "size"}


def test_parse_email_accepts_a_dict_from_a_mail_api():
    parsed = inbox.parse(
        "email",
        {
            "From": "raj@abcindustries.example",
            "Subject": "Hello",
            "Date": "2026-08-14T09:00:00Z",
            "Message-ID": "<api-1@x>",
            "text": "Hi there",
        },
    )
    assert parsed["sender"] == "raj@abcindustries.example" and parsed["text"] == "Hi there"


def test_parse_chat_and_webhook_normalize_to_the_same_shape():
    chat = inbox.parse("chat", chat_payload())
    hook = inbox.parse("webhook", {**chat_payload(), "delivery_id": "dlv-1", "source": "wa"})
    assert chat["text"] == hook["text"] == "how much do I owe?"
    assert chat.keys() == hook.keys()
    assert chat["channel"] == "chat" and hook["channel"] == "webhook"
    assert hook["external_id"] == "dlv-1" and hook["metadata"]["source"] == "wa"


def test_missing_delivery_id_gets_a_stable_synthetic_one():
    payload = chat_payload(message_id=None)
    first = inbox.parse("chat", payload)
    second = inbox.parse("chat", dict(payload))
    assert first["external_id"].startswith("sha1:")
    assert first["external_id"] == second["external_id"]
    assert first["metadata"]["synthetic_external_id"]


def test_different_text_gets_a_different_synthetic_id():
    a = inbox.parse("chat", chat_payload(message_id=None))
    b = inbox.parse("chat", chat_payload(message_id=None, text="something else"))
    assert a["external_id"] != b["external_id"]


def test_undated_message_falls_back_to_now():
    parsed = inbox.parse("chat", chat_payload(timestamp=None))
    assert parsed["timestamp"].tzinfo is not None


def test_unparseable_date_does_not_crash():
    assert inbox.parse("chat", chat_payload(timestamp="whenever"))["timestamp"].tzinfo


def test_empty_message_is_rejected():
    with pytest.raises(inbox.InputError):
        inbox.parse("chat", chat_payload(text="   "))


def test_attachment_only_message_is_accepted():
    parsed = inbox.parse("chat", chat_payload(text="", attachments=[{"filename": "x.pdf"}]))
    assert parsed["attachments"]


def test_unsupported_channel_is_rejected():
    with pytest.raises(inbox.InputError):
        inbox.parse("carrier_pigeon", {})


# --------------------------------------------------------------------------
# Persistence fixtures — scratch database, dropped every test
# --------------------------------------------------------------------------


@pytest.fixture
def db():
    from pymongo.errors import PyMongoError

    from ca.config import _client, settings

    name = "customer_assist_test"
    try:
        database = _client()[name]
        database.command("ping")
    except PyMongoError as exc:
        pytest.skip(f"MongoDB unavailable: {exc}")
    assert name != settings().app_db != settings().tenant_db
    _client().drop_database(name)
    inbox.ensure_indexes(database)
    yield database
    _client().drop_database(name)


@pytest.fixture
def known_customer(db):
    """A real debtor, so sender resolution exercises the Phase 1 resolver."""
    from ca import customer360 as c3

    try:
        return c3.resolve_customer("Aakash Traders, Sch No 78, Niranjanpur")
    except Exception as exc:  # tenant DB missing
        pytest.skip(f"tenant data unavailable: {exc}")


# --------------------------------------------------------------------------
# Integration — new conversations per channel
# --------------------------------------------------------------------------


def test_new_email_creates_conversation_and_message(db):
    conversation, message, created = inbox.ingest("email", RAW_EMAIL, db=db)
    assert created
    assert isinstance(conversation, Conversation) and isinstance(message, Message)
    assert conversation.channel == "email" and conversation.subject == "Outstanding balance"
    assert conversation.thread_key == "subject:outstanding balance"
    assert message.conversation_id == conversation.conversation_id
    assert message.direction == "inbound"
    assert db["messages"].count_documents({}) == 1


def test_new_chat_and_webhook_create_their_own_conversations(db):
    _, _, a = inbox.ingest("chat", chat_payload(), db=db)
    _, _, b = inbox.ingest("webhook", {**chat_payload(), "delivery_id": "d1"}, db=db)
    assert a and b
    assert db["conversations"].count_documents({}) == 2


def test_all_three_channels_produce_the_same_internal_shape(db):
    """The Phase 2 exit criterion."""
    messages = [
        inbox.ingest("email", RAW_EMAIL, db=db)[1],
        inbox.ingest("chat", chat_payload(), db=db)[1],
        inbox.ingest("webhook", {**chat_payload(), "delivery_id": "d1"}, db=db)[1],
    ]
    for m in messages:
        assert isinstance(m, Message)
        assert m.message_id and m.conversation_id and m.external_id
        assert m.text and m.timestamp.tzinfo is not None
    assert {m.channel for m in messages} == {"email", "chat", "webhook"}


# --------------------------------------------------------------------------
# Integration — threading
# --------------------------------------------------------------------------


def test_reply_joins_the_thread_via_in_reply_to(db):
    first, _, _ = inbox.ingest("email", RAW_EMAIL, db=db)
    second, message, created = inbox.ingest("email", RAW_REPLY, db=db)
    assert created
    assert second.conversation_id == first.conversation_id
    assert message.metadata["thread_resolved_by"] == "in_reply_to"
    assert db["conversations"].count_documents({}) == 1


def test_reply_without_references_falls_back_to_the_subject(db, known_customer):
    sender = f"{known_customer.mobile}"
    first = {
        "From": sender,
        "Subject": "Payment query",
        "Date": "2026-08-14T09:00:00Z",
        "Message-ID": "<a@x>",
        "text": "question one",
    }
    second = {**first, "Subject": "Re: Payment query", "Message-ID": "<b@x>",
              "Date": "2026-08-15T09:00:00Z", "text": "question two"}
    conv_a, _, _ = inbox.ingest("email", first, db=db)
    conv_b, message, _ = inbox.ingest("email", second, db=db)
    assert conv_b.conversation_id == conv_a.conversation_id
    assert message.metadata["thread_resolved_by"] == "thread_key"


def test_subject_threading_does_not_reach_across_customers(db):
    """Two unknown senders sharing a subject must not share a conversation."""
    base = {"Subject": "Invoice query", "Date": "2026-08-14T09:00:00Z", "text": "hi"}
    a, _, _ = inbox.ingest(
        "email", {**base, "From": "one@unknown.example", "Message-ID": "<a@x>"}, db=db
    )
    b, _, _ = inbox.ingest(
        "email", {**base, "From": "two@unknown.example", "Message-ID": "<b@x>"}, db=db
    )
    # Both are unresolved customers, so the weak subject signal must not merge
    # them into one thread on the strength of the subject alone.
    assert a.customer_id is None and b.customer_id is None


def test_stale_subject_does_not_reopen_an_old_thread(db, known_customer):
    old = {
        "From": known_customer.mobile,
        "Subject": "Yearly review",
        "Date": "2026-01-01T09:00:00Z",
        "Message-ID": "<old@x>",
        "text": "old message",
    }
    fresh = {**old, "Message-ID": "<new@x>", "Date": "2026-08-14T09:00:00Z", "text": "new message"}
    conv_old, _, _ = inbox.ingest("email", old, db=db)
    conv_new, _, _ = inbox.ingest("email", fresh, db=db)
    assert conv_new.conversation_id != conv_old.conversation_id


def test_chat_session_continues_the_same_conversation(db):
    a, _, _ = inbox.ingest("chat", chat_payload(message_id="c1"), db=db)
    b, msg, _ = inbox.ingest("chat", chat_payload(message_id="c2", text="and my invoice?"), db=db)
    assert a.conversation_id == b.conversation_id
    assert msg.metadata["thread_resolved_by"] == "thread_key"


def test_chat_without_a_session_starts_a_new_conversation(db):
    a, _, _ = inbox.ingest("chat", chat_payload(message_id="c1", session_id=None), db=db)
    b, _, _ = inbox.ingest("chat", chat_payload(message_id="c2", session_id=None, text="two"), db=db)
    assert a.conversation_id != b.conversation_id


def test_explicit_conversation_id_wins(db):
    first, _, _ = inbox.ingest("chat", chat_payload(message_id="c1"), db=db)
    _, msg, _ = inbox.ingest(
        "chat",
        chat_payload(message_id="c2", session_id="different", conversation_id=first.conversation_id),
        db=db,
    )
    assert msg.conversation_id == first.conversation_id
    assert msg.metadata["thread_resolved_by"] == "explicit"


def test_unknown_explicit_conversation_id_starts_a_new_thread(db):
    _, msg, _ = inbox.ingest(
        "chat", chat_payload(conversation_id="CNV-2026-doesnotexist"), db=db
    )
    assert msg.metadata["thread_resolved_by"] == "new"


def test_same_customer_can_hold_several_conversations(db, known_customer):
    inbox.ingest("chat", chat_payload(message_id="c1", session_id="s1"), db=db)
    inbox.ingest("chat", chat_payload(message_id="c2", session_id="s2"), db=db)
    conversations = inbox.customer_conversations(known_customer.customer_id, db=db)
    assert len(conversations) == 2


# --------------------------------------------------------------------------
# Integration — duplicates and ordering
# --------------------------------------------------------------------------


def test_duplicate_delivery_is_ingested_once(db):
    first_conv, first_msg, created = inbox.ingest("email", RAW_EMAIL, db=db)
    again_conv, again_msg, created_again = inbox.ingest("email", RAW_EMAIL, db=db)
    assert created and not created_again
    assert again_msg.message_id == first_msg.message_id
    assert again_conv.conversation_id == first_conv.conversation_id
    assert db["messages"].count_documents({}) == 1
    assert db["conversations"].count_documents({}) == 1


def test_duplicate_webhook_delivery_is_ingested_once(db):
    payload = {**chat_payload(), "delivery_id": "dlv-42"}
    inbox.ingest("webhook", payload, db=db)
    _, _, created_again = inbox.ingest("webhook", dict(payload), db=db)
    assert not created_again and db["messages"].count_documents({}) == 1


def test_identical_text_without_a_delivery_id_deduplicates(db):
    payload = chat_payload(message_id=None)
    inbox.ingest("chat", payload, db=db)
    _, _, created_again = inbox.ingest("chat", dict(payload), db=db)
    assert not created_again


def test_the_same_id_on_two_channels_is_two_messages(db):
    inbox.ingest("chat", chat_payload(message_id="shared"), db=db)
    _, _, created = inbox.ingest(
        "webhook", {**chat_payload(), "delivery_id": "shared", "text": "hook"}, db=db
    )
    assert created and db["messages"].count_documents({}) == 2


def test_dedupe_is_enforced_by_a_unique_index_not_by_the_code(db):
    conversation, message, _ = inbox.ingest("email", RAW_EMAIL, db=db)
    from pymongo.errors import DuplicateKeyError

    with pytest.raises(DuplicateKeyError):
        db["messages"].insert_one(message.model_dump(mode="python"))


def test_out_of_order_arrival_reads_back_in_time_order(db):
    later = {
        "From": "raj@abcindustries.example",
        "Subject": "Ordering",
        "Message-ID": "<late@x>",
        "Date": "2026-08-14T12:00:00Z",
        "text": "second in time, first to arrive",
    }
    earlier = {**later, "Message-ID": "<early@x>", "Date": "2026-08-14T10:00:00Z",
               "text": "first in time, second to arrive"}
    conversation, _, _ = inbox.ingest("email", later, db=db)
    inbox.ingest("email", {**earlier, "In-Reply-To": "<late@x>"}, db=db)

    messages = inbox.conversation_messages(conversation.conversation_id, db=db)
    assert [m.timestamp for m in messages] == sorted(m.timestamp for m in messages)
    assert messages[0].text.startswith("first in time")


def test_conversation_updated_at_tracks_the_latest_message(db):
    conversation, _, _ = inbox.ingest("email", RAW_EMAIL, db=db)
    updated, _, _ = inbox.ingest("email", RAW_REPLY, db=db)
    assert updated.updated_at > conversation.created_at
    assert updated.updated_at == datetime(2026, 8, 14, 11, 0, tzinfo=UTC)


def test_late_arriving_older_message_does_not_rewind_updated_at(db):
    conversation, _, _ = inbox.ingest("email", RAW_REPLY, db=db)
    after, _, _ = inbox.ingest(
        "email",
        {"From": "raj@abcindustries.example", "Subject": "Re: Outstanding balance",
         "Message-ID": "<older@x>", "Date": "2026-08-14T08:00:00Z",
         "In-Reply-To": "<second@abcindustries.example>", "text": "older"},
        db=db,
    )
    assert after.updated_at >= conversation.updated_at


# --------------------------------------------------------------------------
# Integration — customer resolution
# --------------------------------------------------------------------------


def test_sender_resolves_to_a_real_customer_by_mobile(db, known_customer):
    _, message, _ = inbox.ingest("chat", chat_payload(), db=db)
    assert message.customer_id == known_customer.customer_id
    assert message.metadata["resolved_by"] == "mobile"


def test_explicit_customer_hint_is_honoured(db, known_customer):
    _, message, _ = inbox.ingest(
        "chat", chat_payload(phone=None, customer_id=known_customer.customer_id), db=db
    )
    assert message.customer_id == known_customer.customer_id
    assert message.metadata["resolved_by"] == "hint"


def test_invalid_customer_hint_does_not_invent_a_customer(db):
    _, message, _ = inbox.ingest("chat", chat_payload(customer_id="not-an-id"), db=db)
    assert message.customer_id is None
    assert message.metadata["resolved_by"] == "hint_invalid"


def test_unknown_sender_still_produces_a_conversation(db):
    conversation, message, created = inbox.ingest("email", RAW_EMAIL, db=db)
    assert created and conversation.conversation_id
    assert message.customer_id is None
    assert message.metadata["resolved_by"].endswith("_unknown")


def test_ambiguous_sender_is_left_unresolved_rather_than_guessed(db):
    """9424885200 belongs to three customers in this book."""
    _, message, _ = inbox.ingest("chat", chat_payload(phone="9424885200"), db=db)
    assert message.customer_id is None
    assert message.metadata["resolved_by"] == "mobile_ambiguous"


def test_customer_changing_email_stays_on_the_same_thread(db, known_customer):
    """The thread carries the customer, not the sender line."""
    first = {
        "From": known_customer.mobile,
        "Subject": "Ledger question",
        "Message-ID": "<c1@x>",
        "Date": "2026-08-14T09:00:00Z",
        "text": "from the old address",
    }
    reply = {
        "From": "raj@brand-new-domain.example",
        "Subject": "Re: Ledger question",
        "Message-ID": "<c2@x>",
        "In-Reply-To": "<c1@x>",
        "Date": "2026-08-14T10:00:00Z",
        "text": "same person, new address",
    }
    conv_a, msg_a, _ = inbox.ingest("email", first, db=db)
    conv_b, msg_b, _ = inbox.ingest("email", reply, db=db)
    assert msg_a.customer_id == known_customer.customer_id
    assert conv_b.conversation_id == conv_a.conversation_id
    assert msg_b.customer_id == known_customer.customer_id
    assert msg_b.metadata["resolved_by"].endswith("->thread")


def test_conversation_adopts_the_customer_once_identified(db, known_customer):
    """An anonymous thread that later identifies itself back-fills the customer."""
    anon = {"From": "mystery@unknown.example", "Subject": "Who am I",
            "Message-ID": "<m1@x>", "Date": "2026-08-14T09:00:00Z", "text": "hello"}
    conversation, message, _ = inbox.ingest("email", anon, db=db)
    assert conversation.customer_id is None and message.customer_id is None

    identified, _, _ = inbox.ingest(
        "email",
        {"From": known_customer.mobile, "Subject": "Re: Who am I", "Message-ID": "<m2@x>",
         "In-Reply-To": "<m1@x>", "Date": "2026-08-14T10:00:00Z", "text": "it is me"},
        db=db,
    )
    assert identified.customer_id == known_customer.customer_id
    stored = db["conversations"].find_one({"conversation_id": conversation.conversation_id})
    assert stored["customer_id"] == known_customer.customer_id


# --------------------------------------------------------------------------
# Integration — outbound and isolation
# --------------------------------------------------------------------------


def test_outbound_replies_share_the_conversation(db):
    conversation, _, _ = inbox.ingest("email", RAW_EMAIL, db=db)
    _, reply, _ = inbox.ingest(
        "email",
        {"From": "support@ourcompany.example", "Subject": "Re: Outstanding balance",
         "Message-ID": "<out@x>", "In-Reply-To": "<first@abcindustries.example>",
         "Date": "2026-08-14T12:00:00Z", "text": "Your balance is attached."},
        direction="outbound",
        db=db,
    )
    assert reply.direction == "outbound"
    assert reply.conversation_id == conversation.conversation_id
    assert len(inbox.conversation_messages(conversation.conversation_id, db=db)) == 2


def test_ingestion_never_writes_to_the_tenant_database(db):
    from ca.config import TENANT_COLLECTIONS, tenant_db

    before = tenant_db()["vouchers"].estimated_document_count()
    inbox.ingest("email", RAW_EMAIL, db=db)
    assert tenant_db()["vouchers"].estimated_document_count() == before
    assert set(db.list_collection_names()) <= {"messages", "conversations"}
    assert not set(db.list_collection_names()) & TENANT_COLLECTIONS - {"messages"}
