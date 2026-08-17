"""Phase 0 — frozen contracts for Customer Assist V1.

Every model here is a boundary type: it crosses agent, tool, persistence or
evaluation lines. Business logic does not belong in this file.

Identity note (grounded in the tenant data, not invented):
  A customer IS a Sundry Debtors ledger. `customer_id` is the ledger `_id`
  string; `ledger_name` is the join key, because vouchers carry `ledgerName`
  and always leave `ledgerId` null.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------

ID_PREFIXES = {
    "conversation": "CNV",
    "message": "MSG",
    "agent_run": "RUN",
    "agent_task": "TSK",
    "tool_call": "TCL",
    "event": "EVT",
    "case": "CASE",
    "dispute": "DSP",
    "approval": "APR",
    "promise": "PRM",
    "health": "HLT",
    "timeline": "TML",
    "task": "TAS",
    "eval_case": "EVL",
}

_ID_RE = re.compile(r"^[A-Z]{3,4}-\d{4}-[0-9a-f]{12}$")


def new_id(kind: str, *, now: datetime | None = None) -> str:
    """`DSP-2026-3f2a91c0b4de`. Sortable-by-year, no counter collection needed."""
    if kind not in ID_PREFIXES:
        raise ValueError(f"unknown id kind: {kind!r}")
    year = (now or utcnow()).year
    return f"{ID_PREFIXES[kind]}-{year}-{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


Id = Annotated[str, StringConstraints(min_length=1)]
NonEmpty = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class Contract(BaseModel):
    """Base: strict — unknown fields are an error, not a silent pass."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------
# Enumerations shared across agents
# --------------------------------------------------------------------------

Channel = Literal["email", "chat", "webhook", "call"]

AgentName = Literal[
    "customer_assist",
    "sa1_general",
    "sa2_recovery",
    "sa3_dispute",
    "sa4_approval",
    "sa5_order",
    "sa6_return",
    "sa7_health",
    "sa8_call_prep",
]
AGENT_NAMES: frozenset[str] = frozenset(AgentName.__args__)

ResultStatus = Literal[
    "completed",
    "needs_information",
    "needs_agent",
    "needs_approval",
    "needs_human",
    "failed",
]

ActionMode = Literal["auto", "auto_inform", "human_approval"]

EventType = Literal[
    "CUSTOMER_CREATED",
    "MESSAGE_RECEIVED",
    "MESSAGE_SENT",
    "ORDER_CREATED",
    "ORDER_UPDATED",
    "PAYMENT_RECEIVED",
    "PAYMENT_PARTIAL",
    "PAYMENT_PROMISE_CREATED",
    "PAYMENT_PROMISE_MODIFIED",
    "PAYMENT_PROMISE_MISSED",
    "RECOVERY_CONTACTED",
    "DISPUTE_CREATED",
    "DISPUTE_UPDATED",
    "DISPUTE_CLOSED",
    "APPROVAL_CREATED",
    "APPROVAL_APPROVED",
    "APPROVAL_REJECTED",
    "RETURN_REQUESTED",
    "RETURN_APPROVED",
    "CREDIT_NOTE_CREATED",
    "HEALTH_SCORE_UPDATED",
    "SALES_CALL_CREATED",
    "SALES_CALL_COMPLETED",
]
EVENT_TYPES: frozenset[str] = frozenset(EventType.__args__)


# --------------------------------------------------------------------------
# Customer / Customer 360
# --------------------------------------------------------------------------


class Customer(Contract):
    customer_id: Id  # ledgers._id
    ledger_name: NonEmpty  # join key into vouchers.ledgerName / partyLedgerName
    company_id: Id
    display_name: NonEmpty
    ledger_code: str | None = None
    group_path: str | None = None
    email: str | None = None
    mobile: str | None = None
    gstin: str | None = None
    state: str | None = None
    opening_balance: float = 0.0


class OpenBill(Contract):
    """An in-book sales invoice with money still outstanding against it."""

    voucher_number: NonEmpty
    invoice_date: date
    invoice_amount: float
    allocated: float
    outstanding: float
    age_days: int
    bucket: Literal["0-30", "31-60", "61-90", "90+"]


class Outstanding(Contract):
    """Bill-level receivables. Every field is derived from vouchers, never
    from an LLM.

    `outstanding` counts only invoices present in this book. Receipts that
    settle bills predating the book cannot be tied to an invoice — they are
    reported separately as `pre_book_settlements` rather than being netted off,
    which would understate the balance.

    `outstanding` is what every customer-facing surface must show as "what you
    owe" — it is the figure cross-checked against an independent implementation
    (`scripts/gen_golden.js`). `net_balance` exists for internal reconciliation
    only (it reproduces Tally's own ledger closing balance) and must NEVER be
    phrased to a customer as their balance: in this book it is contaminated by
    receipts settling pre-book invoices, which silently turns genuine debtors
    into apparent credits. Measured on real data: net_balance says Aadinath
    Traders is "in credit by ₹49,458"; they owe ₹386,114. Do not add a code path
    that surfaces `net_balance` as a dues figure without re-reading this note.
    """

    customer_id: Id
    ledger_name: NonEmpty
    as_of: date
    outstanding: float  # gross: sum of open invoices after their Agst-Ref receipts
    net_balance: float  # DIAGNOSTIC ONLY — see class docstring. Never customer-facing.
    open_bill_count: int
    invoiced_total: float
    receipted_total: float
    allocated_total: float
    pre_book_settlements: float
    on_account: float
    advance: float
    opening_balance: float
    ageing: dict[str, float] = Field(default_factory=dict)
    open_bills: list[OpenBill] = Field(default_factory=list)


class LedgerLine(Contract):
    """One posting to the customer's ledger, oldest first."""

    date: date
    voucher_number: NonEmpty
    category: Literal["Sales", "Receipt", "Other"]
    debit: float = 0.0  # increases what the customer owes
    credit: float = 0.0  # reduces it
    balance: float = 0.0  # running amount owed after this line
    against_bills: list[str] = Field(default_factory=list)


class PaymentBehaviour(Contract):
    receipt_count: int = 0
    total_received: float = 0.0
    first_receipt: date | None = None
    last_receipt: date | None = None
    avg_days_to_settle: float | None = None
    settled_bill_count: int = 0


class SalesHistoryQuery(Contract):
    """Normalized query parameters for sales/purchase history requests."""

    item_query: str | None = None
    voucher_number: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    period: str = "all_time"
    limit: int | None = None
    metric: Literal["rate", "quantity", "invoices", "all"] = "all"


class PaymentHistoryQuery(Contract):
    """Normalized query parameters for payment/receipt history requests."""

    voucher_number: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    period: str = "all_time"
    limit: int | None = None
    metric: Literal["total_amount", "count", "recent_payments", "settle_speed", "last_payment", "all"] = "all"


class DataCapability(Contract):
    """What this tenant's book can and cannot answer. Guards every agent from
    promising data that does not exist."""

    credit_notes: bool = False
    orders: bool = False
    due_dates: bool = False
    credit_limits: bool = False
    note: str = ""


class Customer360(Contract):
    """The logical state the orchestrator reasons over. Sections are filled in
    Phase 1; Phase 0 only freezes the shape."""

    customer: Customer
    financial: dict[str, Any] = Field(default_factory=dict)
    commercial: dict[str, Any] = Field(default_factory=dict)
    communication: dict[str, Any] = Field(default_factory=dict)
    relationship: dict[str, Any] = Field(default_factory=dict)
    operational: dict[str, Any] = Field(default_factory=dict)
    agent_state: dict[str, Any] = Field(default_factory=dict)
    built_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Conversation / Message
# --------------------------------------------------------------------------


class Message(Contract):
    message_id: Id = Field(default_factory=lambda: new_id("message"))
    conversation_id: Id
    customer_id: Id | None = None
    channel: Channel
    direction: Literal["inbound", "outbound"]
    text: str
    timestamp: datetime = Field(default_factory=utcnow)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # External id (email Message-ID, webhook delivery id) used for dedupe.
    external_id: str | None = None


class Conversation(Contract):
    conversation_id: Id = Field(default_factory=lambda: new_id("conversation"))
    customer_id: Id | None = None
    channel: Channel
    subject: str | None = None
    status: Literal["open", "waiting", "closed"] = "open"
    thread_key: str | None = None  # email thread id / chat session id
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------
# Tools and agents
# --------------------------------------------------------------------------


class ToolSpec(Contract):
    name: NonEmpty
    purpose: NonEmpty
    access: Literal["read", "write"]
    mode: ActionMode = "auto"
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCall(Contract):
    tool_call_id: Id = Field(default_factory=lambda: new_id("tool_call"))
    tool: NonEmpty
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    latency_ms: int | None = None


class AgentSpec(Contract):
    """The agent contract from Phase 0 of the roadmap."""

    name: AgentName
    purpose: NonEmpty
    tools: list[str] = Field(default_factory=list)
    readable_state: list[str] = Field(default_factory=list)
    writable_state: list[str] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)


class AgentTask(Contract):
    agent_task_id: Id = Field(default_factory=lambda: new_id("agent_task"))
    agent: AgentName
    action: NonEmpty
    reason: str = ""
    priority: int = Field(default=1, ge=1, le=5)
    requires_human: bool = False
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)


class ProposedAction(Contract):
    """What an agent wants done. Shadow mode executes nothing but these."""

    type: NonEmpty
    mode: ActionMode = "auto"
    payload: dict[str, Any] = Field(default_factory=dict)
    executed: bool = False


class AgentResult(Contract):
    agent_run_id: Id = Field(default_factory=lambda: new_id("agent_run"))
    agent: AgentName
    agent_task_id: Id | None = None
    status: ResultStatus
    summary: str = ""
    actions: list[ProposedAction] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    customer_message: str | None = None
    next_agent: AgentName | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _failed_needs_error(self) -> "AgentResult":
        if self.status == "failed" and not self.error:
            raise ValueError("status='failed' requires an error")
        return self


class ExecutionPlan(Contract):
    tasks: list[AgentTask] = Field(default_factory=list)

    @field_validator("tasks")
    @classmethod
    def _deps_resolve(cls, tasks: list[AgentTask]) -> list[AgentTask]:
        ids = {t.agent_task_id for t in tasks}
        for t in tasks:
            unknown = set(t.depends_on) - ids
            if unknown:
                raise ValueError(f"task {t.agent_task_id} depends on unknown {unknown}")
        return tasks

    @property
    def agents(self) -> set[str]:
        return {t.agent for t in self.tasks}


# --------------------------------------------------------------------------
# Understanding
# --------------------------------------------------------------------------


class Intent(Contract):
    name: NonEmpty
    confidence: float = Field(ge=0.0, le=1.0)
    entities: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


# --------------------------------------------------------------------------
# What the model returns — one object per inbound message
# --------------------------------------------------------------------------


class ModelOutput(BaseModel):
    """Base for anything an LLM fills in.

    Unlike `Contract`, unknown fields are ignored rather than rejected: a model
    that adds a stray key should not fail the whole parse. Every field here is
    a *claim*, not a fact — `orchestrator.verify()` checks each one against the
    message before anything downstream sees it.
    """

    model_config = ConfigDict(extra="ignore")


class ExtractedValue(ModelOutput):
    """A number the model found. `text` must be a verbatim span of the message —
    that is what makes the claim checkable. `value` is the model's arithmetic and
    is always recomputed by us before use."""

    text: str = ""
    value: float | None = None
    unit: str | None = None


class Request(ModelOutput):
    """One thing the customer is asking for."""

    intent: str = ""
    clause: str = ""
    confidence: float = 0.5
    amount: ExtractedValue | None = None
    quantity: ExtractedValue | None = None
    voucher_ref: str | None = None
    due_date_text: str | None = None
    reason: str = ""

    # Dispute-only, and both purely descriptive — neither gates a deterministic
    # decision or states a fact about money, so they carry none of the risk a
    # hallucinated amount or voucher would. `about_balance` is the one field
    # that does steer SA-3's evidence path (invoice lookup vs. the account's
    # own outstanding position), and being wrong either way is safe: it can
    # only pick between two grounded, tool-read answers, never invent one.
    about_balance: bool = False
    issue_label: str | None = None
    item_mentioned: str | None = None

    # settlement_request / credit_note_request only — which of Approval.type's
    # six categories this is. A free string, validated against that Literal by
    # the consumer (`sa4_approval.py`), the same arm's-length pattern already
    # used for `intent` against the agent catalog: the model names it, the
    # system checks the name is one it actually recognises.
    approval_type: str | None = None

    @field_validator("about_balance", mode="before")
    @classmethod
    def _coerce_about_balance(cls, v: Any) -> bool:
        return bool(v) if v is not None else False

    @field_validator(
        "voucher_ref", "due_date_text", "issue_label", "item_mentioned", "approval_type",
        mode="before",
    )
    @classmethod
    def _coerce_plain_string_fields(cls, v: Any) -> Any:
        """Measured the model shaping a plain-string field like its
        `ExtractedValue`-typed siblings above (`{"text": ..., "value": ...}`)
        instead of a bare string — plausibly by analogy to `amount`/`quantity`
        sitting right next to them, and not confined to one field: seen on
        both `voucher_ref` and `item_mentioned` in practice. Strict validation
        rejects the *entire* Understanding over one wrong field, discarding an
        otherwise-correct classification. Every field here is still a claim
        the orchestrator re-verifies against the raw message before trusting
        it (`verify_value`, `_clause_grounded`), so coercing the shape here
        loses no safety — it only stops a good parse being thrown away for a
        wrong-shaped-but-recoverable field."""
        if isinstance(v, dict):
            return v.get("text") or v.get("value")
        return v


class Understanding(ModelOutput):
    """The single structured reading of one inbound message. Intents, entities,
    language and the cross-customer signal all come from here, so one call
    answers everything the orchestrator needs to plan."""

    language: str = "en"
    is_greeting_only: bool = False
    refers_to_other_party: str | None = None
    requests: list[Request] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Operational records (all persisted in the app DB, never the tenant DB)
# --------------------------------------------------------------------------


class Event(Contract):
    event_id: Id = Field(default_factory=lambda: new_id("event"))
    customer_id: Id
    type: EventType
    source: NonEmpty
    timestamp: datetime = Field(default_factory=utcnow)
    conversation_id: Id | None = None
    agent_run_id: Id | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class Case(Contract):
    case_id: Id = Field(default_factory=lambda: new_id("case"))
    customer_id: Id
    conversation_id: Id | None = None  # where to send the resolution follow-up
    type: Literal["dispute", "task", "other"] = "dispute"
    status: Literal["open", "investigating", "waiting", "resolved", "closed"] = "open"
    priority: Literal["low", "normal", "high", "critical"] = "normal"
    title: NonEmpty
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)
    owner: str | None = None
    resolution: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


ApprovalType = Literal[
    "special_discount",
    "settlement",
    "credit_limit",
    "large_credit_note",
    "write_off",
    "exceptional_terms",
]
APPROVAL_TYPES: frozenset[str] = frozenset(ApprovalType.__args__)


class Approval(Contract):
    approval_id: Id = Field(default_factory=lambda: new_id("approval"))
    customer_id: Id
    conversation_id: Id | None = None  # where to send the decision follow-up
    type: ApprovalType
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    requested_by: AgentName
    amount: float | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    recommendation: str = ""
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class PaymentPromise(Contract):
    promise_id: Id = Field(default_factory=lambda: new_id("promise"))
    customer_id: Id
    amount: float = Field(gt=0)
    due_date: date
    status: Literal["promised", "paid", "partial", "missed", "cancelled"] = "promised"
    conversation_id: Id | None = None
    paid_amount: float = 0.0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class HealthScore(Contract):
    health_id: Id = Field(default_factory=lambda: new_id("health"))
    customer_id: Id
    score: int = Field(ge=0, le=100)
    previous_score: int | None = Field(default=None, ge=0, le=100)
    drivers: list[str] = Field(default_factory=list)
    components: dict[str, float] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=utcnow)

    @property
    def change(self) -> int | None:
        return None if self.previous_score is None else self.score - self.previous_score


class Task(Contract):
    """A follow-up someone must act on later. Unlike an AgentTask (a step inside
    one run), this outlives the conversation and carries its own due date."""

    task_id: Id = Field(default_factory=lambda: new_id("task"))
    customer_id: Id
    kind: Literal["reminder", "recovery_followup", "payment_trace", "other"] = "other"
    title: NonEmpty
    due_date: date | None = None
    status: Literal["open", "done", "cancelled"] = "open"
    conversation_id: Id | None = None
    source: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class TimelineEvent(Contract):
    timeline_id: Id = Field(default_factory=lambda: new_id("timeline"))
    customer_id: Id
    at: datetime
    kind: NonEmpty  # "invoice", "receipt", "message", "promise", "dispute", ...
    title: NonEmpty
    ref: str | None = None  # voucher number, case id, ...
    payload: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Orchestrator state
# --------------------------------------------------------------------------


class CustomerAssistState(Contract):
    customer_id: Id | None = None
    conversation_id: Id | None = None
    channel: Channel
    message: str

    customer_context: Customer360 | None = None
    conversation_context: list[Message] = Field(default_factory=list)
    relevant_vouchers: list[dict[str, Any]] = Field(default_factory=list)
    active_cases: list[Case] = Field(default_factory=list)
    active_approvals: list[Approval] = Field(default_factory=list)
    active_events: list[Event] = Field(default_factory=list)

    intents: list[Intent] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["low", "normal", "high"] = "normal"

    execution_plan: ExecutionPlan | None = None
    agent_results: list[AgentResult] = Field(default_factory=list)
    pending_actions: list[ProposedAction] = Field(default_factory=list)
    completed_actions: list[ProposedAction] = Field(default_factory=list)

    final_response: str | None = None


# System boundaries — named so tests and docs cannot drift apart.
BOUNDARIES = (
    "input",
    "customer_360",
    "orchestrator",
    "agents",
    "tools",
    "business_services",
    "events",
    "persistence",
    "llm_gateway",
    "evaluation",
    "observability",
)
