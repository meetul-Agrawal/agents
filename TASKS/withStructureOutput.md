
# Customer Representative AI Agent — MVP Specification

## 1. Objective

Build a production-oriented MVP of a **Customer Representative AI Agent** using:

* **LangGraph** for stateful workflow orchestration.
* **OpenAI SDK** for model interaction.
* **OpenAI Structured Outputs** for typed LLM decisions and intermediate results.
* **OpenAI tool/function calling** for controlled business operations.
* **MongoDB** as the business system of record.
* A controlled **service/repository layer** between the agent and MongoDB.
* An **Agent Gateway** abstraction for future multi-agent orchestration.
* WebSocket/session support for conversational customer interactions.

The system should behave as a customer representative capable of:

1. Understanding customer messages.
2. Maintaining conversation context.
3. Identifying the customer.
4. Retrieving customer-specific business information.
5. Understanding sales, invoices, receipts, ledgers, vouchers and outstanding balances.
6. Correctly distinguishing receipts against references from on-account receipts.
7. Answering financial/account questions.
8. Searching and managing cases.
9. Creating disputes and complaints.
10. Creating approval requests.
11. Reading management decisions.
12. Communicating management decisions to customers.
13. Analyzing historical payment behavior for payment reminders.
14. Escalating situations requiring human intervention.
15. Eventually delegating work to specialized agents.

The MVP is a **single Customer Representative Agent**, but its architecture must be explicitly designed to support a future multi-agent ecosystem.

---

# 2. Architectural Principles

Use these responsibilities:

```text
LangGraph
    =
Workflow orchestration + state + routing + loops + persistence

OpenAI SDK
    =
LLM interaction

Structured Outputs
    =
Typed contracts for LLM decisions/results

Tool Calling
    =
Controlled interaction with application capabilities

Business Services
    =
Business rules + validation + authorization

Repositories
    =
MongoDB access

MongoDB
    =
Source of truth

Agent Gateway
    =
Future agent-to-agent orchestration
```

Do NOT build:

```text
LLM
 |
 └── arbitrary MongoDB queries
```

Build:

```text
LangGraph
   |
   v
OpenAI SDK
   |
   +── Structured Outputs
   |
   +── Tool Calling
   |
   v
Business Tools
   |
   v
Business Services
   |
   v
Repositories
   |
   v
MongoDB
```

---

# 3. High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CUSTOMER CHANNELS                                   │
│                                                                             │
│ WhatsApp │ iMessage │ WebSocket │ Web Chat │ Future Channels               │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SESSION / API LAYER                                  │
│                                                                             │
│ • Authentication                                                            │
│ • Customer identification                                                   │
│ • session_id                                                                │
│ • Conversation persistence                                                  │
│ • WebSocket lifecycle                                                       │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
╔═════════════════════════════════════════════════════════════════════════════╗
║                    CUSTOMER REPRESENTATIVE AGENT                           ║
║                              LangGraph                                      ║
║                                                                             ║
║  ┌───────────────────────────────────────────────────────────────────────┐  ║
║  │                         LANGGRAPH STATE                                │  ║
║  │                                                                       │  ║
║  │ session_id                                                            │  ║
║  │ customer_id                                                           │  ║
║  │ messages                                                              │  ║
║  │ conversation_summary                                                  │  ║
║  │ current_intent                                                        │  ║
║  │ extracted_entities                                                    │  ║
║  │ customer_context                                                      │  ║
║  │ financial_context                                                     │  ║
║  │ case_context                                                          │  ║
║  │ task_plan                                                             │  ║
║  │ tool_results                                                          │  ║
║  │ agent_results                                                         │  ║
║  │ pending_action                                                        │  ║
║  │ approval_state                                                        │  ║
║  │ response_context                                                      │  ║
║  │ errors                                                                │  ║
║  └───────────────────────────────────────────────────────────────────────┘  ║
║                                                                             ║
║                              │                                              ║
║                              ▼                                              ║
║                    ┌─────────────────────┐                                  ║
║                    │   OpenAI SDK        │                                  ║
║                    │                     │                                  ║
║                    │ Structured Output  │                                  ║
║                    │ Intent + Task Plan  │                                  ║
║                    └──────────┬──────────┘                                  ║
║                               │                                             ║
║                               ▼                                             ║
║                    ┌─────────────────────┐                                  ║
║                    │   LangGraph Router  │                                  ║
║                    └──────────┬──────────┘                                  ║
║                               │                                             ║
║                ┌──────────────┼──────────────┐                             ║
║                │              │              │                             ║
║                ▼              ▼              ▼                             ║
║          Financial        Case Context    Agent Gateway                    ║
║           Context                                                          ║
║                │              │              │                             ║
║                └──────────────┼──────────────┘                             ║
║                               │                                             ║
║                               ▼                                             ║
║                    ┌─────────────────────┐                                  ║
║                    │ OpenAI SDK          │                                  ║
║                    │ Tool Calling        │                                  ║
║                    └──────────┬──────────┘                                  ║
║                               │                                             ║
║                ┌──────────────┼───────────────┐                            ║
║                ▼              ▼               ▼                            ║
║           Read Tools      Write Tools     Agent Tools                      ║
║                               │               │                             ║
║                               ▼               ▼                             ║
║                        Business Services  Agent Gateway                    ║
║                               │               │                             ║
║                               └───────┬───────┘                             ║
║                                       │                                     ║
║                                       ▼                                     ║
║                            ┌──────────────────┐                             ║
║                            │ Result Validation│                             ║
║                            └────────┬─────────┘                             ║
║                                     │                                       ║
║                                     ▼                                       ║
║                            ┌──────────────────┐                             ║
║                            │ Response Output  │                             ║
║                            │ Structured       │                             ║
║                            └────────┬─────────┘                             ║
║                                     │                                       ║
╚═════════════════════════════════════╪═══════════════════════════════════════╝
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         BUSINESS SERVICE LAYER                              │
│                                                                             │
│ Customer │ Financial Context │ Case │ Approval │ Payment Behavior           │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              REPOSITORIES                                   │
│                                                                             │
│ Customer │ Sales │ Receipts │ Ledger │ Vouchers │ Cases │ Approvals         │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                               MONGODB                                       │
│                                                                             │
│ Customers │ Sales │ Receipts │ Ledgers │ Vouchers │ Cases │ Decisions       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

# 4. OpenAI SDK Usage

Use the OpenAI SDK directly for LLM calls.

Do not introduce an unnecessary second LLM abstraction layer.

The application should have a single configurable model client.

Conceptually:

```python
from openai import OpenAI

client = OpenAI()
```

Use the OpenAI SDK for:

* Structured Outputs.
* Tool calling.
* Final response generation.
* Intent classification.
* Task planning.
* Entity extraction.
* Context interpretation.

The exact model should be configurable through environment variables.

---

# 5. Structured Output Strategy

Structured Outputs should be used for **application-facing decisions**, not just final responses.

Create typed schemas for:

1. Intent classification.
2. Entity extraction.
3. Task planning.
4. Financial interpretation.
5. Case classification.
6. Approval classification.
7. Delegation decisions.
8. Final response.

Do not parse arbitrary natural-language model output when a structured schema can be used.

---

# 6. Intent Schema

Create a structured model similar to:

```python
class IntentClassification(BaseModel):
    intent: Literal[
        "GENERAL_QUERY",
        "CUSTOMER_INFORMATION",
        "INVOICE_QUERY",
        "PAYMENT_QUERY",
        "RECEIPT_QUERY",
        "LEDGER_QUERY",
        "OUTSTANDING_QUERY",
        "PAYMENT_HISTORY",
        "PAYMENT_REMINDER",
        "DISPUTE",
        "COMPLAINT",
        "CASE_STATUS",
        "CASE_UPDATE",
        "APPROVAL_REQUEST",
        "MANAGEMENT_DECISION",
        "GENERAL_SUPPORT",
        "UNKNOWN",
    ]

    confidence: float
    requires_customer_context: bool
    requires_financial_context: bool
    requires_case_context: bool
    requires_action: bool
    requires_human: bool
```

Validate `confidence` between 0 and 1.

---

# 7. Entity Extraction Schema

Use a structured schema:

```python
class ExtractedEntities(BaseModel):
    customer_id: str | None
    invoice_ids: list[str]
    receipt_ids: list[str]
    voucher_ids: list[str]
    case_ids: list[str]
    amounts: list[float]
    dates: list[str]
    payment_references: list[str]
    unresolved_references: list[str]
```

The application must separately validate whether supplied identifiers belong to the authenticated customer.

---

# 8. Task Planning Schema

Create:

```python
class TaskPlan(BaseModel):
    objective: str

    required_context: list[
        Literal[
            "CUSTOMER",
            "SALES",
            "RECEIPTS",
            "LEDGER",
            "VOUCHERS",
            "OUTSTANDING",
            "CASES",
            "APPROVALS",
            "DECISIONS",
            "PAYMENT_HISTORY",
        ]
    ]

    allowed_actions: list[
        Literal[
            "READ",
            "CREATE_CASE",
            "UPDATE_CASE",
            "CREATE_APPROVAL",
            "NOTIFY_CUSTOMER",
            "ESCALATE",
            "DELEGATE",
        ]
    ]

    requires_confirmation: bool
    requires_management_approval: bool
    delegation_required: bool
```

The plan is a **proposal from the model**.

Application code must still enforce permissions and business rules.

---

# 9. Final Response Structured Output

Use a schema such as:

```python
class CustomerResponse(BaseModel):
    message: str

    action_taken: bool
    action_type: str | None

    case_id: str | None
    approval_id: str | None

    requires_follow_up: bool
    escalation_required: bool

    factual_basis: list[str]
```

The `factual_basis` should contain internal references/identifiers that can be logged or used for traceability.

Do not necessarily expose raw internal identifiers to the customer.

---

# 10. LangGraph State

Define a strongly typed state.

Example:

```python
class CustomerRepState(TypedDict):
    session_id: str
    customer_id: str | None

    messages: list

    conversation_summary: str | None

    intent: IntentClassification | None
    entities: ExtractedEntities | None
    task_plan: TaskPlan | None

    customer_context: dict
    financial_context: dict
    case_context: dict

    tool_results: list
    agent_results: list

    pending_action: dict | None
    approval_state: dict | None

    response: CustomerResponse | None

    errors: list[str]

    task_id: str | None
    parent_task_id: str | None
    requesting_agent: str | None
    delegated_agent: str | None
```

Use serialization-safe state.

Avoid putting database connections or non-serializable objects into LangGraph state.

---

# 11. LangGraph Flow

Implement the graph approximately as:

```text
START
  |
  v
initialize_session
  |
  v
identify_customer
  |
  v
load_conversation_context
  |
  v
classify_intent
  |
  v
extract_entities
  |
  v
create_task_plan
  |
  v
route_task
  |
  +-------------------+--------------------+
  |                   |                    |
  v                   v                    v
local_context       local_action       delegation
  |                   |                    |
  |                   |                    |
  +-------------------+--------------------+
                      |
                      v
                execute_tools
                      |
                      v
                validate_results
                      |
                +-----+------+
                |            |
                v            v
            more_work      complete
                |            |
                +--> tools   v
                         response_generation
                                |
                                v
                         persist_interaction
                                |
                                v
                               END
```

---

# 12. Customer Identification

Customer identity must primarily come from the authenticated session/channel.

Do not trust the model to determine authorization.

For example:

```text
WebSocket authentication
        |
        v
customer_id = CUST-123
        |
        v
LangGraph
        |
        v
All customer tools are scoped to CUST-123
```

If a customer says:

> Show me customer CUST-999's invoices.

the application must reject that request regardless of what the LLM decides.

---

# 13. Context Retrieval

Create dedicated context services.

```text
CustomerContextService
FinancialContextService
CaseContextService
PaymentBehaviorService
```

The LLM should not retrieve every MongoDB document.

Use targeted context retrieval.

For example:

```text
Customer asks:
"Why is INV-123 still outstanding?"

        ↓

Retrieve:
Customer
INV-123
Receipts related to INV-123
On-account receipts for customer
Relevant ledger entries
Relevant vouchers
Relevant cases
```

Do not load unrelated historical data unless required.

---

# 14. Financial Context Service

Implement:

```python
FinancialContextService.get_context(
    customer_id,
    intent,
    entities
)
```

It should produce a structured financial context.

Example:

```python
class FinancialContext(BaseModel):
    customer_id: str

    invoices: list[dict]
    receipts: list[dict]
    on_account_receipts: list[dict]
    ledger_entries: list[dict]
    vouchers: list[dict]

    reported_outstanding: float | None

    reconciliation_notes: list[str]
```

The actual field names should be adapted to the existing database schema.

---

# 15. Receipt Semantics

This is a critical business requirement.

The system must distinguish:

```text
AGAINST_REFERENCE
```

from:

```text
ON_ACCOUNT
```

Example:

```text
Receipt R001
Amount = ₹40,000
Type = AGAINST_REFERENCE
Reference = INV-123
```

versus:

```text
Receipt R002
Amount = ₹20,000
Type = ON_ACCOUNT
Reference = null
```

The AI must never automatically allocate R002 to INV-123.

If the database contains an explicit allocation later, use that authoritative relationship.

---

# 16. Financial Reasoning

Use Structured Outputs for financial interpretation.

For example:

```python
class FinancialAnalysis(BaseModel):
    question_answerable: bool

    invoice_status: str | None
    outstanding_amount: float | None

    directly_allocated_receipts: list[str]
    on_account_receipts: list[str]
    relevant_vouchers: list[str]
    relevant_ledger_entries: list[str]

    explanation: str

    requires_accounting_review: bool
```

Important:

The LLM should reason **over retrieved financial facts**.

It should not become the source of truth for balances.

If MongoDB/accounting services provide an authoritative outstanding balance, that value takes precedence over an LLM-calculated value.

---

# 17. Tool Architecture

Use OpenAI tool/function calling for business capabilities.

Tools should be typed and narrowly scoped.

### Customer tools

```text
get_customer
get_customer_profile
```

### Sales tools

```text
get_sales
get_invoice
```

### Receipt tools

```text
get_receipts
get_receipt
```

### Ledger tools

```text
get_ledger
get_outstanding
```

### Voucher tools

```text
get_vouchers
```

### Case tools

```text
search_cases
get_case
create_case
update_case
```

### Approval tools

```text
create_approval_request
get_approval_request
get_management_decision
```

### Customer communication

```text
send_customer_notification
```

### Escalation

```text
escalate_to_human
```

---

# 18. Tool Security

Every tool must enforce:

```text
authenticated_customer_id
```

and appropriate authorization.

The LLM should not be able to bypass the authorization layer.

For example:

```python
get_invoice(
    customer_id="CUST-999",
    invoice_id="INV-123"
)
```

must fail if the authenticated customer is `CUST-123`.

Prefer deriving customer scope from application state rather than accepting it as a freely controlled LLM parameter.

---

# 19. Read vs Write Tools

Clearly separate tools into:

### Read

```text
get_customer
get_sales
get_invoice
get_receipts
get_ledger
get_vouchers
get_outstanding
search_cases
get_case
get_management_decision
```

### Write

```text
create_case
update_case
create_approval_request
send_customer_notification
escalate_to_human
```

Write tools require stronger validation.

---

# 20. Case Creation Workflow

Customer:

> I want to dispute invoice INV-123.

Flow:

```text
Customer Message
       |
       v
Structured Intent
       |
       v
DISPUTE
       |
       v
Retrieve Invoice
       |
       v
Retrieve Financial Context
       |
       v
Search Existing Cases
       |
       +----------+
       |          |
       v          v
Existing       No Existing
Case           Case
       |          |
       v          v
Update/Use     create_case()
Existing         |
Case             v
       |       Case ID
       |          |
       +----------+
              |
              v
       Structured Response
              |
              v
           Customer
```

Never create duplicate cases without checking existing cases first.

---

# 21. Case Tool Result

A write tool should return a structured result.

Example:

```python
class CaseCreationResult(BaseModel):
    success: bool
    case_id: str | None
    status: str
    error: str | None
```

Only if:

```text
success == true
```

may the agent tell the customer that the case was created.

---

# 22. Approval Workflow

Example:

Customer:

> Please waive my late fee.

Flow:

```text
Customer
   |
   v
Intent = APPROVAL_REQUEST
   |
   v
Retrieve relevant financial/case context
   |
   v
Determine approval requirement
   |
   v
create_approval_request()
   |
   v
Approval ID
   |
   v
Customer informed:
"Your request has been submitted for review."
   |
   v
Management
   |
   v
Decision
   |
   v
get_management_decision()
   |
   v
Customer Representative
   |
   v
Customer
```

The agent must not claim approval before an authoritative decision exists.

---

# 23. Management Decision Workflow

Possible states:

```text
PENDING
APPROVED
REJECTED
CANCELLED
```

The agent should communicate the actual authoritative state.

Example:

```text
PENDING
→ "Your request is still under review."

APPROVED
→ "Your request has been approved."

REJECTED
→ "Your request was reviewed but could not be approved."
```

The exact customer-facing explanation should be based on the management decision data.

---

# 24. Payment Behavior

Create:

```python
PaymentBehaviorService
```

It should calculate deterministic metrics such as:

```text
average payment interval
median payment interval
last payment date
typical payment window
overdue frequency
average days to payment
```

Do not ask the LLM to calculate these from thousands of receipts.

The service should calculate them.

Then provide the result to the agent.

Example:

```python
class PaymentBehavior(BaseModel):
    last_payment_date: str | None
    average_interval_days: float | None
    median_interval_days: float | None
    typical_payment_window: str | None
    overdue_frequency: float | None
```

The LLM can use this information when communicating reminders.

---

# 25. Payment Reminder Rules

The MVP should distinguish:

```text
Data analysis
```

from:

```text
Actual reminder scheduling/sending
```

Payment behavior can be calculated by the service.

The actual reminder should only be sent through a controlled communication tool/workflow.

Do not let the LLM independently decide to send arbitrary reminders without the configured business rules allowing it.

---

# 26. Conversation Memory

Use three layers:

### Session memory

Current WebSocket conversation.

### Customer context

Relevant current business information.

### Persistent interaction history

Historical customer interactions.

Do not inject the complete history into every model call.

Use:

```text
recent messages
+
conversation summary
+
relevant retrieved business context
```

---

# 27. Conversation Reference Resolution

The agent should support references such as:

```text
"that invoice"
"that payment"
"the previous receipt"
"my last complaint"
"the case we opened"
"that amount"
```

Use Structured Outputs for entity/reference resolution.

Example:

```python
class ReferenceResolution(BaseModel):
    reference_text: str
    entity_type: str
    resolved_id: str | None
    confidence: float
    ambiguous: bool
```

If ambiguous:

```text
Customer:
"What happened to my payment?"

```

and there are multiple recent payments, ask for clarification instead of guessing.

---

# 28. Final Response Generation

After all tool calls and workflow actions are complete, call OpenAI again with a structured response schema.

The response generator receives:

```text
customer message
conversation context
relevant business facts
tool results
case status
approval status
management decision
```

It produces:

```python
class CustomerResponse(BaseModel):
    message: str

    action_taken: bool
    action_type: str | None

    case_id: str | None
    approval_id: str | None

    requires_follow_up: bool
    escalation_required: bool
```

The customer-facing `message` must be generated from verified facts.

---

# 29. Grounding Rules

The agent MUST follow these rules:

### Never invent

* Invoice numbers.
* Receipt numbers.
* Amounts.
* Dates.
* Case IDs.
* Approval IDs.
* Management decisions.
* Payment allocations.
* Ledger balances.

### Never claim successful actions without successful tool results.

### Never infer an on-account receipt as payment against an invoice without authoritative allocation.

### Never override the accounting system's authoritative balance with an LLM calculation.

### Never expose another customer's information.

### Never reveal internal reasoning or hidden chain-of-thought.

---

# 30. Agent Gateway

Create an abstraction for future multi-agent orchestration.

```python
class AgentGateway:
    async def discover_agents(
        self,
        capability: str
    ) -> list:
        ...

    async def delegate_task(
        self,
        agent_id: str,
        task: "AgentTask"
    ) -> "AgentResult":
        ...

    async def get_task_result(
        self,
        task_id: str
    ) -> "AgentResult":
        ...
```

For the MVP, no specialized agents need to be implemented.

The gateway can return:

```text
No agent available
```

or use a local stub.

---

# 31. Agent Task Contract

Use Structured Models for agent-to-agent communication too.

```python
class AgentTask(BaseModel):
    task_id: str
    parent_task_id: str | None

    requesting_agent: str
    target_agent: str

    customer_id: str

    intent: str
    objective: str

    context: dict
```

---

# 32. Agent Result Contract

```python
class AgentResult(BaseModel):
    task_id: str
    agent_id: str

    status: Literal[
        "SUCCESS",
        "PARTIAL",
        "FAILED",
        "PENDING"
    ]

    findings: dict
    actions_taken: list[dict]
    pending_actions: list[dict]

    requires_human_approval: bool
    customer_communication_required: bool

    recommended_next_action: str | None
```

This means future agents can be added without changing the Customer Rep's fundamental state model.

---

# 33. Future Agent Architecture

Eventually:

```text
                         CUSTOMER
                            |
                            v
                  Customer Rep Agent
                         LangGraph
                            |
                      Agent Gateway
                            |
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
    Finance Agent     Collections Agent    Sales Agent
          │                 │                  │
          └─────────────────┼──────────────────┘
                            │
                    ┌───────┴────────┐
                    ▼                ▼
              Support Agent     Credit Agent
```

The Customer Rep remains the owner of the customer conversation.

Specialized agents own specialist tasks.

---

# 34. Business Service Layer

Create services such as:

```text
CustomerService
FinancialContextService
ReceiptService
LedgerService
CaseService
ApprovalService
DecisionService
PaymentBehaviorService
NotificationService
```

The LLM should never directly implement business rules.

For example:

```text
LLM:
"I want to create a dispute."

       ↓

Tool:
create_case()

       ↓

CaseService:
validate customer
validate invoice
check duplicates
determine case type
persist case

       ↓

MongoDB
```

---

# 35. Repository Layer

Create repositories such as:

```text
CustomerRepository
SalesRepository
ReceiptRepository
LedgerRepository
VoucherRepository
CaseRepository
ApprovalRepository
DecisionRepository
ConversationRepository
```

Repositories should contain MongoDB-specific implementation.

Do not put LLM logic into repositories.

---

# 36. MongoDB

MongoDB is the authoritative business data store.

The system must adapt to the existing MongoDB schema.

Before implementation:

1. Inspect existing collections.
2. Inspect document structures.
3. Identify relationships.
4. Identify indexes.
5. Identify existing business identifiers.
6. Identify receipt reference semantics.
7. Identify ledger semantics.
8. Identify case/approval structures.

Do not invent a new schema if an existing production schema already exists.

If schema information is missing, create a documented adapter layer rather than making hidden assumptions.

---

# 37. WebSocket Flow

The WebSocket request flow should be:

```text
WebSocket
   |
   v
Authenticate
   |
   v
Resolve Customer
   |
   v
Load/Resume LangGraph State
   |
   v
Run Graph
   |
   v
Tool Calls
   |
   v
Response
   |
   v
Persist Interaction
   |
   v
Return/Stream Response
```

Use `session_id` as the conversation/session identifier.

Use `customer_id` from the authenticated context.

---

# 38. LangGraph Persistence

Use LangGraph-compatible checkpointing/persistence where appropriate.

The implementation should support:

```text
session_id
thread_id
customer_id
```

and allow a conversation to continue across multiple WebSocket messages.

Do not put raw database clients in graph state.

---

# 39. Error Handling

Handle:

### MongoDB failure

Return a safe message:

> I'm temporarily unable to retrieve your account information.

Do not guess.

### Tool failure

Do not claim success.

### Ambiguous reference

Ask clarification.

### Conflicting accounting records

Flag for review.

### Missing customer

Ask for appropriate identification or escalate.

### Unauthorized request

Reject.

### Management decision unavailable

Say the request is still pending/requires review.

---

# 40. Human Escalation

Implement:

```python
escalate_to_human(...)
```

Trigger when:

* Customer explicitly requests a human.
* Financial records conflict.
* High-risk action is requested.
* Agent lacks authority.
* Management approval is needed.
* Customer dispute cannot be safely resolved.
* Required data is unavailable.

---

# 41. Observability

Every graph execution should have traceable identifiers:

```text
session_id
customer_id
run_id
task_id
node
tool
timestamp
duration
success
error
case_id
approval_id
```

Log structured events.

Do not unnecessarily log sensitive financial/customer information.

---

# 42. Recommended Project Structure

Use:

```text
app/
│
├── api/
│   ├── websocket.py
│   └── routes.py
│
├── agents/
│   ├── customer_rep/
│   │   ├── graph.py
│   │   ├── state.py
│   │   ├── nodes.py
│   │   ├── schemas.py
│   │   ├── prompts.py
│   │   └── policies.py
│   │
│   └── gateway/
│       ├── gateway.py
│       ├── registry.py
│       └── schemas.py
│
├── llm/
│   ├── client.py
│   ├── structured.py
│   └── tools.py
│
├── tools/
│   ├── customer.py
│   ├── sales.py
│   ├── receipts.py
│   ├── ledger.py
│   ├── vouchers.py
│   ├── cases.py
│   ├── approvals.py
│   ├── decisions.py
│   └── notifications.py
│
├── services/
│   ├── customer_context.py
│   ├── financial_context.py
│   ├── case.py
│   ├── approval.py
│   ├── decision.py
│   ├── payment_behavior.py
│   └── notification.py
│
├── repositories/
│   ├── customer.py
│   ├── sales.py
│   ├── receipts.py
│   ├── ledger.py
│   ├── vouchers.py
│   ├── cases.py
│   ├── approvals.py
│   ├── decisions.py
│   └── conversation.py
│
├── models/
│   ├── customer.py
│   ├── financial.py
│   ├── case.py
│   ├── approval.py
│   └── agent.py
│
├── memory/
│   ├── session.py
│   └── conversation.py
│
├── db/
│   └── mongodb.py
│
├── config/
│   └── settings.py
│
└── tests/
    ├── agents/
    ├── tools/
    ├── services/
    ├── repositories/
    └── integration/
```

Adapt to the existing codebase rather than blindly creating this exact structure.

---

# 43. Environment Configuration

Use environment variables:

```text
OPENAI_API_KEY
OPENAI_MODEL
MONGODB_URI
MONGODB_DATABASE
LOG_LEVEL
```

Never commit secrets.

The model must be configurable.

---

# 44. Testing

Implement unit, integration and graph-level tests.

### Intent

Test:

```text
"What is my outstanding balance?"
→ OUTSTANDING_QUERY
```

```text
"I want to dispute invoice INV-123."
→ DISPUTE
```

```text
"Please waive my late fee."
→ APPROVAL_REQUEST
```

### Receipt semantics

Test:

```text
Against-reference receipt
```

and:

```text
On-account receipt
```

must remain distinct.

### Conversation

Test:

```text
Customer:
What is outstanding on INV-123?

Agent:
₹50,000.

Customer:
What about the payment I made last week?

Agent:
Finds the relevant payment.

Customer:
Yes, that one should be against this invoice.

Agent:
Understands "that one" and "this invoice".
```

### Cases

Test:

```text
Existing relevant case
→ no duplicate case
```

and:

```text
No existing case
→ create_case()
```

### Approval

Test:

```text
Approval created
→ pending
→ management approved
→ customer informed
```

and:

```text
Approval created
→ pending
→ customer asks status
→ agent does NOT say approved
```

### Security

Test cross-customer access attempts.

### Tool grounding

Verify:

```text
Tool failure
→ agent cannot claim action succeeded.
```

---

# 45. MVP Conversation Examples

## Example 1 — Outstanding

```text
Customer:
How much do I currently owe?

Agent:
Retrieves authoritative outstanding balance.

Response:
Your current outstanding balance is ₹72,500.
```

---

## Example 2 — Receipt

```text
Customer:
I paid ₹20,000 last week. Why is the invoice still outstanding?

Agent:
Retrieves receipts and invoice.

Finds:
₹20,000 receipt
Type = ON_ACCOUNT

Response:
I can see the ₹20,000 payment from last week. It is currently recorded as an on-account receipt rather than being directly allocated to this invoice. Your ledger still shows the invoice as outstanding.
```

Do not claim the payment settled the invoice.

---

## Example 3 — Dispute

```text
Customer:
I want to dispute invoice INV-123.

Agent:
Searches existing cases.

No existing case.

Calls:
create_case()

Tool:
success = true
case_id = CASE-1001

Response:
I've raised a dispute for invoice INV-123. Your case reference is CASE-1001.
```

---

## Example 4 — Approval

```text
Customer:
Can you waive my late fee?

Agent:
Determines management approval is required.

Calls:
create_approval_request()

Response:
I've submitted your late-fee waiver request for management review. I'll be able to update you once a decision is available.
```

---

## Example 5 — Management decision

```text
Customer:
What's happening with my waiver request?

Agent:
get_management_decision()

Result:
APPROVED

Response:
Your late-fee waiver request has been approved by management.
```

---

# 46. Important LLM Prompting Rules

The system prompt for the Customer Representative should establish:

```text
You are the company's Customer Representative.

You are responsible for helping the authenticated customer with
questions about their account, sales, invoices, payments, receipts,
ledgers, vouchers, cases, disputes and approval requests.

You must use available business tools to retrieve authoritative
information.

Never invent financial information.

Never assume an on-account receipt belongs to a specific invoice
unless the accounting data explicitly establishes that allocation.

Never claim an action succeeded unless the corresponding tool
returned a successful result.

Never claim management approved or rejected a request unless an
authoritative management decision exists.

Never access or disclose another customer's information.

When information is ambiguous, ask for clarification.

When business records conflict, do not guess. Escalate or explain
that the account requires review.

You are a customer-facing representative, not the source of truth.
The business systems and approved tools are the source of truth.
```

---

# 47. Structured Output vs Free-Form Text

Use Structured Outputs for:

```text
Intent
Entity extraction
Reference resolution
Task plan
Financial analysis
Case classification
Approval classification
Delegation
Final response metadata
```

Use normal natural language only for:

```text
Customer-facing message
```

Even then, wrap the final response in a structured schema.

---

# 48. Tool Calling vs Structured Outputs

Treat them as complementary.

```text
Structured Output
=
"What does the model think should happen?"

Tool Calling
=
"Perform this specific controlled operation."

LangGraph
=
"Control the workflow around those decisions."

Business Service
=
"Validate whether the requested operation is actually allowed."

MongoDB
=
"Store/retrieve the authoritative business data."
```

---

# 49. Definition of Done

The MVP is complete when:

1. OpenAI SDK is integrated.
2. Structured Outputs are used for core agent decisions.
3. OpenAI tool/function calling is integrated.
4. LangGraph controls the workflow.
5. Customer session state is persisted.
6. Customer identity is authenticated and enforced.
7. MongoDB is accessed only through repositories/services/tools.
8. Customer information can be retrieved.
9. Sales/invoices can be retrieved.
10. Receipts can be retrieved.
11. Against-reference and on-account receipts are distinguished.
12. Ledgers can be retrieved.
13. Vouchers can be retrieved.
14. Outstanding balances can be retrieved.
15. Relevant financial context can be assembled.
16. Cases can be searched.
17. Cases can be created.
18. Cases can be updated.
19. Duplicate case creation is prevented where possible.
20. Approval requests can be created.
21. Management decisions can be retrieved.
22. Customer communication reflects authoritative case/approval state.
23. Payment behavior can be calculated through deterministic application code.
24. Payment reminder functionality has a controlled service/tool boundary.
25. Human escalation exists.
26. Tool failures cannot result in false claims.
27. Cross-customer data access is prevented.
28. Structured logging/tracing exists.
29. Tests cover the critical workflows.
30. Agent Gateway abstractions exist for future specialized agents.

---

# 50. Implementation Instructions for Codex

Before coding:

1. Inspect the repository.
2. Inspect the current MongoDB integration.
3. Inspect existing models and schemas.
4. Inspect existing WebSocket/session code.
5. Inspect authentication/authorization.
6. Identify existing dependencies.
7. Reuse existing infrastructure where appropriate.

Do not rewrite existing working infrastructure unnecessarily.

Do not invent database schemas without checking the actual schema.

Where the existing schema is unclear, create adapters/interfaces and document the assumption.

Implement the system incrementally.

Prioritize:

```text
Correctness
   >
Security
   >
Business-rule enforcement
   >
Observability
   >
Extensibility
   >
Convenience
```

The final architecture should make this progression possible:

```text
MVP

Customer
   ↓
Customer Rep Agent
   ↓
Tools
   ↓
MongoDB


Future

Customer
   ↓
Customer Rep Agent
   ↓
Agent Gateway
   ├── Finance Agent
   ├── Collections Agent
   ├── Sales Agent
   ├── Support Agent
   ├── Credit Agent
   └── Other Specialized Agents
   ↓
Shared Business Services
   ↓
MongoDB / Other Systems
```

The goal is **not** to build a generic chatbot.

The goal is to build a **stateful, tool-using, financially grounded Customer Representative Agent that can safely operate against real customer business data and evolve into the front-door orchestrator of a multi-agent customer-service platform.**
