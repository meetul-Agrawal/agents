from __future__ import annotations
from typing import Annotated, Any
import operator
from typing_extensions import TypedDict


class CustomerRepState(TypedDict):
    session_id: str
    customer_id: str | None
    customer_name: str | None          # denormalized for repo queries

    # Accumulated message list (each turn appends)
    messages: Annotated[list[dict], operator.add]

    conversation_summary: str | None

    # Structured LLM outputs
    intent: dict | None                # IntentClassification
    entities: dict | None              # ExtractedEntities
    task_plan: dict | None             # TaskPlan

    # Retrieved context
    customer_context: dict
    financial_context: dict
    case_context: dict

    # Tool loop
    tool_results: Annotated[list[dict], operator.add]
    agent_results: list[dict]

    pending_action: dict | None
    approval_state: dict | None

    response: dict | None              # CustomerResponse

    errors: Annotated[list[str], operator.add]

    # Multi-agent routing fields (future use)
    task_id: str | None
    parent_task_id: str | None
    requesting_agent: str | None
    delegated_agent: str | None
