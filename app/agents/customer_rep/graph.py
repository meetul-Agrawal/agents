"""LangGraph graph for the Customer Representative Agent."""

from __future__ import annotations
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from app.agents.customer_rep.state import CustomerRepState
from app.agents.customer_rep.nodes import (
    initialize_session,
    identify_customer,
    classify_intent,
    extract_entities,
    create_task_plan,
    route_task,
    execute_tools,
    delegate_agent,
    generate_response,
    persist_interaction,
)

_checkpointer = MemorySaver()


def build_graph():
    g = StateGraph(CustomerRepState)

    g.add_node("initialize_session", initialize_session)
    g.add_node("identify_customer", identify_customer)
    g.add_node("classify_intent", classify_intent)
    g.add_node("extract_entities", extract_entities)
    g.add_node("create_task_plan", create_task_plan)
    g.add_node("execute_tools", execute_tools)
    g.add_node("delegate_agent", delegate_agent)
    g.add_node("generate_response", generate_response)
    g.add_node("persist_interaction", persist_interaction)

    g.add_edge(START, "initialize_session")
    g.add_edge("initialize_session", "identify_customer")
    g.add_edge("identify_customer", "classify_intent")
    g.add_edge("classify_intent", "extract_entities")
    g.add_edge("extract_entities", "create_task_plan")

    g.add_conditional_edges(
        "create_task_plan",
        route_task,
        {"execute_tools": "execute_tools", "delegate_agent": "delegate_agent", "generate_response": "generate_response"},
    )

    g.add_edge("execute_tools", "generate_response")
    g.add_edge("delegate_agent", "generate_response")
    g.add_edge("generate_response", "persist_interaction")
    g.add_edge("persist_interaction", END)

    return g.compile(checkpointer=_checkpointer)


# Singleton compiled graph
graph = build_graph()
