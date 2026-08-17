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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ca.config import CUSTOMER_GROUP_PATH_RE, app_db, tenant_db
from ca.contracts import Conversation, Message, new_id, utcnow
from ca import inbox, orchestrator, sa3_dispute, sa4_approval, services

_DIST = pathlib.Path(__file__).parent.parent / "ui" / "dist"
# Labels live outside evals/datasets/ on purpose: the eval loader globs every
# *.jsonl under datasets/ as an EvalCase, and a label record is a different shape.
_REVIEWED = pathlib.Path("evals/reviewed/routing.jsonl")


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
    # ponytail: whole debtor list, filtered client-side by the search box. Add a
    # `?q=` server filter if a tenant ever has more debtors than this cap.
    docs = tenant_db()["ledgers"].find(
        {"groupPath": {"$regex": CUSTOMER_GROUP_PATH_RE}},
        {"_id": 1, "ledgerName": 1},
    ).limit(5000)
    rows = [{"customer_id": str(d["_id"]), "display_name": d.get("ledgerName", "")} for d in docs]
    rows.sort(key=lambda r: r["display_name"].lower())
    return rows


@app.get("/api/intents")
def list_intents():
    """Every intent the classifier can emit — the UI colours each green when the
    LLM picked it for a message, red when it did not."""
    return [
        {"name": name, "agent": spec.agent}
        for name, spec in sorted(orchestrator.INTENT_CATALOG.items())
    ]


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
    result["classifier"] = body.classifier

    # Stash the classification on the message itself, so clicking it later shows
    # how the LLM read it — no separate store, it rides in the message metadata.
    app_db()["messages"].update_one(
        {"message_id": mid}, {"$set": {"metadata.classification": result}}
    )
    return result


# ── Approvals & Disputes ──────────────────────────────────────────────────────
# The human side of the approval gateway: SA-4 raises a pending approval and
# SA-3 opens a case, both write-only from the agent side (`services.py` never
# lets an agent set anything but "pending"/"open"). This is the one place a
# human decision is recorded, and the one place `decide_approval`/
# `resolve_case` are ever called.

def _customer_names(customer_ids: list[str]) -> dict[str, str]:
    from bson import ObjectId
    from bson.errors import InvalidId

    oids = []
    for cid in set(customer_ids):
        try:
            oids.append(ObjectId(cid))
        except (InvalidId, TypeError):
            pass
    if not oids:
        return {}
    docs = tenant_db()["ledgers"].find({"_id": {"$in": oids}}, {"ledgerName": 1})
    return {str(d["_id"]): d.get("ledgerName", "") for d in docs}


@app.get("/api/approvals")
def list_approvals(status: str = "pending"):
    query = {} if status == "all" else {"status": status}
    docs = list(app_db()["approvals"].find(query).sort("created_at", -1).limit(200))
    names = _customer_names([d["customer_id"] for d in docs])
    return [{**_clean(d), "customer_name": names.get(d["customer_id"], d["customer_id"])} for d in docs]


@app.get("/api/disputes")
def list_disputes(status: str = "open"):
    query = {} if status == "all" else {"status": status}
    docs = list(app_db()["cases"].find(query).sort("created_at", -1).limit(200))
    names = _customer_names([d["customer_id"] for d in docs])
    return [{**_clean(d), "customer_name": names.get(d["customer_id"], d["customer_id"])} for d in docs]


class DecideReq(BaseModel):
    approved: bool
    decided_by: str = "ops"
    note: str = ""


@app.post("/api/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, body: DecideReq):
    approval = services.decide_approval(approval_id, body.approved, body.decided_by)
    if approval is None:
        return {"ok": False, "error": "approval not found"}

    text = sa4_approval.decision_message(approval, body.approved, note=body.note)
    sent = services.send_customer_message(approval.customer_id, approval.conversation_id, text)
    services.record_event(
        approval.customer_id, "APPROVAL_APPROVED" if body.approved else "APPROVAL_REJECTED",
        "ops", conversation_id=approval.conversation_id,
        payload={"approval_id": approval.approval_id, "decided_by": body.decided_by},
    )
    return {"ok": True, "approval": approval.model_dump(mode="json"),
            "message_sent": sent is not None, "message_text": text}


class ResolveReq(BaseModel):
    outcome: str  # "solved" | "dropped"
    resolution: str = ""
    note: str = ""


@app.post("/api/disputes/{case_id}/resolve")
def resolve_dispute(case_id: str, body: ResolveReq):
    if body.outcome not in services.RESOLUTION_STATUS:
        return {"ok": False, "error": f"outcome must be one of {list(services.RESOLUTION_STATUS)}"}

    case = services.resolve_case(case_id, body.resolution, outcome=body.outcome)
    if case is None:
        return {"ok": False, "error": "case not found"}

    text = sa3_dispute.resolution_message(case, body.outcome, note=body.note)
    sent = services.send_customer_message(case.customer_id, case.conversation_id, text)
    services.record_event(
        case.customer_id, "DISPUTE_CLOSED", "ops", conversation_id=case.conversation_id,
        payload={"case_id": case.case_id, "outcome": body.outcome},
    )
    return {"ok": True, "case": case.model_dump(mode="json"),
            "message_sent": sent is not None, "message_text": text}


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


# ── UI (Vite build) ───────────────────────────────────────────────────────────
# Dev: run `npm run dev` in ui/ (Vite proxies /api → this server).
# Prod: run `npm run build` in ui/, then start this server.

@app.get("/")
def index():
    return FileResponse(str(_DIST / "index.html"))

# Vite puts hashed JS/CSS chunks here after build.
_assets = _DIST / "assets"
if _assets.exists():
    app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")
