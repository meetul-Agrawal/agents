"""All pydantic models for the Customer Representative Agent."""

from __future__ import annotations
from datetime import datetime
from typing import Literal, Any
from pydantic import BaseModel, Field


# ── Structured Output Schemas (LLM → App) ────────────────────────────────────

class IntentClassification(BaseModel):
    intent: str = "GENERAL_QUERY"
    confidence: float = 1.0
    requires_customer_context: bool = True
    requires_financial_context: bool = False
    requires_case_context: bool = False
    requires_action: bool = False
    requires_human: bool = False


class ExtractedEntities(BaseModel):
    customer_id: str | None = None
    invoice_ids: list[str] = []
    receipt_ids: list[str] = []
    voucher_ids: list[str] = []
    case_ids: list[str] = []
    amounts: list[float] = []
    dates: list[str] = []
    payment_references: list[str] = []
    unresolved_references: list[str] = []

    def __init__(self, **data):
        # Flatten customer_id if passed as a list
        cid = data.get("customer_id")
        if isinstance(cid, list):
            valid_cids = [str(x) for x in cid if x is not None]
            data["customer_id"] = valid_cids[0] if valid_cids else None
        super().__init__(**data)


class TaskPlan(BaseModel):
    objective: str = "Assist customer"
    required_context: list[str] = []
    allowed_actions: list[str] = ["READ"]
    requires_confirmation: bool = False
    requires_management_approval: bool = False
    delegation_required: bool = False


class CustomerResponse(BaseModel):
    message: str
    action_taken: bool = False
    action_type: str | None = None
    case_id: str | None = None
    approval_id: str | None = None
    requires_follow_up: bool = False
    escalation_required: bool = False
    factual_basis: list[Any] = []


class FinancialAnalysis(BaseModel):
    question_answerable: bool = True
    invoice_status: str | None = None
    outstanding_amount: float | None = None
    directly_allocated_receipts: list[str] = []
    on_account_receipts: list[str] = []
    relevant_vouchers: list[str] = []
    relevant_ledger_entries: list[str] = []
    explanation: str = ""
    requires_accounting_review: bool = False


# ── Domain Models ─────────────────────────────────────────────────────────────

class ReceiptAllocation(BaseModel):
    bill_name: str | None = None          # invoice/bill reference name
    bill_type: str | None = None          # "Agst Ref" | "New Ref" | "Advance"
    amount: float = 0.0
    bill_date: str | None = None


class ReceiptDoc(BaseModel):
    voucher_number: str
    party_ledger_name: str
    date: str | None = None
    total_amount: float = 0.0
    allocations: list[ReceiptAllocation] = []
    is_cancelled: bool = False

    @property
    def against_reference_allocations(self) -> list[ReceiptAllocation]:
        return [a for a in self.allocations if a.bill_type == "Agst Ref"]

    @property
    def on_account_allocations(self) -> list[ReceiptAllocation]:
        return [a for a in self.allocations if a.bill_type in ("New Ref", "Advance", None)]


class SalesDoc(BaseModel):
    voucher_number: str
    party_ledger_name: str
    date: str | None = None
    total_amount: float = 0.0
    narration: str | None = None
    is_cancelled: bool = False
    bill_allocations: list[ReceiptAllocation] = []


class LedgerBalance(BaseModel):
    outstanding_balance: float = 0.0
    balance_type: str = "DEBIT"  # DEBIT | CREDIT
    as_of_date: str | None = None


class CustomerDoc(BaseModel):
    ledger_guid: str
    ledger_name: str
    group_name: str | None = None
    group_path: str | None = None
    mobile: str | None = None
    email: str | None = None
    outstanding_balance: float = 0.0
    balance_type: str = "DEBIT"


class FinancialContext(BaseModel):
    customer_id: str
    customer_name: str
    invoices: list[dict] = []
    receipts: list[dict] = []
    on_account_receipts: list[dict] = []
    ledger_entries: list[dict] = []
    vouchers: list[dict] = []
    reported_outstanding: float | None = None
    reconciliation_notes: list[str] = []


# ── Case / Approval Models ────────────────────────────────────────────────────

class CaseStatus(str):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class CaseType(str):
    COMPLAINT = "COMPLAINT"
    DISPUTE = "DISPUTE"
    PAYMENT_ISSUE = "PAYMENT_ISSUE"
    INVOICE_ISSUE = "INVOICE_ISSUE"
    RECEIPT_ISSUE = "RECEIPT_ISSUE"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    GENERAL_SUPPORT = "GENERAL_SUPPORT"


class Case(BaseModel):
    case_id: str
    customer_id: str
    case_type: str
    subject: str
    description: str
    status: str = "OPEN"
    priority: str = "NORMAL"
    related_entities: dict = {}
    notes: list[dict] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CaseCreationResult(BaseModel):
    success: bool
    case_id: str | None = None
    status: str = ""
    error: str | None = None


class ApprovalDecision(str):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class Approval(BaseModel):
    approval_id: str
    customer_id: str
    case_id: str | None = None
    request_type: str
    requested_action: str
    reason: str
    supporting_context: dict = {}
    decision: str = "PENDING"
    decision_notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: datetime | None = None


# ── Agent Gateway Contracts ───────────────────────────────────────────────────

class AgentTask(BaseModel):
    task_id: str
    parent_task_id: str | None = None
    requesting_agent: str
    target_agent: str
    customer_id: str
    intent: str
    objective: str
    context: dict = {}


class AgentResult(BaseModel):
    task_id: str
    agent_id: str
    status: Literal["SUCCESS", "PARTIAL", "FAILED", "PENDING"]
    findings: dict = {}
    actions_taken: list[dict] = []
    pending_actions: list[dict] = []
    requires_human_approval: bool = False
    customer_communication_required: bool = False
    recommended_next_action: str | None = None


# ── Payment Behavior ──────────────────────────────────────────────────────────

class PaymentBehavior(BaseModel):
    last_payment_date: str | None = None
    average_interval_days: float | None = None
    median_interval_days: float | None = None
    typical_payment_window: str | None = None
    overdue_frequency: float | None = None
    total_payments: int = 0


# ── Session ───────────────────────────────────────────────────────────────────

class Session(BaseModel):
    session_id: str
    customer_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    conversation_history: list[dict] = []
    current_context: dict = {}
    active_case_id: str | None = None
