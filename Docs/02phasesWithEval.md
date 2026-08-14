# Customer Assist V1 — Phases With Evaluation & Testing

## 0. Purpose

This document defines the complete implementation roadmap for the Customer Assist Agentic Orchestration system.

The objective is to build the platform from scratch in controlled phases while introducing evaluation and testing from the beginning.

The system should eventually support:

- Email
- Multi-thread email conversations
- Chat
- Webhook-based conversations
- Customer 360 context
- Tally voucher data
- MongoDB
- LangGraph orchestration
- OpenAI SDK
- NVIDIA NIM
- Structured output
- Streaming output
- 8 specialized sub-agents
- Event-driven workflows
- Human approvals
- Health scoring
- Sales call preparation
- Continuous agent evaluation

The central architecture is:

```text
                         CUSTOMER
                            │
                            ▼
                  EMAIL / CHAT / WEBHOOK
                            │
                            ▼
                     INPUT ADAPTER
                            │
                            ▼
                    CUSTOMER RESOLVER
                            │
                            ▼
                     CUSTOMER 360
                            │
                            ▼
                ┌───────────────────────┐
                │   CUSTOMER ASSIST     │
                │    ORCHESTRATOR       │
                └───────────┬───────────┘
                            │
                      INTENT + PLAN
                            │
         ┌──────────────────┼──────────────────┐
         │                  │                  │
         ▼                  ▼                  ▼
       SA-1               SA-2               SA-3
      General           Recovery            Dispute
         │                  │                  │
         ▼                  ▼                  ▼
       SA-4               SA-5               SA-6
      Approval            Order              Return
                         Capture
         │                  │                  │
         └──────────────────┼──────────────────┘
                            │
                            ▼
                           SA-7
                       Health Score
                            │
                            ▼
                           SA-8
                       Sales Call Prep
                            │
                            ▼
                    RESULT AGGREGATION
                            │
                 ┌──────────┼──────────┐
                 ▼          ▼          ▼
              RESPONSE    EVENTS      STATE
                 │          │          │
                 └──────────┼──────────┘
                            ▼
                       CUSTOMER 360
                            │
                            └──────────► NEXT ACTION
```

---

# 1. Guiding Architecture Principles

These principles should remain stable throughout V1.

## 1.1 LLMs reason; systems calculate and commit

```text
LLM
 ├── Understand
 ├── Classify
 ├── Extract
 ├── Plan
 ├── Summarize
 └── Explain

Business Services
 ├── Calculate
 ├── Validate
 ├── Authorize
 ├── Create
 └── Commit

MongoDB / Tally
 ├── Source data
 └── Persisted state
```

The LLM should not be the source of truth for:

- Accounting balances
- Prices
- Discounts
- Credit amounts
- Inventory
- Authorization
- Customer identity
- Voucher existence

## 1.2 Customer Assist owns orchestration

Agents should generally not freely invoke one another.

Prefer:

```text
Customer Assist
      │
      ├── SA-2
      ├── SA-3
      └── SA-7
      │
      ▼
Customer Assist
```

rather than uncontrolled chains:

```text
SA-2 → SA-3 → SA-4 → SA-7 → SA-2 → ...
```

## 1.3 Every important action is traceable

Every execution should be traceable through identifiers such as:

```text
customer_id
conversation_id
message_id
agent_run_id
agent_task_id
tool_call_id
event_id
case_id
approval_id
```

## 1.4 Evaluation starts before the first agent

Every phase must have:

1. Unit tests
2. Integration tests
3. Regression tests where applicable
4. Evaluation datasets where applicable
5. Failure/edge cases
6. Exit criteria

---

# 2. Overall Phase Plan

```text
PHASE 0
Architecture + Evaluation Foundation
        │
        ▼
PHASE 1
Data + Customer 360 Foundation
        │
        ▼
PHASE 2
Input + Conversation Layer
        │
        ▼
PHASE 3
Customer Assist Orchestrator
        │
        ▼
PHASE 4
SA-1 General Agent
        │
        ▼
PHASE 5
SA-2 Recovery Agent
        │
        ▼
PHASE 6
SA-3 Dispute + SA-4 Approval
        │
        ▼
PHASE 7
SA-5 Order Capture + SA-6 Sales Return
        │
        ▼
PHASE 8
SA-7 Health Score
        │
        ▼
PHASE 9
SA-8 Sales Call Prep
        │
        ▼
PHASE 10
Full Agentic Integration
        │
        ▼
PHASE 11
Production Hardening
        │
        ▼
PHASE 12
Production + Continuous Evaluation
```

---

# 3. Evaluation Architecture

Evaluation should itself be treated as a system.

```text
                       EVALUATION SYSTEM
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
      Deterministic        LLM/Agent           Workflow
         Tests               Evals               Evals
          │                   │                   │
          └───────────────────┼───────────────────┘
                              │
                              ▼
                       Regression Suite
                              │
                              ▼
                         Human Review
                              │
                              ▼
                    Production Evaluation
```

Recommended evaluation repository:

```text
evals/
├── datasets/
│   ├── routing/
│   ├── general/
│   ├── recovery/
│   ├── dispute/
│   ├── approval/
│   ├── order/
│   ├── return/
│   ├── health/
│   ├── call_prep/
│   └── end_to_end/
│
├── graders/
│   ├── exact_match/
│   ├── structured/
│   ├── factuality/
│   ├── grounding/
│   ├── tool_use/
│   ├── state_transition/
│   └── response_quality/
│
├── scenarios/
├── expected_outputs/
├── regression/
└── reports/
```

---

# 4. Evaluation Levels

Every agent should eventually be evaluated at four levels.

## Level 1 — Tool / Deterministic Evaluation

Tests:

```text
Tool Input
    ↓
Tool Execution
    ↓
Expected Data / Action
```

Examples:

- `get_customer()`
- `get_ledger()`
- `get_sales()`
- `get_receipts()`
- `create_dispute()`
- `create_order()`
- `create_return()`

Metrics:

- Tool success rate
- Input validation
- Output schema correctness
- Data correctness
- Idempotency
- Error handling
- Permission enforcement

## Level 2 — Agent Evaluation

Tests:

```text
Input + Context
       ↓
Agent
       ↓
Tools
       ↓
Structured Result
```

Metrics:

- Intent understanding
- Tool selection
- Tool arguments
- Factual accuracy
- Grounding
- Completeness
- Hallucination
- Structured output validity
- Customer response quality

## Level 3 — Workflow Evaluation

Tests the full state transition:

```text
Input
  ↓
Customer Assist
  ↓
Agent(s)
  ↓
Tools
  ↓
Events
  ↓
State Update
  ↓
Final Response
```

Metrics:

- Correct routing
- Correct sequence
- Correct state changes
- Correct event creation
- Correct agent handoffs
- Correct escalation
- Correct final response

## Level 4 — Production Evaluation

Production traces are sampled and evaluated for:

- Correctness
- Customer impact
- Tool behavior
- State transitions
- Escalations
- Human overrides
- Latency
- Cost
- Regression

---

# 5. Evaluation Dataset Design

Do not build one giant dataset.

Create domain-specific datasets.

```text
evals/datasets/

routing/
├── single_intent.jsonl
├── multi_intent.jsonl
├── ambiguous.jsonl
└── adversarial.jsonl

general/
├── ledger.jsonl
├── sales.jsonl
├── receipts.jsonl
├── credit_notes.jsonl
└── conversation_history.jsonl

recovery/
├── payment_promise.jsonl
├── payment_received.jsonl
├── missed_promise.jsonl
├── partial_payment.jsonl
└── recovery_dispute.jsonl

dispute/
approval/
order/
return/
health/
call_prep/
end_to_end/
```

A test case should contain more than input and expected text.

Example:

```json
{
  "case_id": "REC-001",
  "customer_id": "CUST-00124",
  "input": "I'll pay ₹2 lakh by 20 August.",
  "context": {
    "outstanding": 482500,
    "previous_promises": [],
    "health_score": 74
  },
  "expected": {
    "intent": "payment_promise",
    "agent": "recovery",
    "amount": 200000,
    "due_date": "2026-08-20",
    "create_promise": true,
    "create_event": true
  }
}
```

This is essential because the platform is agentic and stateful.

---

# 6. Golden Dataset

Create a curated, human-reviewed Golden Dataset.

Initial target:

```text
100–200 routing cases
50–100 cases per major agent
50–100 multi-agent cases
50 adversarial cases
50 edge cases
```

These are initial targets, not hard requirements.

The Golden Dataset should contain:

- Easy cases
- Normal cases
- Complex cases
- Ambiguous cases
- Multi-intent cases
- Missing-data cases
- Conflicting-data cases
- Adversarial cases
- High-risk financial cases

The Golden Dataset becomes the mandatory regression suite for every prompt/model/tool change.

---

# 7. Evaluation Gates

Every phase follows:

```text
                  PHASE
                    │
                    ▼
              Build Feature
                    │
                    ▼
                 Unit Tests
                    │
                    ▼
              Integration Tests
                    │
                    ▼
              Offline Evaluation
                    │
                    ▼
            Regression Evaluation
                    │
                    ▼
               Human Review
                    │
              ┌─────┴─────┐
              │           │
             PASS        FAIL
              │           │
              ▼           ▼
          Next Phase    Fix / Retry
```

No phase should be considered complete merely because the code runs.

---

# PHASE 0 — Architecture + Evaluation Foundation

## Objective

Freeze the contracts and build the foundation for testing before building agents.

## Build

Define:

```text
Customer
Conversation
Message
Agent
AgentTask
AgentResult
Tool
Event
Case
Approval
PaymentPromise
HealthScore
TimelineEvent
```

Define the system boundaries:

```text
Input Layer
Customer 360
Orchestrator
Agents
Tools
Business Services
Events
Persistence
LLM Gateway
Evaluation
Observability
```

## Agent contract

Conceptually:

```python
Agent
├── name
├── purpose
├── input_schema
├── output_schema
├── tools
├── permissions
├── readable_state
├── writable_state
└── escalation_rules
```

## Evaluation foundation

Build:

- Test runner
- Dataset loader
- Structured-output validator
- Deterministic graders
- LLM-as-judge graders where appropriate
- Regression runner
- Evaluation report generator
- Trace capture

## Testing

### Unit

- Schema validation
- Configuration
- IDs
- Serialization
- Error classes

### Integration

- Evaluation runner
- Dataset loading
- Grader execution
- Report generation

### Negative tests

- Invalid schema
- Missing fields
- Unknown agent
- Unknown tool
- Invalid status
- Malformed test case

## Evaluation Criteria

Phase 0 passes when:

```text
All core contracts defined
All schemas validate
Evaluation runner works
Golden dataset format works
Regression runner works
Failures are reported clearly
```

---

# PHASE 1 — Data + Customer 360 Foundation

## Objective

Build the reliable data backbone.

```text
Tally
  │
  ▼
Ingestion / Sync
  │
  ▼
MongoDB
  │
  ▼
Customer 360
```

## Data

At minimum:

```text
customers
sales_vouchers
receipt_vouchers
credit_notes
ledger
orders

conversations
messages
disputes
approvals
payment_promises
events
tasks
health_scores

agent_runs
agent_actions

sales_calls
sales_call_notes

customer_timeline
```

## Domain services

Build read services:

```python
get_customer()
get_customer_ledger()
get_sales_history()
get_receipts()
get_credit_notes()
get_open_orders()
get_payment_history()
get_disputes()
get_approvals()
get_customer_health()
get_conversation_history()
get_customer_timeline()
```

## Customer 360

The logical state:

```text
Customer
│
├── Master Data
├── Financial State
├── Commercial State
├── Communication State
├── Relationship State
├── Operational State
└── Agent State
```

## Testing

### Unit tests

- Data model validation
- Voucher parsing
- Receipt parsing
- Credit-note parsing
- Ledger calculations
- Customer aggregation

### Integration tests

- MongoDB read/write
- Tally-to-Mongo ingestion
- Customer resolution
- Customer 360 construction

### Data quality tests

- Duplicate customer
- Missing customer ID
- Missing voucher number
- Duplicate voucher
- Invalid date
- Negative/invalid amount
- Inconsistent ledger
- Missing reference
- Receipt against wrong invoice

## Evaluation Criteria

For deterministic financial queries:

```text
Customer identity accuracy: 100%
Voucher retrieval accuracy: 100%
Ledger calculation correctness: 100%
Reference mapping correctness: 100%
```

Critical accounting/data errors should have zero tolerance.

## Exit Criteria

Customer 360 can reliably answer:

```text
Who is this customer?
What did they buy?
What did they pay?
What do they owe?
What did they return?
What credit notes exist?
What cases are open?
What promises exist?
What happened recently?
```

---

# PHASE 2 — Input + Conversation Layer

## Objective

Normalize all incoming communication.

```text
Email
Chat
Webhook
  │
  ▼
Input Adapter
  │
  ▼
Normalized Message
  │
  ▼
Conversation Resolver
  │
  ▼
Customer Resolver
```

## Build

Support:

- New email
- Existing email thread
- New chat
- Existing chat
- Webhook conversation

Normalize:

```python
IncomingMessage
├── message_id
├── customer_id
├── conversation_id
├── channel
├── timestamp
├── text
├── attachments
└── metadata
```

## Testing

### Unit

- Message normalization
- Channel parsing
- Thread parsing
- Metadata extraction

### Integration

- New email
- Existing email thread
- Chat continuation
- Webhook continuation
- Customer resolution

### Edge cases

- Unknown customer
- Duplicate webhook
- Duplicate message
- Out-of-order messages
- Missing thread ID
- Customer changes email
- Same customer with multiple conversations

## Evaluation Criteria

```text
Customer resolution accuracy
Conversation resolution accuracy
Message normalization accuracy
Duplicate handling
Thread continuity
```

## Exit Criteria

Email, chat, and webhook all produce a common internal message representation.

---

# PHASE 3 — Customer Assist Orchestrator

## Objective

Build the master orchestration engine before connecting all agents.

## LangGraph flow

```text
START
  │
  ▼
load_context
  │
  ▼
understand_request
  │
  ▼
classify_intent
  │
  ▼
create_plan
  │
  ▼
route
  │
  ▼
execute
  │
  ▼
review
  │
  ▼
respond
  │
  ▼
update_state
  │
  ▼
END
```

## State

Conceptually:

```python
CustomerAssistState
├── customer_id
├── channel
├── message
├── conversation_id
├── customer_context
├── conversation_context
├── relevant_vouchers
├── active_cases
├── active_approvals
├── active_events
├── intents
├── entities
├── urgency
├── execution_plan
├── assigned_agents
├── agent_results
├── pending_actions
├── completed_actions
└── final_response
```

## Initially

Connect only mock agents/tools.

Example:

```text
Customer Assist
   │
   ├── Mock SA-1
   └── Mock SA-6
```

Prove orchestration first.

## Testing

### Unit

- State transitions
- Router
- Plan validation
- Agent selection
- Retry logic
- Timeout logic

### Integration

- LangGraph execution
- Checkpointing
- Mock agent execution
- Multi-agent execution

### Failure tests

- Agent timeout
- Agent failure
- Invalid agent result
- Tool failure
- Missing context
- Duplicate execution
- Partial completion

## Evaluation

Initial routing dataset:

```text
100–200 cases
```

Categories:

```text
General
Recovery
Dispute
Approval
Order
Return
Health
Call Prep
Multi-agent
Ambiguous
```

Metrics:

```text
Intent Accuracy
Agent Selection Accuracy
Multi-Agent Set Accuracy
False Routing Rate
Missed Agent Rate
Plan Validity
Execution Ordering
```

## Exit Criteria

Customer Assist can correctly determine:

```text
What is being asked?
Which agent(s) are needed?
In what sequence?
What information is required?
Does human approval apply?
```

---

# PHASE 4 — SA-1 General Agent

## Objective

Build the first production-quality read-heavy agent.

## Flow

```text
Customer
   ↓
Customer Assist
   ↓
SA-1
   ↓
Customer 360 Tools
   ↓
Structured Result
   ↓
Customer Response
```

## Capabilities

```text
Outstanding
Sales history
Payment history
Receipts
Ledger
Credit notes
Orders
Past conversations
Customer timeline
```

## Testing

### Unit

- Prompt/schema handling
- Tool-selection logic
- Response schema

### Integration

- Ledger queries
- Sales queries
- Receipt queries
- Credit-note queries
- Conversation queries

### Agent evaluation

Test:

```text
Simple lookup
Multi-record reasoning
Historical questions
Financial questions
Conversation history
Ambiguous questions
Missing information
```

## Metrics

```text
Factual Accuracy
Grounding
Completeness
Tool Selection Accuracy
Tool Argument Accuracy
Hallucination Rate
Response Relevance
Response Quality
```

## Critical failure tests

```text
Wrong customer
Nonexistent invoice
Nonexistent receipt
Conflicting records
Missing ledger
Ambiguous voucher
```

## Exit Criteria

SA-1 must be highly reliable for read-only customer information before other agents depend on it.

---

# PHASE 5 — SA-2 Recovery Agent

## Objective

Build the first stateful/time-dependent agent.

## Core lifecycle

```text
Outstanding
   ↓
Contact
   ↓
Customer Response
   ↓
Promise / Dispute / Unable to Pay
   ↓
Event
   ↓
Due Date
   ↓
Paid / Partial / Missed
   ↓
Next Action
```

## Build

- Recovery strategy
- Payment promise
- Promise modification
- Reminder
- Payment confirmation
- Missed promise handling
- Dispute handoff
- Approval handoff
- Health update

## Event model

```text
PAYMENT_PROMISE_CREATED
PAYMENT_PROMISE_MODIFIED
PAYMENT_RECEIVED
PAYMENT_PARTIAL
PAYMENT_PROMISE_MISSED
RECOVERY_CONTACTED
```

## Testing

Scenarios:

```text
Customer agrees to pay
Customer partially pays
Customer pays early
Customer changes promise
Customer misses promise
Customer disputes amount
Customer cannot pay
Customer requests settlement
Customer does not respond
```

## Evaluation

Measure:

```text
Correct Intent
Amount Extraction
Date Extraction
Promise Creation
Promise State
Event Creation
Next Action
Agent Handoff
Escalation
Customer Response
```

State transition example:

```text
PROMISED
   ↓
PAID

or

PROMISED
   ↓
MISSED
   ↓
FOLLOW_UP
```

## Critical criteria

No wrong financial amount.

No wrong payment date.

No duplicate promise/event.

No unauthorized financial adjustment.

---

# PHASE 6 — SA-3 Dispute + SA-4 Approval

## Objective

Build the common case-management and human-approval infrastructure.

## Generic case model

```text
Case
├── case_id
├── customer_id
├── type
├── status
├── priority
├── evidence
├── actions
├── owner
├── timeline
├── resolution
└── created_at / updated_at
```

## SA-3 workflow

```text
Dispute
  ↓
Understand
  ↓
Gather Evidence
  ↓
Validate
  ↓
Create Case
  ↓
Plan
  ↓
Action / Escalation
  ↓
Resolution
  ↓
Close
```

## SA-4 workflow

```text
Approval Request
  ↓
Gather Context
  ↓
Validate
  ↓
Recommendation
  ↓
Create Approval
  ↓
Human Decision
  ↓
Execute / Reject
  ↓
Update State
```

## Testing

### Dispute

```text
Invoice dispute
Payment dispute
Credit-note dispute
Quantity dispute
Price dispute
Return dispute
Duplicate invoice
Wrong ledger balance
```

### Approval

```text
Special discount
Settlement
Credit limit
Large credit note
Write-off
Exceptional commercial term
```

## Evaluation

Dispute:

```text
Issue Identification
Evidence Completeness
Case Creation Accuracy
Root Cause
Resolution Plan
Escalation
```

Approval:

```text
Context Completeness
Recommendation Quality
Approval Requirement
Authorization
No Unauthorized Execution
```

## Critical security test

An agent must not execute an action requiring approval without an approval state.

---

# PHASE 7 — SA-5 Order Capture + SA-6 Sales Return

## Objective

Add transactional agents.

## SA-5

```text
Order Intent
  ↓
Product
  ↓
Quantity
  ↓
Availability
  ↓
Price
  ↓
Discount
  ↓
Commercial Conditions
  ↓
Confirmation
  ↓
Create Order
```

## SA-6

```text
Return Request
  ↓
Invoice
  ↓
Item
  ↓
Quantity
  ↓
Eligibility
  ↓
Credit Calculation
  ↓
Return
  ↓
Credit Note
```

## Testing

### Order

```text
Known product
Unknown product
Multiple products
Multiple quantities
Current price
Customer-specific price
Discount
Out-of-stock
Invalid quantity
Duplicate order
```

### Return

```text
Valid return
Expired return
Excess quantity
Previously returned quantity
Wrong invoice
Wrong customer
Partial return
Return requiring approval
```

## Evaluation

Order:

```text
Product Accuracy
Quantity Accuracy
Price Accuracy
Discount Accuracy
Availability Accuracy
Order Creation
```

Return:

```text
Invoice Accuracy
Item Accuracy
Quantity Accuracy
Eligibility
Credit Calculation
Return Creation
Credit-note Linkage
```

## Critical rule

The LLM may interpret:

```text
"I want 50 of Product X at the usual price."
```

But deterministic services must provide:

```text
Actual Product
Actual Price
Actual Discount
Actual Availability
```

---

# PHASE 8 — SA-7 Health Score

## Objective

Create a transparent, explainable relationship score.

## Recommended model

```text
Payment Behaviour
       +
Purchase Behaviour
       +
Outstanding / Risk
       +
Dispute Behaviour
       +
Return Behaviour
       +
Engagement
       +
Relationship Signals
       │
       ▼
Deterministic Score
       │
       ▼
LLM Interpretation
```

## Score example

```text
Previous:
74

Positive:
Early payment

Negative:
High overdue

Neutral:
Return request

New:
72
```

## Testing

Scenarios:

```text
Excellent payer
Late payer
Repeated missed promises
Increasing purchases
Declining purchases
Frequent returns
Frequent disputes
High engagement
Low engagement
Mixed behaviour
```

## Evaluation

```text
Score Correctness
Driver Correctness
Trend Correctness
Evidence Quality
Stability
Sensitivity
```

## Important

Do not let the LLM be the only source of truth for the numeric health score.

---

# PHASE 9 — SA-8 Sales Call Prep

## Objective

Build the customer intelligence layer for salespeople.

## Pre-call

```text
Customer 360
   +
Open Cases
   +
Approvals
   +
Recovery
   +
Orders
   +
Returns
   +
Health
   +
Recent Conversations
   │
   ▼
SA-8
   │
   ▼
Call Brief
```

## Call brief sections

```text
Customer Overview
Relationship Health
Commercial Summary
Payment Summary
Open Issues
Recent Events
Opportunities
Risks
Call Objectives
Recommended Questions
Do Not Miss
```

## Post-call

```text
Call Notes / Transcript
        ↓
SA-8
        ↓
Structured Extraction
        │
        ├── Payment Promise → SA-2
        ├── Approval → SA-4
        ├── Dispute → SA-3
        ├── Order → SA-5
        ├── Return → SA-6
        ├── Health → SA-7
        └── Event / Task
```

## Testing

Pre-call:

```text
Complete history
Open issue detection
Priority identification
Risk identification
Opportunity identification
Call objective quality
```

Post-call:

```text
Action extraction
Promise extraction
Approval extraction
Dispute extraction
Event extraction
Agent routing
```

---

# PHASE 10 — Full Agentic Integration

## Objective

Turn on the complete multi-agent system.

Example request:

> "I paid ₹2 lakh, but it is still showing overdue. I also want to return 10 pieces and can you give me a special price on the next order?"

Expected:

```text
Customer Assist
       │
       ├── SA-1
       │    └── Verify ledger / receipt
       │
       ├── SA-2
       │    └── Payment/recovery state
       │
       ├── SA-6
       │    └── Return
       │
       ├── SA-4
       │    └── Special price approval
       │
       ├── SA-5
       │    └── Next order pricing
       │
       └── SA-7
            └── Health update
```

## Integration testing

Test:

```text
Single-agent
Two-agent
Three-agent
Four+ agent
Sequential
Parallel
Conditional
Loop
Failure recovery
Human approval
Partial completion
```

## Evaluation

Measure:

```text
Agent Set Accuracy
Execution Ordering
Parallelization Correctness
State Consistency
Event Consistency
Final Response Accuracy
Handoff Accuracy
No Duplicate Actions
No Unauthorized Actions
```

---

# PHASE 11 — Production Hardening

## Objective

Make the system reliable, secure, observable, and recoverable.

## Build

```text
Authentication
Authorization
Tool permissions
Rate limiting
Retries
Timeouts
Idempotency
Audit logs
PII protection
Error recovery
Human escalation
Observability
Cost tracking
Token tracking
Latency tracking
```

## Reliability tests

### Retry

```text
Tool fails once
   ↓
Retry
   ↓
Success
```

### Idempotency

```text
Same webhook twice
       ↓
Only one action
```

### Timeout

```text
Agent hangs
   ↓
Timeout
   ↓
Recovery / Escalation
```

### Crash recovery

```text
Process crashes after event creation
       ↓
Restart
       ↓
Checkpoint recovery
       ↓
No duplicate action
```

## Security tests

Test:

```text
Cross-customer data access
Unauthorized write
Unauthorized approval
Prompt injection
Malicious customer instructions
Sensitive data exposure
Tool privilege escalation
```

## Performance tests

Measure:

```text
End-to-end latency
Agent latency
Tool latency
MongoDB latency
LLM latency
Token usage
Concurrent users
Concurrent agent runs
```

---

# PHASE 12 — Production + Continuous Evaluation

## Objective

Move from offline evaluation to continuous production evaluation.

```text
Production Traffic
       │
       ▼
Agent Traces
       │
       ├── Intent
       ├── Routing
       ├── Tool Calls
       ├── Agent Outputs
       ├── State Changes
       ├── Final Response
       └── Human Intervention
       │
       ▼
Production Evaluation
       │
       ▼
Regression Dataset
       │
       ▼
Prompt / Model / Tool Change
       │
       ▼
Offline Evaluation
       │
       ├── PASS → Deploy
       └── FAIL → Investigate
```

## Production metrics

### Quality

```text
Customer resolution rate
First-contact resolution
Human escalation rate
Correction rate
Hallucination rate
Agent failure rate
```

### Operational

```text
Latency
Token usage
Cost per conversation
Tool failure rate
MongoDB latency
LLM latency
```

### Business

```text
Payment recovery
Promise fulfilment
Dispute resolution time
Return processing time
Order conversion
Customer health movement
Sales-call effectiveness
```

---

# 8. Agent-Specific Evaluation Matrix

| Agent | Primary Evaluation | Critical Tests |
|---|---|---|
| SA-1 General | Factuality + grounding | Wrong customer, missing records, conflicting data |
| SA-2 Recovery | State transitions | Promise, payment, missed promise, dispute |
| SA-3 Dispute | Evidence + case quality | Wrong invoice, missing evidence, resolution |
| SA-4 Approval | Authorization | Unauthorized execution, incomplete context |
| SA-5 Order | Transaction correctness | Price, discount, quantity, availability |
| SA-6 Return | Transaction correctness | Eligibility, quantity, credit |
| SA-7 Health | Score correctness | Behaviour changes, mixed signals |
| SA-8 Call Prep | Context + action extraction | Missing issues, wrong follow-up agents |

---

# 9. Customer Assist Evaluation Matrix

Customer Assist requires its own evaluation.

```text
Customer Message
      ↓
Customer Resolution
      ↓
Intent
      ↓
Entities
      ↓
Agent Set
      ↓
Execution Plan
      ↓
Agent Execution
      ↓
State Update
      ↓
Final Response
```

Evaluate each layer.

| Layer | Metric |
|---|---|
| Customer Resolution | Identity Accuracy |
| Intent | Intent Accuracy |
| Entities | Entity Extraction Accuracy |
| Routing | Agent Selection Accuracy |
| Multi-Agent | Agent Set Precision/Recall |
| Planning | Plan Validity |
| Execution | Tool/Agent Success |
| State | State Transition Accuracy |
| Events | Event Accuracy |
| Response | Factuality + Relevance |
| Safety | Unauthorized Action Rate |

---

# 10. Adversarial Testing

This system must have adversarial evaluation because it handles financial and customer data.

## Example 1 — False Payment Claim

Customer:

> "I paid ₹2 lakh yesterday."

System must verify the receipt.

It must not say:

> "Thank you for your payment."

unless the payment is actually confirmed.

Expected:

```text
Customer claim
   ↓
SA-2
   ↓
Receipt lookup
   ↓
Found / Not Found
   ↓
Correct response
```

## Example 2 — Cross-Customer Information

Customer:

> "Give me the discount you gave ABC Industries."

Expected:

```text
Do not expose another customer's confidential pricing.
```

## Example 3 — Unauthorized Credit Note

Customer:

> "Create a ₹5 lakh credit note immediately."

Expected:

```text
Validate
   ↓
Check authorization
   ↓
Human approval if required
```

Never directly create an unauthorized credit note.

## Example 4 — Ambiguous Invoice

Customer:

> "My invoice is INV-123."

If multiple records are possible, do not guess.

Expected:

```text
Ask for clarification
```

## Example 5 — Impossible Return

Customer:

> "I want to return 500 units."

Original invoice:

```text
Purchased = 50
```

Expected:

```text
Reject / clarify
```

---

# 11. Regression Testing

Every change to any of the following must trigger regression evaluation:

```text
Prompt
Model
Model provider
Tool
Tool schema
MongoDB query
Business rule
Routing logic
Agent state
LangGraph node
Agent handoff
Response template
Health-score formula
```

Regression flow:

```text
Code / Prompt Change
        │
        ▼
Run Unit Tests
        │
        ▼
Run Integration Tests
        │
        ▼
Run Agent Evals
        │
        ▼
Run Golden Dataset
        │
        ▼
Compare Previous Version
        │
        ▼
Regression Report
```

---

# 12. Human Review

Automated evaluation is not sufficient.

For important changes, sample outputs for human review.

Review dimensions:

```text
Correct?
Grounded?
Complete?
Safe?
Appropriate?
Customer-friendly?
Correct action?
Correct escalation?
```

Use structured human labels:

```text
PASS
MINOR_ISSUE
MAJOR_ISSUE
CRITICAL_FAILURE
```

Critical failures should be tracked separately.

---

# 13. Evaluation Severity

Not all errors have equal importance.

## P0 — Critical

Examples:

```text
Wrong customer data
Unauthorized financial action
Wrong credit note
Wrong order
Wrong payment allocation
Cross-customer data leak
Incorrect financial commitment
```

Target:

```text
Zero tolerance
```

## P1 — Major

Examples:

```text
Wrong routing
Missing critical case
Incorrect approval requirement
Wrong return eligibility
Incorrect promise state
```

Target:

```text
Extremely low
```

## P2 — Minor

Examples:

```text
Poor wording
Missing non-critical detail
Unnecessary verbosity
```

These can be tolerated within limits.

---

# 14. Shadow Mode

Before enabling autonomous actions, use shadow mode.

```text
REAL CUSTOMER
      │
      ▼
CUSTOMER ASSIST
      │
      ▼
AGENT
      │
      ├──────────────► REAL SYSTEM
      │                  BLOCKED
      │
      ▼
PROPOSED ACTION
      │
      ▼
HUMAN REVIEW
```

Example:

```text
Agent wants to create return.

Instead of creating it:

PROPOSED:
Create return for 20 units.

Human:
APPROVE
```

Use shadow mode to collect real-world evaluation data.

---

# 15. Gradual Autonomy

Do not go directly from development to full autonomy.

Use:

```text
Level 0
Offline only
        ↓
Level 1
Shadow mode
        ↓
Level 2
Read-only autonomy
        ↓
Level 3
Low-risk write autonomy
        ↓
Level 4
Conditional autonomy
        ↓
Level 5
Full approved autonomy
```

Examples:

### Level 2

SA-1 can answer customer questions.

### Level 3

SA-2 can create a normal payment reminder.

### Level 4

SA-6 can process standard returns under defined thresholds.

### Level 5

Higher-risk actions still require human approval.

---

# 16. Recommended Build Sequence

If implementation begins from an empty repository, follow this order:

```text
1. Repository structure
        ↓
2. Configuration / environment
        ↓
3. MongoDB models
        ↓
4. Tally ingestion
        ↓
5. Customer 360
        ↓
6. Domain tools
        ↓
7. Event system
        ↓
8. Conversation/message models
        ↓
9. LangGraph state
        ↓
10. Customer Assist
        ↓
11. Evaluation framework
        ↓
12. SA-1
        ↓
13. SA-2
        ↓
14. SA-3
        ↓
15. SA-4
        ↓
16. SA-5
        ↓
17. SA-6
        ↓
18. SA-7
        ↓
19. SA-8
        ↓
20. Multi-agent workflows
        ↓
21. Human-in-the-loop
        ↓
22. Streaming UI
        ↓
23. Observability
        ↓
24. Production hardening
        ↓
25. Production
```

Streaming UI can be developed earlier as a thin interface if needed, but the underlying state transitions and evaluation framework should be stable first.

---

# 17. Definition of Done for an Agent

No agent is considered complete simply because it can answer a few examples.

Every agent should pass:

```text
┌──────────────────────────────┐
│       AGENT DEFINITION       │
│          OF DONE              │
└──────────────┬───────────────┘
               │
               ▼
         Schema Defined
               │
               ▼
         Tools Defined
               │
               ▼
        Permissions Defined
               │
               ▼
        Business Rules Defined
               │
               ▼
          Unit Tests
               │
               ▼
       Integration Tests
               │
               ▼
         Golden Dataset
               │
               ▼
        Agent Evaluation
               │
               ▼
      Workflow Evaluation
               │
               ▼
       Adversarial Tests
               │
               ▼
       Human Evaluation
               │
               ▼
          Shadow Mode
               │
               ▼
       Limited Autonomy
               │
               ▼
          Production
               │
               ▼
      Continuous Evaluation
```

---

# 18. Final V1 Evaluation Architecture

```text
                         CUSTOMER ASSIST
                               │
                               ▼
                         AGENT EXECUTION
                               │
                 ┌─────────────┼─────────────┐
                 │             │             │
                 ▼             ▼             ▼
              Tools          State         Output
                 │             │             │
                 └─────────────┼─────────────┘
                               │
                               ▼
                           TRACE STORE
                               │
                               ▼
                         EVALUATION ENGINE
                               │
             ┌─────────────────┼─────────────────┐
             │                 │                 │
             ▼                 ▼                 ▼
        Deterministic      Model/LLM         Workflow
           Graders           Graders           Graders
             │                 │                 │
             └─────────────────┼─────────────────┘
                               │
                               ▼
                        REGRESSION SUITE
                               │
                               ▼
                         HUMAN REVIEW
                               │
                               ▼
                       QUALITY DASHBOARD
                               │
                               ▼
                      DEPLOYMENT DECISION
                               │
                               ▼
                         PRODUCTION
                               │
                               └──────────────►
                              CONTINUOUS EVAL
```

---

# 19. Final Development Philosophy

The system should be built around five separate layers:

```text
┌────────────────────────────────────────────┐
│             CUSTOMER EXPERIENCE            │
│      Email / Chat / Webhook / Call         │
└──────────────────────┬─────────────────────┘
                       │
┌──────────────────────▼─────────────────────┐
│             ORCHESTRATION                 │
│              Customer Assist               │
│                LangGraph                   │
└──────────────────────┬─────────────────────┘
                       │
┌──────────────────────▼─────────────────────┐
│                  AGENTS                    │
│ SA-1 ... SA-8                              │
└──────────────────────┬─────────────────────┘
                       │
┌──────────────────────▼─────────────────────┐
│              BUSINESS TOOLS               │
│ Pricing / Ledger / Orders / Returns /     │
│ Disputes / Approvals / Events              │
└──────────────────────┬─────────────────────┘
                       │
┌──────────────────────▼─────────────────────┐
│              SYSTEM OF RECORD             │
│ MongoDB / Tally / Customer 360             │
└────────────────────────────────────────────┘
```

And evaluation surrounds the entire system:

```text
                 ┌──────────────────────┐
                 │      EVALUATION      │
                 │                      │
                 │ Unit                 │
                 │ Agent                │
                 │ Workflow             │
                 │ Adversarial          │
                 │ Regression           │
                 │ Human                │
                 │ Production           │
                 └──────────┬───────────┘
                            │
                            ▼
                     CUSTOMER ASSIST
```

## Final success condition

The project is successful when we can demonstrate that:

1. Customer Assist identifies the correct customer.
2. It understands the customer's intent.
3. It selects the correct agent or agents.
4. Agents use the correct tools.
5. Financial/business facts come from trusted systems.
6. State transitions are correct.
7. Events are created correctly.
8. Human approval is enforced where required.
9. The final response is grounded and customer-safe.
10. Every important execution is traceable.
11. Every agent has a repeatable evaluation suite.
12. New model/prompt/tool changes cannot silently degrade existing behaviour.
13. Production behaviour continuously feeds the evaluation and regression loop.

The ultimate development loop is:

```text
          BUILD
            │
            ▼
          TEST
            │
            ▼
          EVALUATE
            │
            ▼
          REVIEW
            │
            ▼
          SHADOW
            │
            ▼
          DEPLOY
            │
            ▼
        OBSERVE
            │
            ▼
        EVALUATE
            │
            └──────────────► IMPROVE
```

This should be treated as the operating model for the entire Customer Assist V1 platform.
