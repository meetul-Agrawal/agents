"""Call Preparation Engine for B2B Receivables & Sales Accounts.

Synthesizes deterministic Customer-360 data from MongoDB (outstanding dues,
ageing buckets, open bills, payment behaviour, active promises, open disputes,
recent purchase line items) and conversation chat history to generate a
high-impact call preparation brief and dialogue script.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from . import customer360 as c3
from .config import app_db
from .contracts import ModelOutput
from .llm import complete_structured, available as llm_available


def _inr(amount: float) -> str:
    return f"₹{amount:,.2f}"


def _fmt_date(val: Any) -> str:
    if val is None:
        return "N/A"
    if isinstance(val, date):
        return val.strftime("%d %b %Y")
    return str(val)[:10]


class TalkingPoint(BaseModel):
    category: str = Field(description="E.g., Overdue Invoices, Payment Promise, Dispute Status, Sales")
    priority: Literal["high", "medium", "low"] = "high"
    point: str = Field(description="Concise bullet point to state or ask")
    detail: str = Field(description="Supporting grounded data / context")


class ObjectionHandling(BaseModel):
    likely_objection: str = Field(description="Likely excuse or objection the customer might raise")
    recommended_response: str = Field(description="Grounded, diplomatic, and firm response technique")


class CallPrepBrief(ModelOutput):
    customer_id: str = ""
    customer_name: str = ""
    account_summary: str = ""
    total_outstanding_formatted: str = ""
    open_bills_count: int = 0
    oldest_bills_summary: str = ""
    ageing_summary: str = ""
    payment_behaviour_summary: str = ""
    active_promise_summary: str = ""
    open_dispute_summary: str = ""
    recent_chat_summary: str = ""

    talking_points: list[TalkingPoint] = Field(default_factory=list)
    call_script_hinglish: str = ""
    call_script_english: str = ""
    objection_handling: list[ObjectionHandling] = Field(default_factory=list)
    recommended_target_commitment: str = ""
    notes_for_agent: list[str] = Field(default_factory=list)


CALL_PREP_SYSTEM_PROMPT = """You are an expert B2B receivables and collections strategist preparing an account manager/collector for a high-priority phone call with a business client.

You will receive:
1. Customer Account Profile & MongoDB ground truth (Outstanding amount, ageing breakdown, oldest open invoices, payment track record, active payment promises, open disputes, recent purchase history).
2. Conversation Chat History (what the customer and agent recently discussed, claimed, or promised in WhatsApp/chat).

Generate a highly strategic, professional, and grounded Call Preparation Brief with:
- `account_summary`: 2-sentence executive summary of the account status and risk level.
- `talking_points`: 3-5 prioritized agenda items to cover during the call.
- `call_script_hinglish`: A natural, conversational, and polite yet firm phone call dialogue in Hindi/Hinglish (common in Indian trade calls). Cover: Opening greeting -> Acknowledging recent chat messages -> Discussing oldest pending bills by specific invoice numbers & dates -> Following up on promises -> Confirming clear next steps.
- `call_script_english`: The equivalent dialogue in clear professional English.
- `objection_handling`: 2-3 realistic objections with tactical, grounded responses.
- `recommended_target_commitment`: Specific amount and deadline to secure during the call.
- `notes_for_agent`: 2-3 behavioral dos and don'ts for the caller.

CRITICAL RULES:
- Never hallucinate amounts, invoice numbers, or dates. Use the exact numbers provided in the prompt.
- Address the customer respectfully using their business name.
"""


def build_call_prep(customer_id: str, conversation_id: str | None = None) -> CallPrepBrief:
    """Builds a complete call preparation brief using MongoDB records and chat history."""
    customer = c3.get_customer(customer_id)
    cust_name = customer.display_name if customer else customer_id

    # 1. Customer 360 Records
    outstanding = c3.get_outstanding(customer_id)
    pay_hist = c3.get_payment_history(customer_id)
    sales_hist = c3.get_sales_history(customer_id, limit=5)

    db = app_db()
    promises = list(db["payment_promises"].find({"customer_id": customer_id}).sort("due_date", -1).limit(5))
    cases = list(db["cases"].find({"customer_id": customer_id}).sort("created_at", -1).limit(5))
    approvals = list(db["approvals"].find({"customer_id": customer_id}).sort("created_at", -1).limit(5))

    # 2. Omnichannel Chat history across ALL customer conversations
    chat_lines: list[str] = []
    conv_docs = list(db["conversations"].find({"customer_id": customer_id}).sort("updated_at", -1))
    cids = [c["conversation_id"] for c in conv_docs if "conversation_id" in c]

    all_msgs = list(db["messages"].find({
        "$or": [
            {"customer_id": customer_id},
            {"conversation_id": {"$in": cids}},
        ]
    }).sort("timestamp", 1))

    for m in all_msgs[-40:]:  # Take recent messages across all conversation threads
        sender = "Customer" if m.get("direction") == "inbound" else "Agent"
        text = m.get("text", "").strip()
        ts = _fmt_date(m.get("timestamp"))
        chat_lines.append(f"[{ts}] {sender}: \"{text}\"")

    # Format data blocks
    tot_out_str = _inr(outstanding.outstanding)
    open_bills_cnt = outstanding.open_bill_count

    oldest_bills_strs = []
    for b in outstanding.open_bills[:4]:
        oldest_bills_strs.append(f"{b.voucher_number} dated {_fmt_date(b.invoice_date)}: {_inr(b.outstanding)}")
    oldest_bills_summary = "; ".join(oldest_bills_strs) if oldest_bills_strs else "No open invoices"

    ageing_parts = []
    for bucket, amt in outstanding.ageing.items():
        if amt > 0:
            ageing_parts.append(f"{bucket} days: {_inr(amt)}")
    ageing_summary = ", ".join(ageing_parts) if ageing_parts else "All current"

    pay_parts = []
    if hasattr(pay_hist, "receipt_count"):
        pay_parts.append(f"{pay_hist.receipt_count} receipts totalling {_inr(pay_hist.total_received)}")
        if pay_hist.last_receipt:
            pay_parts.append(f"last payment on {_fmt_date(pay_hist.last_receipt)}")
        if pay_hist.avg_days_to_settle is not None:
            pay_parts.append(f"avg settlement {pay_hist.avg_days_to_settle:.0f} days")
    pay_summary = ", ".join(pay_parts) if pay_parts else "No payment history recorded"

    active_p = next((p for p in promises if p.get("status") == "promised"), None)
    active_promise_summary = (
        f"Promised {_inr(active_p['amount'])} due on {_fmt_date(active_p['due_date'])}"
        if active_p else "No active promise"
    )

    open_cases = [c for c in cases if c.get("status") in ("open", "investigating", "waiting")]
    open_dispute_summary = (
        f"{len(open_cases)} open case(s): " + ", ".join(c.get("title", "") for c in open_cases[:2])
        if open_cases else "No open disputes"
    )

    recent_chat_str = "\n".join(chat_lines) if chat_lines else "No previous chat history in this thread."

    # If LLM not available, construct a rich deterministic brief
    if not llm_available():
        points = [
            TalkingPoint(
                category="Outstanding Balance",
                priority="high",
                point=f"Review total outstanding of {tot_out_str} across {open_bills_cnt} invoices.",
                detail=f"Overdue ageing: {ageing_summary}. Oldest bills: {oldest_bills_summary}",
            )
        ]
        if active_p:
            points.append(TalkingPoint(
                category="Payment Promise",
                priority="high",
                point=f"Follow up on promise of {_inr(active_p['amount'])} due on {_fmt_date(active_p['due_date'])}.",
                detail=f"Status: {active_p.get('status')}",
            ))
        if open_cases:
            points.append(TalkingPoint(
                category="Open Disputes",
                priority="medium",
                point=f"Address open dispute: {open_cases[0].get('title')}",
                detail=f"Priority: {open_cases[0].get('priority')}",
            ))

        return CallPrepBrief(
            customer_id=customer_id,
            customer_name=cust_name,
            account_summary=f"Account {cust_name} has {tot_out_str} outstanding across {open_bills_cnt} invoices.",
            total_outstanding_formatted=tot_out_str,
            open_bills_count=open_bills_cnt,
            oldest_bills_summary=oldest_bills_summary,
            ageing_summary=ageing_summary,
            payment_behaviour_summary=pay_summary,
            active_promise_summary=active_promise_summary,
            open_dispute_summary=open_dispute_summary,
            recent_chat_summary=recent_chat_str[:200],
            talking_points=points,
            call_script_hinglish=f"Namaste sir, {cust_name} se baat ho rahi hai? Hum accounts department se call kar rahe hain regarding aapka balance of {tot_out_str}...",
            call_script_english=f"Hello, am I speaking with {cust_name}? Calling regarding your pending balance of {tot_out_str}...",
            objection_handling=[
                ObjectionHandling(
                    likely_objection="Payment release will take time due to cash flow.",
                    recommended_response=f"We understand, sir. Can we clear the oldest bill ({oldest_bills_strs[0] if oldest_bills_strs else tot_out_str}) this week?",
                )
            ],
            recommended_target_commitment=f"Get firm payment commitment for oldest bills ({oldest_bills_strs[0] if oldest_bills_strs else tot_out_str})",
            notes_for_agent=["Acknowledge customer's relationship before addressing overdue balance.", "Confirm specific UTR / payment transfer mode."],
        )

    # LLM Prompt construction
    prompt = f"""### Customer Profile:
Customer ID: {customer_id}
Customer Name: {cust_name}
Total Outstanding: {tot_out_str}
Open Invoices Count: {open_bills_cnt}
Oldest Open Invoices: {oldest_bills_summary}
Ageing Breakdown: {ageing_summary}
Historical Payment Record: {pay_summary}
Active Payment Promises: {active_promise_summary}
Open Disputes/Cases: {open_dispute_summary}

### Recent Conversation Chat History:
{recent_chat_str}

Extract and synthesize the Call Preparation Brief for this customer."""

    try:
        brief = complete_structured(
            CallPrepBrief,
            CALL_PREP_SYSTEM_PROMPT,
            prompt,
            capability="structured_completion",
            temperature=0.1,
            example={
                "customer_id": customer_id,
                "customer_name": cust_name,
                "account_summary": f"Key trade customer with {tot_out_str} total dues.",
                "total_outstanding_formatted": tot_out_str,
                "open_bills_count": open_bills_cnt,
                "oldest_bills_summary": oldest_bills_summary,
                "ageing_summary": ageing_summary,
                "payment_behaviour_summary": pay_summary,
                "active_promise_summary": active_promise_summary,
                "open_dispute_summary": open_dispute_summary,
                "recent_chat_summary": "Customer discussed recent account status.",
                "talking_points": [
                    {
                        "category": "Overdue Balance",
                        "priority": "high",
                        "point": f"Address total outstanding balance of {tot_out_str}",
                        "detail": oldest_bills_summary,
                    }
                ],
                "call_script_hinglish": f"Namaste sir, {cust_name} se baat ho rahi hai?...",
                "call_script_english": f"Hello, calling from accounts regarding {cust_name}'s statement...",
                "objection_handling": [
                    {
                        "likely_objection": "Need some more time for payment",
                        "recommended_response": "We understand, can we schedule a partial release?",
                    }
                ],
                "recommended_target_commitment": f"Secure commitment for oldest overdue invoices",
                "notes_for_agent": ["Be polite and maintain relationship while securing firm dates."],
            },
        )
        brief.customer_id = customer_id
        brief.customer_name = cust_name
        brief.total_outstanding_formatted = tot_out_str
        brief.open_bills_count = open_bills_cnt
        brief.oldest_bills_summary = oldest_bills_summary
        brief.ageing_summary = ageing_summary
        brief.payment_behaviour_summary = pay_summary
        brief.active_promise_summary = active_promise_summary
        brief.open_dispute_summary = open_dispute_summary
        return brief
    except Exception as e:
        # Graceful fallback
        return CallPrepBrief(
            customer_id=customer_id,
            customer_name=cust_name,
            account_summary=f"Customer {cust_name} currently has {tot_out_str} outstanding across {open_bills_cnt} open bills.",
            total_outstanding_formatted=tot_out_str,
            open_bills_count=open_bills_cnt,
            oldest_bills_summary=oldest_bills_summary,
            ageing_summary=ageing_summary,
            payment_behaviour_summary=pay_summary,
            active_promise_summary=active_promise_summary,
            open_dispute_summary=open_dispute_summary,
            recent_chat_summary=recent_chat_str[:200],
            talking_points=[
                TalkingPoint(
                    category="Overdue Clearance",
                    priority="high",
                    point=f"Review oldest open invoices: {oldest_bills_summary}",
                    detail=f"Total outstanding: {tot_out_str}",
                )
            ],
            call_script_hinglish=f"Namaste sir, {cust_name} se baat ho rahi hai? Accounts department se call kiya hai aapke pending invoices ({oldest_bills_summary}) ke clearance ke regarding...",
            call_script_english=f"Hello, calling on behalf of accounts for {cust_name} regarding pending invoices ({oldest_bills_summary})...",
            objection_handling=[
                ObjectionHandling(
                    likely_objection="Payment is currently in process.",
                    recommended_response="Thank you! Could you please share the expected date or transaction ref so we can update our records?",
                )
            ],
            recommended_target_commitment=f"Secure clearance date for {oldest_bills_summary}",
            notes_for_agent=["Check if any invoice copies or statements are needed."],
        )
