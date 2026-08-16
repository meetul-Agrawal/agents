"""Dev-only server for the Phase 2/3 manual eval UI.

    uv run uvicorn scripts.ui_server:app --reload
"""
from __future__ import annotations

import json
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ca.config import CUSTOMER_GROUP_PATH_RE, app_db, tenant_db
from ca.contracts import Conversation, Message, new_id, utcnow
from ca import inbox, orchestrator

_UI = pathlib.Path(__file__).parent.parent / "ui" / "index.html"
_REVIEWED = pathlib.Path("evals/datasets/routing/reviewed.jsonl")


@asynccontextmanager
async def lifespan(app):
    inbox.ensure_indexes()
    yield


app = FastAPI(lifespan=lifespan)


def _clean(doc: dict) -> dict:
    return {
        k: (v.isoformat() if isinstance(v, datetime) else v)
        for k, v in doc.items()
        if k != "_id"
    }


# ── Customers ─────────────────────────────────────────────────────────────────

@app.get("/api/customers")
def list_customers():
    docs = tenant_db()["ledgers"].find(
        {"groupPath": {"$regex": CUSTOMER_GROUP_PATH_RE}},
        {"_id": 1, "ledgerName": 1},
    ).limit(500)
    return [{"customer_id": str(d["_id"]), "display_name": d.get("ledgerName", "")} for d in docs]


# ── Conversations ─────────────────────────────────────────────────────────────

@app.get("/api/conversations")
def list_conversations():
    docs = app_db()["conversations"].find({}).sort("updated_at", -1).limit(100)
    return [_clean(d) for d in docs]


class NewConvReq(BaseModel):
    customer_id: str | None = None


@app.post("/api/conversations")
def create_conversation(body: NewConvReq):
    conv = Conversation(customer_id=body.customer_id, channel="chat")
    app_db()["conversations"].insert_one(conv.model_dump(mode="python"))
    return conv.model_dump(mode="json")


@app.get("/api/conversations/{conv_id}")
def get_messages(conv_id: str):
    return [m.model_dump(mode="json") for m in inbox.conversation_messages(conv_id)]


# ── Classify ──────────────────────────────────────────────────────────────────

class ClassifyReq(BaseModel):
    message: str
    customer_id: str | None = None
    conversation_id: str
    classifier: str = "llm"  # "llm" | "rules"


@app.post("/api/classify")
def classify(body: ClassifyReq):
    mid = new_id("message")
    msg = Message(
        message_id=mid,
        external_id=mid,
        conversation_id=body.conversation_id,
        customer_id=body.customer_id,
        channel="chat",
        direction="inbound",
        text=body.message,
    )
    try:
        app_db()["messages"].insert_one(msg.model_dump(mode="python"))
    except Exception:
        pass  # duplicate on retry

    app_db()["conversations"].update_one(
        {"conversation_id": body.conversation_id},
        {"$set": {"updated_at": utcnow()}},
    )

    fn = orchestrator.classify_rules if body.classifier == "rules" else orchestrator.classify_llm
    state = orchestrator.handle(
        body.message,
        channel="chat",
        customer_id=body.customer_id,
        conversation_id=body.conversation_id,
        classifier=fn,
    )

    if state.final_response:
        rid = new_id("message")
        resp = Message(
            message_id=rid,
            external_id=rid,
            conversation_id=body.conversation_id,
            customer_id=body.customer_id,
            channel="chat",
            direction="outbound",
            text=state.final_response,
        )
        try:
            app_db()["messages"].insert_one(resp.model_dump(mode="python"))
        except Exception:
            pass

    result = orchestrator.summarize(state)
    result["intents_detail"] = [i.model_dump() for i in state.intents]
    result["message_id"] = mid
    return result


# ── Label ─────────────────────────────────────────────────────────────────────

class LabelReq(BaseModel):
    case_id: str = ""
    input: str
    customer_id: str | None = None
    context: dict[str, Any] = {}
    expected: dict[str, Any] = {}
    label: str
    correct_fields: list[str] = []
    incorrect_fields: list[str] = []


@app.post("/api/label")
def save_label(body: LabelReq):
    _REVIEWED.parent.mkdir(parents=True, exist_ok=True)
    record = body.model_dump()
    if not record["case_id"]:
        record["case_id"] = new_id("eval_case")
    record["reviewed_at"] = utcnow().isoformat()
    with _REVIEWED.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return {"ok": True, "case_id": record["case_id"]}


# ── UI ────────────────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(str(_UI))
