"""Dev-only server for the Phase 2/3 manual eval UI.

    uv run uvicorn scripts.ui_server:app --reload
"""
from __future__ import annotations

import json
import pathlib
from contextlib import asynccontextmanager
from datetime import date, datetime
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

# compute_portfolio_snapshot() is a ~3s company-wide scan; the dashboard polls
# every 15s, so cache it instead of re-scanning `vouchers` on every poll.
_SNAPSHOT_TTL_SECONDS = 300
_snapshot_cache: dict[str, Any] = {"data": None, "at": 0.0}
_HITL_AGENTS = {"sa3_dispute", "sa4_approval", "sa6_return"}

# What each agent's event card shows: label (with avatar initials baked in),
# accent color (matches the existing tag-metric palette), and which HITL tab
# a "view" quick-button should jump to.
_AGENT_EVENT_META = {
    "sa2_recovery": {"agent_label": "Payment Promise Agent (SA-2)", "color": "indigo", "tab": "promises"},
    "sa3_dispute": {"agent_label": "Dispute Agent (SA-3)", "color": "rose", "tab": "disputes"},
    "sa4_approval": {"agent_label": "Approval Agent (SA-4)", "color": "amber", "tab": "approvals"},
}


def _company_snapshot():
    import time as _time
    from ca import customer360 as c3

    if _time.time() - _snapshot_cache["at"] > _SNAPSHOT_TTL_SECONDS:
        _snapshot_cache["data"] = c3.compute_portfolio_snapshot()
        _snapshot_cache["at"] = _time.time()
    return _snapshot_cache["data"]


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


# ── Dashboard Summary ─────────────────────────────────────────────────────────

@app.get("/api/dashboard/summary")
def get_dashboard_summary():
    adb = app_db()
    tdb = tenant_db()
    snap = _company_snapshot()

    # Company Master Info
    comp_doc = tdb["companies"].find_one() or {}
    company_name = comp_doc.get("companyName") or "GANGWAL FLOUR FOODS LLP 26-27"

    # HITL Pending counts across company
    approvals_cnt = adb["approvals"].count_documents({"status": "pending"})
    disputes_cnt = adb["cases"].count_documents({"status": {"$in": ["open", "investigating", "waiting"]}})
    promises_cnt = adb["payment_promises"].count_documents({"status": "promised"})

    # Omnichannel company message activity — feeds agent_counts only, the
    # per-message text isn't shown anymore (see agent event feed below).
    msgs = list(adb["messages"].find({}).sort("timestamp", -1).limit(30))
    agent_counts: dict[str, int] = {
        "sa1_general": 0, "sa2_recovery": 0, "sa3_dispute": 0,
        "sa4_approval": 0, "sa5_order": 0, "sa6_return": 0,
        "sa7_health": 0, "sa8_call_prep": 0,
    }
    for m in msgs:
        cls_data = (m.get("metadata") or {}).get("classification") or {}
        for a in cls_data.get("agents") or []:
            agent_counts[a] = agent_counts.get(a, 0) + 1

    activity_stream = _agent_event_feed(adb)

    # High Exposure Portfolio Debtors — real top-N by company-wide outstanding,
    # not per-customer detail, so only what compute_portfolio_snapshot() knows.
    _RISK_BY_BUCKET = {"90+": "critical", "61-90": "high", "31-60": "medium", "0-30": "low"}
    portfolio_debtors = [
        {
            "customer_id": d["customer_id"],
            "customer_name": d["customer_name"],
            "risk_level": _RISK_BY_BUCKET.get(d["ageing_bucket"], "medium"),
            "outstanding_formatted": f"₹{d['outstanding']:,.2f}",
            "open_bills": d["open_bills"],
            "ageing_bucket": f"{d['ageing_bucket']} Days",
        }
        for d in snap.top_debtors
    ]

    tot_receivables = snap.total_outstanding
    tot_collected = snap.total_receipted

    # Multi-Agent Fleet Status
    fleet_agents = [
        {"id": "sa1_general", "name": "SA-1: General & Inquiries", "role": "Ledger statements, balances & price lookups", "status": "active", "runs": agent_counts.get("sa1_general", 18), "mode": "Autonomous"},
        {"id": "sa2_recovery", "name": "SA-2: Recovery & Commitments", "role": "Payment promise recording, claims & follow-ups", "status": "active", "runs": agent_counts.get("sa2_recovery", 12), "mode": "Autonomous"},
        {"id": "sa3_dispute", "name": "SA-3: Dispute Resolution", "role": "Shortage claims, damaged goods & rate discrepancies", "status": "active", "runs": agent_counts.get("sa3_dispute", 5), "mode": "HITL Supervised"},
        {"id": "sa4_approval", "name": "SA-4: Financial Approvals", "role": "Special discount & credit limit authority checks", "status": "active", "runs": agent_counts.get("sa4_approval", 4), "mode": "Human Gated"},
        {"id": "sa5_order", "name": "SA-5: Order Processing", "role": "Standard repeat orders & catalog fulfillment", "status": "active", "runs": agent_counts.get("sa5_order", 3), "mode": "Autonomous"},
        {"id": "sa6_return", "name": "SA-6: Sales Returns", "role": "Physical returns & unsold inventory claims", "status": "active", "runs": agent_counts.get("sa6_return", 2), "mode": "HITL Supervised"},
        {"id": "sa7_health", "name": "SA-7: Account Health & Risk", "role": "Customer relationship & overdue risk scoring", "status": "active", "runs": agent_counts.get("sa7_health", 6), "mode": "Autonomous"},
        {"id": "sa8_call_prep", "name": "SA-8: Executive Call Prep", "role": "AI talking points, call scripts & objection tactics", "status": "active", "runs": agent_counts.get("sa8_call_prep", 4), "mode": "On Demand"},
    ]

    # Resolution rate from the same recent-activity window agent_counts already
    # covers: fraction of routed runs that didn't need a human-gated SA.
    total_runs = sum(agent_counts.values())
    hitl_runs = sum(agent_counts.get(a, 0) for a in _HITL_AGENTS)
    autonomous_rate = round(100 * (1 - hitl_runs / total_runs), 1) if total_runs else 0.0

    return {
        "company_info": {
            "name": company_name,
            "formal_name": comp_doc.get("basicCompantFormalName") or company_name,
            "total_debtor_accounts": snap.debtor_accounts,
            "total_catalog_items": tdb["stockItems"].count_documents({}),
            "total_vouchers_indexed": tdb["vouchers"].count_documents({}),
            "active_channels": ["WhatsApp", "Chat", "Email", "Phone Desk"],
        },
        "metrics": {
            "total_receivables_formatted": f"₹{tot_receivables:,.2f}",
            "open_invoices_count": snap.open_bill_count,
            "historical_collected_formatted": f"₹{tot_collected:,.2f}",
            "avg_settlement_days": snap.avg_settlement_days,
            "autonomous_resolution_rate": autonomous_rate,
            "hitl_pending_total": approvals_cnt + disputes_cnt + promises_cnt,
            "approvals_pending": approvals_cnt,
            "disputes_open": disputes_cnt,
            "promises_active": promises_cnt,
        },
        "ageing_distribution": {b: f"₹{amt:,.2f}" for b, amt in snap.ageing.items()},
        "ageing_distribution_raw": snap.ageing,
        "fleet_agents": fleet_agents,
        "agent_workload": agent_counts,
        "activity_stream": activity_stream,
        "portfolio_debtors": portfolio_debtors,
    }


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
    classifier: str = "llm"


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

    state = orchestrator.handle(
        body.message,
        channel="chat",
        customer_id=body.customer_id,
        conversation_id=body.conversation_id,
        classifier=orchestrator.classify_llm,
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


def _labelize(key: str) -> str:
    return key.replace("_", " ").title()


def _agent_event_feed(adb, limit: int = 12) -> list[dict]:
    """Chat-style feed of what SA-2/3/4 actually decided — one card per case,
    approval or promise, newest first — instead of raw inbound message text."""
    cases = list(adb["cases"].find({}).sort("created_at", -1).limit(limit))
    approvals = list(adb["approvals"].find({}).sort("created_at", -1).limit(limit))
    promises = list(adb["payment_promises"].find({}).sort("updated_at", -1).limit(limit))

    cids = [d["customer_id"] for d in [*cases, *approvals, *promises]]
    names = _customer_names(cids)

    def cust(d):
        return names.get(d["customer_id"], "Trade Counterparty")

    # Tag colors reuse the existing tag-metric palette (tag-rose/amber/indigo/emerald).
    _PRIORITY_COLOR = {"critical": "rose", "high": "rose", "normal": "amber", "low": "emerald"}
    _CASE_STATUS_COLOR = {"open": "amber", "investigating": "amber", "waiting": "amber", "resolved": "emerald", "closed": "emerald"}
    _APPROVAL_STATUS_COLOR = {"pending": "amber", "approved": "emerald", "rejected": "rose", "expired": "rose"}

    events = []
    for c in cases:
        meta = _AGENT_EVENT_META["sa3_dispute"]
        tags = [
            {"label": c["priority"].upper(), "color": _PRIORITY_COLOR.get(c["priority"], "amber")},
            {"label": _labelize(c["status"]), "color": _CASE_STATUS_COLOR.get(c["status"], "amber")},
        ]
        detail = f"Reason: {c['title']}"
        if c.get("resolution"):
            detail += f" — Resolution: {c['resolution']}"
        events.append({
            "event_id": c["case_id"], "agent": "sa3_dispute", **meta,
            "customer_name": cust(c), "timestamp": c["created_at"],
            "headline": f"Case {c['case_id']} opened for {cust(c)}",
            "detail": detail, "tags": tags,
            "ref_type": meta["tab"], "ref_id": c["case_id"],
        })
    for a in approvals:
        meta = _AGENT_EVENT_META["sa4_approval"]
        amt = f" of ₹{a['amount']:,.0f}" if a.get("amount") is not None else ""
        detail = f"Reason: {_labelize(a['type'])}{amt}" + (f" — {a['recommendation']}" if a.get("recommendation") else "")
        tags = [{"label": _labelize(a["status"]), "color": _APPROVAL_STATUS_COLOR.get(a["status"], "amber")}]
        if a.get("decided_by"):
            detail += f" — Decided by {a['decided_by']}"
        events.append({
            "event_id": a["approval_id"], "agent": "sa4_approval", **meta,
            "customer_name": cust(a), "timestamp": a["created_at"],
            "headline": f"Approval {a['approval_id']} requested for {cust(a)}",
            "detail": detail, "tags": tags,
            "ref_type": meta["tab"], "ref_id": a["approval_id"],
        })
    for p in promises:
        meta = _AGENT_EVENT_META["sa2_recovery"]
        modified = p.get("updated_at") and p["updated_at"] > p["created_at"]
        headline = (
            f"{cust(p)} modified their commitment — now ₹{p['amount']:,.0f} by {p['due_date']}"
            if modified else
            f"{cust(p)} committed to pay ₹{p['amount']:,.0f} by {p['due_date']}"
        )
        overdue = p["status"] == "promised" and date.fromisoformat(p["due_date"]) < date.today()
        tags = [{"label": "OVERDUE" if overdue else _labelize(p["status"]), "color": "rose" if overdue else "indigo"}]
        detail = f"Status: {_labelize(p['status'])}"
        if p.get("paid_amount"):
            detail += f" — Paid so far: ₹{p['paid_amount']:,.0f}"
        events.append({
            "event_id": p["promise_id"], "agent": "sa2_recovery", **meta,
            "customer_name": cust(p), "timestamp": p["updated_at"] or p["created_at"],
            "headline": headline, "detail": detail, "tags": tags,
            "ref_type": meta["tab"], "ref_id": p["promise_id"],
        })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    for e in events[:limit]:
        e["timestamp"] = e["timestamp"].isoformat() if isinstance(e["timestamp"], datetime) else str(e["timestamp"])
    return events[:limit]


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


# ── Payment Promises ─────────────────────────────────────────────────────────

@app.get("/api/promises")
def list_promises(status: str = "all"):
    query = {} if status == "all" else {"status": status}
    docs = list(app_db()["payment_promises"].find(query).sort("due_date", 1).limit(200))
    names = _customer_names([d["customer_id"] for d in docs])
    return [{**_clean(d), "customer_name": names.get(d["customer_id"], d["customer_id"])} for d in docs]


class PromiseStatusReq(BaseModel):
    status: str
    paid_amount: float | None = None
    note: str = ""


@app.post("/api/promises/{promise_id}/status")
def update_promise_status(promise_id: str, body: PromiseStatusReq):
    db = app_db()
    doc = db["payment_promises"].find_one({"promise_id": promise_id})
    if not doc:
        return {"ok": False, "error": "promise not found"}
    update_fields = {"status": body.status, "updated_at": services.utcnow().isoformat()}
    if body.paid_amount is not None:
        update_fields["paid_amount"] = float(body.paid_amount)
    db["payment_promises"].update_one({"promise_id": promise_id}, {"$set": update_fields})
    updated = db["payment_promises"].find_one({"promise_id": promise_id})

    event_type_map = {
        "missed": "PAYMENT_PROMISE_MISSED",
        "paid": "PAYMENT_RECEIVED",
        "partial": "PAYMENT_PARTIAL",
        "cancelled": "PAYMENT_PROMISE_MODIFIED",
        "promised": "PAYMENT_PROMISE_MODIFIED",
    }
    evt = event_type_map.get(body.status, "PAYMENT_PROMISE_MODIFIED")
    services.record_event(
        doc["customer_id"], evt, "ops",
        conversation_id=doc.get("conversation_id"),
        payload={"promise_id": promise_id, "status": body.status, "note": body.note},
    )
# ── Call Prep ────────────────────────────────────────────────────────────────

@app.get("/api/customers/{customer_id}/call-prep")
def get_customer_call_prep(customer_id: str, conversation_id: str | None = None):
    from ca import call_prep
    brief = call_prep.build_call_prep(customer_id, conversation_id=conversation_id)
    return brief.model_dump(mode="json")


# ── Vouchers (raw MongoDB browser) ──────────────────────────────────────────

@app.get("/api/customers/{customer_id}/vouchers")
def list_customer_vouchers(customer_id: str, category: str = "all"):
    from ca import customer360
    customer = customer360.get_customer(customer_id)
    vs = customer360.fetch_vouchers(customer.ledger_name)
    docs = vs.vouchers if category == "all" else vs.by_category(category)
    return [customer360._voucher_summary(v, vs.ledger_name) for v in docs]


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
