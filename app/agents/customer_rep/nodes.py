"""LangGraph node functions for the Customer Representative graph."""

from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime

from app.agents.customer_rep.state import CustomerRepState
from app.agents.customer_rep.prompts import (
    SYSTEM_PROMPT, INTENT_PROMPT, ENTITY_PROMPT, TASK_PLAN_PROMPT, RESPONSE_PROMPT,
)
from app.llm.client import structured_call, tool_call
from app.models.schemas import (
    IntentClassification, ExtractedEntities, TaskPlan, CustomerResponse,
)
from app.repositories.ledger import CustomerRepository
from app.repositories.conversation import ConversationRepository
from app.tools.tools import ToolExecutor, TOOL_SPECS

logger = logging.getLogger(__name__)

_cust_repo = CustomerRepository()
_conv_repo = ConversationRepository()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _msg_history(state: CustomerRepState) -> list[dict]:
    """Return message history suitable for LLM calls.
    ponytail: keep last 20 messages to avoid context bloat with small models.
    """
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    history = state.get("messages", [])
    # Filter out tool-call pairs that are no longer the most recent exchange
    # Keep only user/assistant/system messages for older turns; recent tool chains intact
    visible = []
    for m in history:
        role = m.get("role", "")
        if role in ("user", "assistant", "system"):
            visible.append(m)
        elif role == "tool" and visible and visible[-1].get("role") == "assistant":
            visible.append(m)  # keep tool response paired with its assistant call
    msgs.extend(visible[-20:])  # cap total visible messages
    return msgs


def _summary_context(state: CustomerRepState) -> str:
    ctx_parts: list[str] = []
    if state.get("conversation_summary"):
        ctx_parts.append(f"[Conversation summary: {state['conversation_summary']}]")
    if state.get("customer_context"):
        ctx_parts.append(f"[Customer: {json.dumps(state['customer_context'], default=str)[:300]}]")
    if state.get("financial_context"):
        ctx_parts.append(f"[Financial context loaded]")
    return "\n".join(ctx_parts)


# ── Nodes ─────────────────────────────────────────────────────────────────────

async def initialize_session(state: CustomerRepState) -> dict:
    """Assign task_id; reset per-turn state; load prior context if new session."""
    task_id = str(uuid.uuid4())
    # Reset every turn — these must NOT accumulate across turns
    updates: dict = {"task_id": task_id, "tool_results": [], "errors": []}

    if not state.get("messages"):
        # Brand-new session — check for prior persisted context
        prior = await _conv_repo.load_session(state["session_id"])
        if prior and prior.get("messages"):
            summary_msg = {
                "role": "system",
                "content": f"[Prior conversation context: {len(prior['messages'])} messages. "
                           f"Customer context: {json.dumps(prior.get('context', {}), default=str)[:200]}]",
            }
            updates["messages"] = [summary_msg]

    logger.info("session=%s customer=%s task=%s", state["session_id"], state.get("customer_id"), task_id)
    return updates


async def identify_customer(state: CustomerRepState) -> dict:
    """Verify customer_id from session against ledger. Reject if not found."""
    customer_id = state.get("customer_id")
    if not customer_id:
        return {"errors": ["No customer_id in session. Cannot identify customer."]}

    raw = await _cust_repo.find_by_guid(customer_id)
    if not raw:
        return {"errors": [f"Customer {customer_id} not found in ledger."]}

    customer_name = raw["ledgerName"]
    ob = raw.get("balances", {}).get("openingBalance", {})
    raw_amt = ob.get("amount", 0.0) if ob else 0.0
    amt = abs(raw_amt)
    btype = ob.get("type", "DEBIT") if ob else "DEBIT"

    # Do NOT pre-populate outstanding here — the tool computes it correctly from vouchers.
    # Putting a stale opening-balance figure here causes the LLM to answer without calling the tool.
    customer_context = {
        "ledger_guid": customer_id,
        "ledger_name": customer_name,
        "group_name": raw.get("groupName"),
        "mobile": raw.get("partyDetails", {}).get("mobile"),
        "email": raw.get("partyDetails", {}).get("email"),
    }
    logger.info("customer identified: %s (%s)", customer_name, customer_id)
    return {"customer_name": customer_name, "customer_context": customer_context}


async def classify_intent(state: CustomerRepState) -> dict:
    """Structured call → IntentClassification."""
    if state.get("errors"):
        return {}  # skip if customer not identified

    history = _msg_history(state)
    ctx = _summary_context(state)
    messages = history + ([{"role": "system", "content": ctx}] if ctx else [])
    messages += [{"role": "user", "content": INTENT_PROMPT}]

    try:
        intent = await structured_call(messages, IntentClassification, temperature=0.0)
        logger.info("intent=%s confidence=%.2f", intent.intent, intent.confidence)
        return {"intent": intent.model_dump()}
    except Exception as exc:
        logger.warning("intent classification failed: %s", exc)
        return {"intent": {"intent": "UNKNOWN", "confidence": 0.0,
                           "requires_customer_context": True, "requires_financial_context": False,
                           "requires_case_context": False, "requires_action": False, "requires_human": False}}


async def extract_entities(state: CustomerRepState) -> dict:
    """Structured call → ExtractedEntities with reference resolution."""
    history = _msg_history(state)
    ctx = _summary_context(state)
    messages = history + ([{"role": "system", "content": ctx}] if ctx else [])
    messages += [{"role": "user", "content": ENTITY_PROMPT}]

    try:
        entities = await structured_call(messages, ExtractedEntities, temperature=0.0)
        return {"entities": entities.model_dump()}
    except Exception as exc:
        logger.warning("entity extraction failed: %s", exc)
        return {"entities": ExtractedEntities().model_dump()}


async def create_task_plan(state: CustomerRepState) -> dict:
    """Structured call → TaskPlan."""
    intent_str = json.dumps(state.get("intent", {}))
    entities_str = json.dumps(state.get("entities", {}))

    history = _msg_history(state)
    messages = history + [{
        "role": "user",
        "content": f"{TASK_PLAN_PROMPT}\nIntent: {intent_str}\nEntities: {entities_str}",
    }]

    try:
        plan = await structured_call(messages, TaskPlan, temperature=0.0)
        return {"task_plan": plan.model_dump()}
    except Exception as exc:
        logger.warning("task planning failed: %s", exc)
        return {"task_plan": {"objective": "Answer customer query", "required_context": [],
                              "allowed_actions": ["READ"], "requires_confirmation": False,
                              "requires_management_approval": False, "delegation_required": False}}


def route_task(state: CustomerRepState) -> str:
    """Conditional routing based on task plan."""
    if state.get("errors"):
        return "generate_response"
    plan = state.get("task_plan") or {}
    if plan.get("delegation_required"):
        return "delegate_agent"
    return "execute_tools"


async def execute_tools(state: CustomerRepState) -> dict:
    """OpenAI tool-calling loop until no more tool calls."""
    if not state.get("customer_name"):
        return {"errors": ["Customer not identified; cannot execute tools."]}

    executor = ToolExecutor(state["customer_id"], state["customer_name"])
    messages = _msg_history(state)

    # Add financial/case context as system context
    fin_ctx = state.get("financial_context")
    if fin_ctx:
        messages.append({"role": "system", "content": f"[Financial context: {json.dumps(fin_ctx, default=str)[:600]}]"})

    all_tool_results: list[dict] = []

    # Loop: call LLM with tools → execute → repeat until no tool calls
    max_iterations = 8  # ponytail: cap to avoid infinite loops
    for iteration in range(max_iterations):
        choice = await tool_call(messages, TOOL_SPECS)

        if not choice.get("tool_calls"):
            # LLM finished; store final assistant message
            if choice.get("content"):
                messages.append({"role": "assistant", "content": choice["content"]})
            break

        # Add assistant turn with tool_calls
        messages.append({
            "role": "assistant",
            "content": choice.get("content"),
            "tool_calls": choice["tool_calls"],
        })

        # Execute each tool call
        for tc in choice["tool_calls"]:
            fn = tc["function"]
            logger.info("tool_call name=%s", fn["name"])
            result_str = await executor.execute(fn["name"], fn.get("arguments", "{}"))
            result_dict = json.loads(result_str)

            all_tool_results.append({
                "tool": fn["name"],
                "arguments": fn.get("arguments"),
                "result": result_dict,
                "timestamp": datetime.utcnow().isoformat(),
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_str,
            })

    return {"tool_results": all_tool_results, "messages": messages[len(_msg_history(state)):]}


async def delegate_agent(state: CustomerRepState) -> dict:
    """Stub: delegate to specialized agent via gateway."""
    # ponytail: no agents registered yet — fall back to tool execution
    logger.info("delegation requested but no agents registered; falling back to local tools")
    return await execute_tools(state)


async def generate_response(state: CustomerRepState) -> dict:
    """Structured call → CustomerResponse. Uses all accumulated context."""
    history = _msg_history(state)
    tool_results_str = json.dumps(state.get("tool_results", []), default=str)[:1500]
    errors = state.get("errors", [])

    if errors:
        error_msg = " ".join(errors)
        resp = CustomerResponse(
            message=f"I'm sorry, I was unable to process your request: {error_msg}. "
                    "Please contact support for assistance.",
            escalation_required=True,
        )
        return {"response": resp.model_dump()}

    messages = history + [{
        "role": "user",
        "content": (
            f"{RESPONSE_PROMPT}\n\n"
            f"Tool results summary: {tool_results_str}"
        ),
    }]

    try:
        resp = await structured_call(messages, CustomerResponse, temperature=0.2)
    except Exception as exc:
        logger.warning("response generation failed: %s", exc)
        from app.llm.client import plain_call
        msg = await plain_call(history + [{"role": "user", "content": "Summarize the findings for the customer."}])
        resp = CustomerResponse(message=msg)

    logger.info("response generated action_taken=%s", resp.action_taken)
    return {"response": resp.model_dump()}


async def persist_interaction(state: CustomerRepState) -> dict:
    """Save session state to conversations collection."""
    try:
        await _conv_repo.upsert_session(
            session_id=state["session_id"],
            customer_id=state.get("customer_id", ""),
            messages=state.get("messages", []),
            context={
                "customer_context": state.get("customer_context", {}),
                "last_intent": state.get("intent"),
                "last_entities": state.get("entities"),
            },
        )
    except Exception as exc:
        logger.warning("persist failed: %s", exc)
    return {}
