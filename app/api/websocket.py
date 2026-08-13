"""WebSocket endpoint for customer conversations."""

from __future__ import annotations
import json
import logging
import uuid
from fastapi import WebSocket, WebSocketDisconnect, Query
from app.agents.customer_rep.graph import graph
from app.repositories.ledger import CustomerRepository

logger = logging.getLogger(__name__)
_cust_repo = CustomerRepository()


async def _authenticate(customer_id: str) -> bool:
    """Verify customer_id maps to a real ledger entry."""
    raw = await _cust_repo.find_by_guid(customer_id)
    return raw is not None


async def websocket_endpoint(
    websocket: WebSocket,
    customer_id: str = Query(..., description="Customer ledger GUID"),
    session_id: str = Query(default=None),
):
    await websocket.accept()
    session_id = session_id or str(uuid.uuid4())

    # Auth check
    if not await _authenticate(customer_id):
        await websocket.send_json({"error": "Authentication failed. Invalid customer ID."})
        await websocket.close(code=4001)
        return

    logger.info("WS connected session=%s customer=%s", session_id, customer_id)
    await websocket.send_json({"event": "connected", "session_id": session_id})

    config = {"configurable": {"thread_id": session_id}}

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                message = payload.get("message", data)
            except json.JSONDecodeError:
                message = data

            logger.info("session=%s message=%s", session_id, message[:100])

            # Build initial state for this turn
            turn_input: dict = {
                "session_id": session_id,
                "customer_id": customer_id,
                "messages": [{"role": "user", "content": message}],
                # Carry-through defaults (LangGraph merges via Annotated reducers)
                "conversation_summary": None,
                "intent": None,
                "entities": None,
                "task_plan": None,
                "customer_context": {},
                "financial_context": {},
                "case_context": {},
                "tool_results": [],
                "agent_results": [],
                "pending_action": None,
                "approval_state": None,
                "response": None,
                "errors": [],
                "task_id": None,
                "parent_task_id": None,
                "requesting_agent": None,
                "delegated_agent": None,
                "customer_name": None,
            }

            try:
                final_state = await graph.ainvoke(turn_input, config=config)
                response = final_state.get("response") or {}
                await websocket.send_json({
                    "event": "response",
                    "session_id": session_id,
                    "message": response.get("message", "I could not generate a response."),
                    "action_taken": response.get("action_taken", False),
                    "case_id": response.get("case_id"),
                    "approval_id": response.get("approval_id"),
                    "escalation_required": response.get("escalation_required", False),
                })
            except Exception as exc:
                logger.exception("Graph error session=%s", session_id)
                await websocket.send_json({
                    "event": "error",
                    "message": "I'm temporarily unable to process your request. Please try again.",
                })

    except WebSocketDisconnect:
        logger.info("WS disconnected session=%s", session_id)
