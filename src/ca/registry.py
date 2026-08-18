"""Phase 0 — the agent and tool registry.

Declarations only: no agent is implemented yet. This is what routing, the
permission check and the "unknown agent / unknown tool" negative tests read.
Tools land here before their implementation so the contract is frozen first.
"""

from __future__ import annotations

from .contracts import ActionMode, AgentSpec, ToolSpec

READ_TOOLS = [
    ToolSpec(name="get_customer", purpose="Resolve and return customer master data", access="read"),
    ToolSpec(name="get_customer_ledger", purpose="Ledger entries and running balance", access="read"),
    ToolSpec(name="get_outstanding", purpose="Bill-level outstanding and ageing", access="read"),
    ToolSpec(name="get_sales_history", purpose="Sales vouchers for a customer", access="read"),
    ToolSpec(name="get_top_purchased_items", purpose="Items ranked by quantity bought, across all sales vouchers", access="read"),
    ToolSpec(name="get_receipts", purpose="Receipt vouchers and bill allocations", access="read"),
    ToolSpec(name="get_credit_notes", purpose="Credit notes / sales returns", access="read"),
    ToolSpec(name="get_open_orders", purpose="Open orders", access="read"),
    ToolSpec(name="get_payment_history", purpose="Payment behaviour over time", access="read"),
    ToolSpec(name="get_disputes", purpose="Dispute cases", access="read"),
    ToolSpec(name="get_approvals", purpose="Approval requests", access="read"),
    ToolSpec(name="get_customer_health", purpose="Latest health score", access="read"),
    ToolSpec(name="get_conversation_history", purpose="Past messages", access="read"),
    ToolSpec(name="get_customer_timeline", purpose="Chronological customer history", access="read"),
    ToolSpec(name="get_events", purpose="Raw event log for a customer", access="read"),
    ToolSpec(name="get_open_promise", purpose="Existing open payment promise for a customer", access="read"),
]

WRITE_TOOLS = [
    ToolSpec(name="create_dispute", purpose="Open a dispute case", access="write", mode="auto"),
    ToolSpec(name="update_dispute", purpose="Update a dispute case", access="write", mode="auto"),
    ToolSpec(name="create_approval", purpose="Raise an approval request", access="write", mode="auto"),
    ToolSpec(name="update_approval", purpose="Record an approval decision", access="write", mode="human_approval"),
    ToolSpec(name="update_approval_draft", purpose="Redraft an approval's summary/context after a self-check", access="write", mode="auto"),
    ToolSpec(name="create_payment_promise", purpose="Record a payment promise", access="write", mode="auto"),
    ToolSpec(name="create_event", purpose="Append to the event store", access="write", mode="auto"),
    ToolSpec(name="create_task", purpose="Create a follow-up task", access="write", mode="auto"),
    ToolSpec(name="create_order", purpose="Create a sales order", access="write", mode="auto_inform"),
    ToolSpec(name="create_sales_return", purpose="Register a sales return", access="write", mode="auto_inform"),
    ToolSpec(name="create_credit_note", purpose="Issue a credit note", access="write", mode="human_approval"),
    ToolSpec(name="create_customer_note", purpose="Attach an internal note", access="write", mode="auto"),
    ToolSpec(name="update_health_score", purpose="Persist a recomputed health score", access="write", mode="auto"),
    ToolSpec(name="send_customer_message", purpose="Send the customer-facing reply", access="write", mode="auto_inform"),
]

TOOLS: dict[str, ToolSpec] = {t.name: t for t in READ_TOOLS + WRITE_TOOLS}

_ALL_READ = [t.name for t in READ_TOOLS]

AGENTS: dict[str, AgentSpec] = {
    a.name: a
    for a in [
        AgentSpec(
            name="customer_assist",
            purpose="Orchestrate: understand, plan, route, aggregate, respond",
            tools=_ALL_READ + ["create_event", "send_customer_message"],
            readable_state=["*"],
            writable_state=["intents", "entities", "urgency", "execution_plan",
                            "agent_results", "pending_actions", "completed_actions",
                            "final_response"],
            escalation_rules=["any task with requires_human -> approval gateway"],
        ),
        AgentSpec(
            name="sa1_general",
            purpose="Answer read-only questions about the customer's records",
            tools=_ALL_READ,
            readable_state=["customer_context", "conversation_context"],
            writable_state=["agent_results"],
            escalation_rules=["operational request -> route back to orchestrator"],
        ),
        AgentSpec(
            name="sa2_recovery",
            purpose="Outstanding, payment promises, reminders, recovery state",
            tools=_ALL_READ + ["create_payment_promise", "create_event", "create_task"],
            readable_state=["customer_context", "conversation_context", "active_events"],
            writable_state=["agent_results", "pending_actions"],
            escalation_rules=["customer disputes amount -> sa3_dispute",
                              "settlement requested -> sa4_approval"],
        ),
        AgentSpec(
            name="sa3_dispute",
            purpose="Investigate disputes, gather evidence, manage the case",
            tools=_ALL_READ + ["create_dispute", "update_dispute", "create_event"],
            readable_state=["customer_context", "conversation_context", "active_cases"],
            writable_state=["agent_results", "pending_actions"],
            escalation_rules=["financial adjustment required -> sa4_approval"],
        ),
        AgentSpec(
            name="sa4_approval",
            purpose="Prepare, raise and track human approval requests",
            tools=_ALL_READ + ["create_approval", "update_approval", "create_event"],
            readable_state=["customer_context", "conversation_context", "active_approvals", "active_cases"],
            writable_state=["agent_results", "pending_actions"],
            escalation_rules=["never execute an approved-only action without an approval record"],
        ),
        AgentSpec(
            name="sa9_verifier",
            purpose="Self-check SA-4's drafted approval against what the customer asked for, "
                    "redraft on a miss, before a human ever sees it",
            tools=_ALL_READ + ["update_approval_draft"],
            readable_state=["customer_context", "active_approvals"],
            writable_state=["agent_results"],
            escalation_rules=["still unverified after max attempts -> leave flagged for the human reviewer"],
        ),
        AgentSpec(
            name="sa5_order",
            purpose="Capture orders with system-derived price, discount, availability",
            tools=_ALL_READ + ["create_order", "create_event"],
            readable_state=["customer_context", "conversation_context"],
            writable_state=["agent_results", "pending_actions"],
            escalation_rules=["non-standard pricing -> sa4_approval"],
        ),
        AgentSpec(
            name="sa6_return",
            purpose="Validate sales returns and trigger the credit note workflow",
            tools=_ALL_READ + ["create_sales_return", "create_credit_note", "create_event"],
            readable_state=["customer_context", "conversation_context"],
            writable_state=["agent_results", "pending_actions"],
            escalation_rules=["credit above threshold -> sa4_approval",
                              "quantity exceeds invoice -> clarify with customer"],
        ),
        AgentSpec(
            name="sa7_health",
            purpose="Recompute the deterministic health score and explain it",
            tools=_ALL_READ + ["update_health_score", "create_event"],
            readable_state=["customer_context", "active_events"],
            writable_state=["agent_results"],
            escalation_rules=[],
        ),
        AgentSpec(
            name="sa8_call_prep",
            purpose="Build the sales-call brief and extract post-call actions",
            tools=_ALL_READ + ["create_task", "create_event"],
            readable_state=["*"],
            writable_state=["agent_results", "pending_actions"],
            escalation_rules=["extracted promise -> sa2_recovery",
                              "extracted approval -> sa4_approval"],
        ),
    ]
}


class UnknownAgentError(KeyError):
    pass


class UnknownToolError(KeyError):
    pass


class PermissionDeniedError(PermissionError):
    pass


def get_agent(name: str) -> AgentSpec:
    try:
        return AGENTS[name]
    except KeyError:
        raise UnknownAgentError(name) from None


def get_tool(name: str) -> ToolSpec:
    try:
        return TOOLS[name]
    except KeyError:
        raise UnknownToolError(name) from None


def check_permission(agent: str, tool: str) -> ToolSpec:
    """Raises unless `agent` is allowed to call `tool`. Every tool call goes
    through here — that is the whole point of the tool layer."""
    spec = get_agent(agent)
    tool_spec = get_tool(tool)
    if tool not in spec.tools:
        raise PermissionDeniedError(f"{agent} may not call {tool}")
    return tool_spec


def action_mode(tool: str) -> ActionMode:
    return get_tool(tool).mode


def _self_check() -> None:
    for spec in AGENTS.values():
        unknown = set(spec.tools) - set(TOOLS)
        assert not unknown, f"{spec.name} references unknown tools: {unknown}"


_self_check()
