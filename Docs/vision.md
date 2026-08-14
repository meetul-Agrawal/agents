# Customer Assist — Agentic Orchestration V1

## 1. Vision

Customer Assist is a customer-service agentic operating system built around a master orchestrator and specialized sub-agents.

The system receives customer interactions from email, chat, webhook interfaces, and eventually post-call data. It resolves the customer, builds a Customer 360 context, understands intent, assigns one or more specialized agents, monitors execution, aggregates results, updates customer state, and produces the final customer-facing response.

The core idea is:

```text
             ┌──────────────────────────┐
             │      CUSTOMER ASSIST     │
             │                          │
             │      "What is needed?"   │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │        AGENTS            │
             │                          │
             │      "How to do it?"     │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │         TOOLS            │
             │                          │
             │      "Do the work."      │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │       DATA / SYSTEMS     │
             │                          │
             │ MongoDB / Tally / Events │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │      CUSTOMER 360        │
             │                          │
             │ "What is the new state?" │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │      NEXT BEST ACTION    │
             └────────────┬─────────────┘
                          │
                          └──────────────► LOOP
```

---

# 2. Technology Stack

```text
Python
LangGraph
OpenAI SDK
NVIDIA NIM LLM API
Structured Output
Streaming Output
MongoDB
Tally voucher/customer data
```

Tally/MongoDB provides business data such as:

- Sales vouchers
- Receipt vouchers
- Receipts against references
- On-account receipts
- Credit notes / sales returns
- Ledger information
- Customer master data
- Orders
- Historical financial activity

---

# 3. Complete V1 Master Architecture

```text
================================================================================
                    CUSTOMER ASSIST — AGENTIC ORCHESTRATION
                         V1 MASTER ARCHITECTURE
================================================================================


                              CUSTOMER
                                 │
                                 │
                    ┌────────────┴────────────┐
                    │                         │
                 EMAIL                      CHAT
                    │                         │
                    │                    WEBHOOK / UI
                    │                         │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │     INPUT ADAPTER       │
                    │─────────────────────────│
                    │ • Email Parser          │
                    │ • Chat Parser            │
                    │ • Webhook Parser        │
                    │ • Thread Resolver       │
                    │ • Message Normalizer    │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   CUSTOMER RESOLVER     │
                    │─────────────────────────│
                    │ Find customer by:       │
                    │ • Email                 │
                    │ • Phone                 │
                    │ • Customer ID           │
                    │ • Account Code          │
                    │ • Conversation Context  │
                    └────────────┬────────────┘
                                 │
                       ┌─────────┴─────────┐
                       │                   │
                  CUSTOMER FOUND       NEW CUSTOMER
                       │                   │
                       │                   ▼
                       │          ┌───────────────────┐
                       │          │ CUSTOMER ONBOARD  │
                       │          │ / CREATE PROFILE  │
                       │          └─────────┬─────────┘
                       │                    │
                       └──────────┬─────────┘
                                  │
                                  ▼
                 ┌────────────────────────────────┐
                 │       CUSTOMER 360 BUILDER      │
                 │────────────────────────────────│
                 │                                │
                 │ Customer Master Data            │
                 │ Sales History                   │
                 │ Orders                          │
                 │ Invoices                        │
                 │ Receipts                        │
                 │ Ledger                          │
                 │ Credit Notes                    │
                 │ Payment Behaviour               │
                 │ Conversations                   │
                 │ Previous Emails                 │
                 │ Previous Chats                  │
                 │ Disputes                        │
                 │ Approvals                       │
                 │ Events                          │
                 │ Payment Promises                │
                 │ Health Score                    │
                 │ Active Cases                    │
                 │                                │
                 └───────────────┬────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │    CUSTOMER ASSIST      │
                    │   MASTER ORCHESTRATOR   │
                    │─────────────────────────│
                    │                         │
                    │ 1. Understand           │
                    │ 2. Classify Intent      │
                    │ 3. Extract Entities     │
                    │ 4. Determine Priority   │
                    │ 5. Check Customer State │
                    │ 6. Build Execution Plan │
                    │ 7. Assign Agent(s)      │
                    │ 8. Monitor Execution    │
                    │ 9. Validate Results     │
                    │10. Synthesize Response  │
                    │11. Update State         │
                    │                         │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      INTENT ROUTER      │
                    └────────────┬────────────┘
                                 │
       ┌──────────┬──────────┬───┴────┬──────────┬──────────┬──────────┐
       │          │          │        │          │          │          │
       ▼          ▼          ▼        ▼          ▼          ▼          ▼
     SA-1       SA-2       SA-3     SA-4       SA-5       SA-6       SA-7
    GENERAL   RECOVERY   DISPUTE  APPROVAL    ORDER      RETURN     HEALTH
                                            CAPTURE
       │          │          │        │          │          │          │
       └──────────┴──────────┴────────┴──────────┴──────────┴──────────┘
                                      │
                                      ▼
                                   SA-8
                              SALES CALL PREP
                                      │
                                      ▼
                         ┌────────────────────────┐
                         │    RESULT AGGREGATOR   │
                         │────────────────────────│
                         │ • Agent Results        │
                         │ • Actions              │
                         │ • Cases                │
                         │ • Events               │
                         │ • Approvals            │
                         │ • Customer Message     │
                         └───────────┬────────────┘
                                     │
                                     ▼
                         ┌────────────────────────┐
                         │     STATE UPDATER      │
                         │────────────────────────│
                         │ MongoDB                │
                         │ • Customer             │
                         │ • Timeline             │
                         │ • Cases                │
                         │ • Events               │
                         │ • Health Score         │
                         │ • Agent Runs            │
                         └───────────┬────────────┘
                                     │
                                     ▼
                         ┌────────────────────────┐
                         │   RESPONSE GENERATOR   │
                         │────────────────────────│
                         │ Customer-safe response │
                         │ + actions              │
                         │ + next steps           │
                         └───────────┬────────────┘
                                     │
                                     ▼
                                  CUSTOMER
```

---

# 4. Agents

## SA-1 — General / Customer Information Agent

Primary responsibility:

> Retrieve, understand, summarize, and explain customer-related information.

Examples:

- Outstanding balance
- Payment history
- Previous orders
- Sales history
- Credit notes
- Receipts
- Ledger
- Previous conversations
- Previous disputes
- Previous approvals

The agent can use read-only Mongo/domain tools as necessary.

Important boundary:

SA-1 should answer informational questions, but should route operational requests to the appropriate specialized agent.

Example:

```text
"How much do I owe?"
    -> SA-1

"I want to return these products."
    -> SA-6

"I dispute this invoice."
    -> SA-3
```

---

## SA-2 — Recovery Agent

Primary responsibility:

> Manage customer outstanding payments, payment promises, reminders, repayment patterns, and recovery interactions.

Workflow:

```text
Outstanding
   ↓
Contact Customer
   ↓
Customer Response
   ↓
 ┌───────────────┬──────────────┬──────────────┐
 │               │              │
Will Pay        Dispute        Can't Pay
 │               │              │
Promise         SA-3           SA-4?
 │
Event
 │
Due Date
 │
 ├── Paid
 │     ↓
 │   Acknowledge
 │
 └── Not Paid
       ↓
    Reminder
       ↓
    New Update
       ↓
    New Promise /
    Dispute /
    Approval
```

Payment promise lifecycle:

```text
                PAYMENT PROMISE
                      │
                      ▼
              ┌───────────────┐
              │ PROMISED      │
              │ ₹2,00,000     │
              │ 20-Aug        │
              └───────┬───────┘
                      │
                      ▼
                  WAITING
                      │
          ┌───────────┴────────────┐
          │                        │
       PAYMENT                  NO PAYMENT
       RECEIVED                    │
          │                        │
          ▼                        ▼
     SA-2 Recovery           EVENT TRIGGER
          │                        │
          ▼                        ▼
    Verify Receipt            Promise Missed
          │                        │
          ▼                        ▼
    Acknowledge              Contact Customer
          │                        │
          ▼                        ▼
    SA-7 Health              New Promise /
                              Dispute /
                              Approval
```

---

## SA-3 — Dispute Agent

Primary responsibility:

> Understand the customer's dispute, gather evidence, create and manage a case, determine the required plan/action, and acknowledge the customer.

Workflow:

```text
Customer Dispute
      ↓
Understand Issue
      ↓
Identify Invoice / Order / Voucher
      ↓
Gather Evidence
      ↓
Validate
      ↓
Create Dispute Case
      ↓
Determine Plan
      ↓
Request Other Agent / Human Action
      ↓
Track Case
      ↓
Communicate Status
      ↓
Close Case
```

Example case:

```text
DSP-2026-00123

Issue:
Customer disputes overdue amount.

Evidence:
• Invoice INV-1024
• Return request
• Credit note status
• Ledger balance

Finding:
Return exists but credit note has not yet
been reflected in the ledger.

Recommended action:
Process/post credit note and re-evaluate ledger.
```

---

## SA-4 — Approval Agent

Primary responsibility:

> Create, manage, and track approval requests based on customer context.

Typical scenarios:

- Special discount
- Settlement
- Credit limit change
- Large credit note
- Write-off
- Exceptional commercial terms

Workflow:

```text
Approval Request
      ↓
Gather Context
      ↓
Financial History
      ↓
Customer Health
      ↓
Past Approvals
      ↓
Disputes / Orders / Payments
      ↓
Prepare Recommendation
      ↓
Create Approval
      ↓
Human Decision
      │
      ├── APPROVED
      │
      └── REJECTED
      ↓
Update State
      ↓
Communicate
```

Example:

```text
APR-2026-00045

Request:
Approve special settlement.

Customer:
CUST-00124

Outstanding:
₹4,82,500

Customer proposal:
Pay ₹2,00,000 immediately.

Recommendation:
Review based on payment history,
health score, dispute history,
and previous settlements.
```

---

## SA-5 — Order Capture Agent

Primary responsibility:

> Process customer orders and provide current, system-derived pricing and discount information.

Workflow:

```text
Order Intent
     ↓
Identify Products
     ↓
Check Availability
     ↓
Get Current Price
     ↓
Get Customer-Specific Price
     ↓
Get Applicable Discount
     ↓
Check Commercial Conditions
     ↓
Order Confirmation
     ↓
Create Order
     ↓
Customer Acknowledgement
```

Important principle:

```text
LLM
  -> interprets customer intent

System / Tool
  -> provides actual price

System / Tool
  -> calculates actual discount

System / Tool
  -> confirms inventory
```

The LLM should never invent a price or discount.

---

## SA-6 — Sales Return Agent

Primary responsibility:

> Process customer sales return requests and initiate the appropriate return/credit-note workflow.

Workflow:

```text
Return Request
      ↓
Identify Invoice / Order
      ↓
Validate Items
      ↓
Check Return Policy
      ↓
Check Quantity
      ↓
Check Time Window
      ↓
Check Previous Returns
      ↓
Calculate Expected Credit
      ↓
Create Return
      ↓
Create / Trigger Credit Note
      ↓
Update Customer State
      ↓
Health Score
      ↓
Customer Acknowledgement
```

Example:

```text
Invoice: INV-1024

Product:
Product-A

Purchased:
100 units

Requested return:
20 units

Return eligible:
YES

Expected credit:
₹36,000

Requires approval:
NO
```

---

## SA-7 — Health Score Agent

Primary responsibility:

> Measure and update the customer relationship health based on financial, commercial, operational, and engagement behaviour.

The health score should not be a pure LLM guess.

A better approach is:

```text
Base Score
    +
Payment Behaviour
    +
Purchase Behaviour
    +
Dispute Behaviour
    +
Return Behaviour
    +
Engagement
    +
Relationship Signals
    =
Customer Health Score
```

Example:

```text
Previous Health:
74

Positive:
Payment made before promise date

Neutral:
Sales return

Negative:
High outstanding amount

New Health:
72
```

Example structured result:

```json
{
  "health_score": 72,
  "previous_score": 74,
  "change": -2,
  "drivers": [
    "High outstanding amount",
    "Recent payment behaviour remained positive",
    "Return request was neutral"
  ]
}
```

---

## SA-8 — Sales Call Prep Agent

Primary responsibility:

> Collect customer information from Customer 360 and all sub-agents, prepare a detailed sales-call brief, and capture post-call actions.

Before call:

```text
                    SALES CALL
                        │
                        ▼
                ┌──────────────┐
                │ SA-8         │
                │ CALL PREP    │
                └──────┬───────┘
                       │
       ┌───────────────┼────────────────┐
       │               │                │
       ▼               ▼                ▼
 Customer 360      Open Cases       Financial
       │               │                │
       ▼               ▼                ▼
 Sales history     Disputes         Outstanding
 Orders            Approvals        Payment pattern
 Returns           Promises         Credit notes
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                CALL BRIEF
                       │
                       ▼
                SALES PERSON
                       │
                       ▼
                    CUSTOMER
```

Example call brief:

```text
CUSTOMER
ABC Industries

RELATIONSHIP
Health: 78/100 ↑

COMMERCIAL
Revenue last 12 months: ₹X
Growth: +12%

PAYMENT
Outstanding: ₹X
Overdue: ₹Y

RECENT EVENTS
• Payment promise
• Sales return
• Price discussion

OPEN ISSUES
• Dispute DSP-2026-00123

CALL OBJECTIVES
1. Resolve dispute
2. Discuss overdue amount
3. Discuss next order
4. Introduce Product X

DO NOT MISS
• Customer requested revised pricing
```

After the call:

```text
CALL NOTES
    │
    ▼
SA-8
    │
    ▼
Extract Structured Actions
    │
    ├───────────────┬────────────────┬───────────────┐
    ▼               ▼                ▼               ▼
Payment          Approval          Event           Sales
Promise          Request           Create          Opportunity
    │               │                │
    ▼               ▼                ▼
   SA-2            SA-4          Calendar/Event
    │               │
    ▼               ▼
Health Score      Approval
```

---

# 5. First-Time Customer Workflow

```text
                         FIRST MESSAGE
                              │
                              ▼
                    ┌───────────────────┐
                    │ Parse Message     │
                    └─────────┬─────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │ Identify Customer │
                    └─────────┬─────────┘
                              │
                              ▼
                     Customer exists?
                         /        \
                       NO          YES
                       │            │
                       ▼            │
              ┌──────────────┐     │
              │ Create /     │     │
              │ Resolve     │     │
              │ Customer    │     │
              └──────┬───────┘     │
                     │             │
                     └──────┬──────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ LOAD CUSTOMER 360    │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ INITIAL HEALTH SCORE │
                 │ CALCULATION          │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ CONVERSATION CREATED │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ CUSTOMER ASSIST      │
                 │                      │
                 │ Understand Request   │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ INTENT DETECTION     │
                 └──────────┬───────────┘
                            │
                ┌───────────┼────────────┐
                │           │            │
                ▼           ▼            ▼
             GENERAL      RETURN      RECOVERY
                │           │            │
               SA-1        SA-6         SA-2
                │           │            │
                └───────────┼────────────┘
                            │
                            ▼
                    RESULT AGGREGATION
                            │
                            ▼
                     STATE UPDATE
                            │
                            ▼
                  CUSTOMER RESPONSE
                            │
                            ▼
                    TIMELINE EVENT
                            │
                            ▼
                           END
```

---

# 6. Example — First Customer Interaction

Customer sends:

> "Hi, I am Raj from ABC Industries. Can you tell me my outstanding amount? Also I want to return 20 pieces from invoice INV-1024."

## Step 1 — Input Adapter

```text
CHANNEL = EMAIL

customer_email = raj@abc.com

message =
"Hi, I am Raj from ABC Industries.
Can you tell me my outstanding amount?
Also I want to return 20 pieces from invoice INV-1024."
```

## Step 2 — Customer Resolver

```text
Search MongoDB
       │
       ├── email
       ├── phone
       ├── customer code
       └── name/company
```

Result:

```text
Customer found

customer_id = CUST-00124
company = ABC Industries
```

## Step 3 — Customer 360 Retrieval

```text
CUST-00124
    │
    ├── Master
    │
    ├── Sales
    │     ├── INV-1001
    │     ├── INV-1010
    │     ├── INV-1024
    │     └── INV-1030
    │
    ├── Receipts
    │     ├── REC-001
    │     └── REC-002
    │
    ├── Ledger
    │
    ├── Credit Notes
    │
    ├── Orders
    │
    ├── Conversations
    │
    ├── Disputes
    │
    ├── Approvals
    │
    └── Health Score
```

## Step 4 — Customer Assist Intent Analysis

```text
USER REQUEST
     │
     ▼
┌─────────────────────────────────────┐
│ INTENT ANALYSIS                     │
├─────────────────────────────────────┤
│ Intent 1: Outstanding enquiry       │
│ Intent 2: Sales return request      │
│                                     │
│ Invoice: INV-1024                   │
│ Quantity: 20                        │
│                                     │
│ Required Agents:                    │
│   SA-1 General                      │
│   SA-6 Sales Return                 │
│   SA-7 Health Score                 │
└──────────────────┬──────────────────┘
                   │
                   ▼
             EXECUTION PLAN
```

Execution plan:

```text
PLAN
 │
 ├── Task 1
 │     SA-1
 │     Retrieve outstanding
 │
 ├── Task 2
 │     SA-6
 │     Validate return
 │
 └── Task 3
       SA-7
       Evaluate health impact
```

---

# 7. SA-1 Example Execution

```text
SA-1 GENERAL
      │
      ▼
get_customer_ledger()
      │
      ▼
get_receipts()
      │
      ▼
get_sales()
      │
      ▼
calculate/current balance
      │
      ▼
STRUCTURED RESULT
```

Example:

```text
Outstanding:
₹4,82,500

Overdue:
₹1,20,000

Latest payment:
₹75,000 on 12-Aug-2026
```

---

# 8. SA-6 Example Execution

```text
SA-6 SALES RETURN
        │
        ▼
Find INV-1024
        │
        ▼
Check invoice items
        │
        ▼
Find requested 20 pieces
        │
        ▼
Check return eligibility
        │
        ▼
Check previous returns
        │
        ▼
Calculate expected credit
        │
        ▼
STRUCTURED RESULT
```

Example:

```text
Invoice: INV-1024

Product:
Product-A

Purchased:
100 units

Requested return:
20 units

Return eligible:
YES

Expected credit:
₹36,000

Requires approval:
NO
```

---

# 9. SA-7 Example Execution

SA-7 receives:

```text
Customer:
CUST-00124

Recent events:
• New return request
• Outstanding ₹4,82,500
• Payment ₹75,000 recently received
```

It produces:

```text
Previous health:
74

Payment behaviour:
Positive

Return:
Neutral

Outstanding:
Negative

New health:
72
```

---

# 10. Customer Assist Result Aggregation

```text
                  CUSTOMER ASSIST
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
         SA-1          SA-6         SA-7
           │            │            │
           ▼            ▼            ▼
       Outstanding    Return       Health
        ₹4.82L        Eligible       72
           │            │            │
           └────────────┼────────────┘
                        │
                        ▼
                  RESULT REVIEW
                        │
                        ▼
                Any unresolved issue?
                     /          \
                   NO            YES
                   │              │
                   ▼              ▼
              Synthesize      More agent /
              response        human approval
```

---

# 11. Example Final Customer Response

```text
Hi Raj,

Your current outstanding balance is ₹4,82,500, of which
₹1,20,000 is currently overdue.

Regarding invoice INV-1024, the requested return of 20 units
is eligible for processing. The expected credit is approximately
₹36,000, subject to the final return processing.

We've registered the return request and will keep you updated
on the next step.
```

The response is synthesized from:

```text
MongoDB
   +
SA-1
   +
SA-6
   +
SA-7
   +
Business Rules
```

---

# 12. Customer Lifecycle After the First Interaction

The conversation does not end when the response is sent.

The system creates events:

```text
CUSTOMER INTERACTION
        │
        ▼
┌──────────────────────┐
│ EVENT STORE          │
├──────────────────────┤
│ MESSAGE_RECEIVED     │
│ RETURN_REQUESTED     │
│ HEALTH_UPDATED       │
│ RESPONSE_SENT        │
└──────────┬───────────┘
           │
           ▼
       CUSTOMER
       TIMELINE
```

---

# 13. Example — Customer Makes a Payment Promise

Customer replies:

> "Okay. I'll pay ₹2,00,000 by 20 August."

Workflow:

```text
CUSTOMER REPLY
      │
      ▼
CUSTOMER ASSIST
      │
      ▼
Intent:
PAYMENT PROMISE
      │
      ▼
SA-2 RECOVERY
      │
      ▼
Extract:
Amount = ₹2,00,000
Date   = 20-Aug-2026
      │
      ▼
Validate
      │
      ▼
CREATE PAYMENT PROMISE
      │
      ▼
CREATE EVENT
      │
      ▼
UPDATE HEALTH SCORE
      │
      ▼
ACKNOWLEDGE CUSTOMER
```

---

# 14. Example — Customer Disputes the Amount

Customer says:

> "I don't agree with ₹1,20,000 overdue. Invoice INV-1024 was returned."

Workflow:

```text
CUSTOMER
   │
   ▼
CUSTOMER ASSIST
   │
   ▼
Intent:
DISPUTE
   │
   ▼
SA-3 DISPUTE
   │
   ├── Find invoice
   ├── Find return
   ├── Find credit note
   ├── Check ledger
   ├── Gather evidence
   └── Create dispute case
           │
           ▼
      DSP-2026-00123
           │
           ▼
     CASE CREATED
           │
           ▼
   Customer acknowledgement
```

Possible finding:

```text
Return exists
       │
       ▼
Credit Note NOT yet posted
       │
       ▼
Ledger still showing old balance
       │
       ▼
Recommended action:
Process/post credit note
       │
       ▼
Recalculate ledger
```

---

# 15. Example — Customer Requests a Special Settlement

Customer says:

> "I can pay ₹2 lakh now if you approve the remaining amount as a special settlement."

Workflow:

```text
CUSTOMER
   │
   ▼
CUSTOMER ASSIST
   │
   ▼
Settlement Request
   │
   ▼
SA-4 APPROVAL
   │
   ├── Gather outstanding
   ├── Payment history
   ├── Customer health
   ├── Previous settlements
   ├── Dispute information
   └── Proposed settlement
          │
          ▼
   APPROVAL REQUEST
          │
          ▼
    APR-2026-00045
          │
          ▼
    HUMAN APPROVAL
          │
       ┌──┴──┐
       ▼     ▼
    APPROVED REJECTED
       │       │
       ▼       ▼
   Execute   Inform
       │
       ▼
   Update State
       │
       ▼
   SA-7 Health
```

---

# 16. SA-8 Sales Call Workflow

Before a sales call:

```text
                    SALES CALL
                        │
                        ▼
                ┌──────────────┐
                │ SA-8         │
                │ CALL PREP    │
                └──────┬───────┘
                       │
       ┌───────────────┼────────────────┐
       │               │                │
       ▼               ▼                ▼
 Customer 360      Open Cases       Financial
       │               │                │
       ▼               ▼                ▼
 Sales history     Disputes         Outstanding
 Orders            Approvals        Payment pattern
 Returns           Promises         Credit notes
       │               │                │
       └───────────────┼────────────────┘
                       ▼
                CALL BRIEF
                       │
                       ▼
                SALES PERSON
                       │
                       ▼
                    CUSTOMER
```

After the call:

```text
CALL NOTES
    │
    ▼
SA-8
    │
    ▼
Extract Structured Actions
    │
    ├───────────────┬────────────────┬───────────────┐
    ▼               ▼                ▼               ▼
Payment          Approval          Event           Sales
Promise          Request           Create          Opportunity
    │               │                │
    ▼               ▼                ▼
   SA-2            SA-4          Calendar/Event
    │               │
    ▼               ▼
Health Score      Approval
```

---

# 17. Full Customer Relationship Loop

```text
================================================================================
                         CUSTOMER RELATIONSHIP LOOP
================================================================================

                               CUSTOMER
                                  │
                                  ▼
                       EMAIL / CHAT / CALL
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
                    ┌────────────────────────┐
                    │    CUSTOMER ASSIST     │
                    │     ORCHESTRATOR       │
                    └───────────┬────────────┘
                                │
                                ▼
                         INTENT + PLAN
                                │
             ┌──────────────────┼──────────────────┐
             │                  │                  │
             ▼                  ▼                  ▼
          INFORMATION         ACTION            ISSUE
             │                  │                  │
             ▼                  ▼                  ▼
            SA-1        SA-2 / SA-5 / SA-6   SA-3 / SA-4
             │                  │                  │
             └──────────────────┼──────────────────┘
                                │
                                ▼
                            SA-7 HEALTH
                                │
                                ▼
                         RESULT AGGREGATOR
                                │
                   ┌────────────┼────────────┐
                   │            │            │
                   ▼            ▼            ▼
                RESPONSE      EVENT        STATE
                   │            │            │
                   ▼            ▼            ▼
                CUSTOMER    EVENT STORE   CUSTOMER 360
                                │
                                │
                                ▼
                         FUTURE TRIGGER
                                │
                                ▼
                       CUSTOMER ASSIST
                                │
                                ▼
                            NEXT ACTION
                                │
                                ▼
                              LOOP
                                │
                                └───────────────────────► CUSTOMER
```

---

# 18. Technical Architecture

```text
================================================================================
                           TECHNICAL ARCHITECTURE
================================================================================


                    EMAIL          CHAT          WEBHOOK
                      │              │              │
                      └──────────────┼──────────────┘
                                     │
                                     ▼
                            PYTHON APPLICATION
                                     │
                                     ▼
                              LANGGRAPH
                         ┌───────────┴───────────┐
                         │                       │
                         ▼                       ▼
                 CUSTOMER ASSIST             AGENTS
                 ORCHESTRATOR
                         │                       │
                         └───────────┬───────────┘
                                     │
                              TOOL / SERVICE
                                  LAYER
                                     │
          ┌──────────────┬───────────┼───────────┬───────────────┐
          │              │           │           │               │
          ▼              ▼           ▼           ▼               ▼
       MongoDB         Tally       Pricing    Event Store   Communication
          │              │           │           │               │
          │              │           │           │               │
          └──────────────┴───────────┼───────────┴───────────────┘
                                     │
                                     ▼
                              LLM GATEWAY
                             /            \
                            /              \
                           ▼                ▼
                     OpenAI SDK         NVIDIA NIM
                           \                /
                            \              /
                             ▼            ▼
                         STRUCTURED OUTPUT
                                │
                                ▼
                           STREAMING EVENTS
                                │
                                ▼
                              UI/API
```

---

# 19. Recommended Customer 360 Model

MongoDB should not merely be treated as a raw voucher database.

The system should build a logical Customer 360 state:

```text
Customer
│
├── Master Data
│   ├── customer_id
│   ├── name
│   ├── contact
│   ├── territory
│   └── sales_person
│
├── Financial State
│   ├── outstanding
│   ├── overdue
│   ├── credit_limit
│   ├── payment_behavior
│   └── ledger
│
├── Commercial State
│   ├── orders
│   ├── sales
│   ├── returns
│   ├── credit_notes
│   └── pricing
│
├── Communication State
│   ├── emails
│   ├── chats
│   ├── calls
│   └── conversations
│
├── Relationship State
│   ├── health_score
│   ├── risk
│   ├── sentiment
│   ├── engagement
│   └── relationship_stage
│
├── Operational State
│   ├── disputes
│   ├── approvals
│   ├── promises
│   ├── events
│   └── tasks
│
└── Agent State
    ├── active_cases
    ├── pending_actions
    └── recent_agent_decisions
```

---

# 20. Recommended MongoDB Logical Collections

```text
customers

customer_ledger

sales_vouchers
receipt_vouchers
credit_notes
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

The `customer_timeline` collection can provide a chronological customer history:

```text
CUSTOMER TIMELINE

2026-08-01  Order #123
2026-08-03  Invoice #456
2026-08-07  Customer email
2026-08-08  Payment promise
2026-08-10  Dispute created
2026-08-11  Sales call
2026-08-12  Partial payment
2026-08-13  Credit note
```

---

# 21. Tool Layer

Agents should not receive unrestricted database access such as:

```python
mongo_query()
```

Instead, expose domain-specific tools.

## Read Tools

```text
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

## Write / Action Tools

```text
create_dispute()
update_dispute()

create_approval()
update_approval()

create_payment_promise()

create_event()
create_task()

create_order()

create_sales_return()

create_customer_note()

update_health_score()

send_customer_message()
```

This improves:

- Security
- Auditability
- Reliability
- Permissions
- Testing
- Observability

---

# 22. Agent State

A shared orchestration state can conceptually look like:

```python
class CustomerAssistState(TypedDict):

    # Identity
    customer_id: str

    # Incoming request
    channel: Literal["email", "chat", "webhook", "call"]
    message: str
    conversation_id: str

    # Context
    customer_context: dict
    conversation_context: list
    relevant_vouchers: list
    active_cases: list
    active_approvals: list
    active_events: list

    # Understanding
    intents: list
    entities: dict
    urgency: str

    # Planning
    execution_plan: list
    assigned_agents: list

    # Agent outputs
    agent_results: list

    # Decisions
    pending_actions: list
    completed_actions: list

    # Final
    final_response: str
```

Agents should not be allowed to mutate every field arbitrarily. Use state ownership and explicit action contracts.

---

# 23. Structured Output

LLM-to-system interactions should use structured output wherever possible.

Example:

```python
class Intent(BaseModel):
    name: str
    confidence: float
    entities: dict
    reason: str
```

Agent routing:

```python
class AgentDecision(BaseModel):
    agent: str
    action: str
    reason: str
    priority: int
    requires_human: bool
```

Agent result:

```python
class AgentResult(BaseModel):
    status: Literal[
        "completed",
        "needs_information",
        "needs_agent",
        "needs_approval",
        "needs_human",
        "failed"
    ]

    summary: str
    actions: list
    customer_message: str | None
    next_agent: str | None
```

---

# 24. Streaming Output

Do not expose hidden/internal reasoning.

Instead stream useful execution events:

```text
Customer Assist
  └─ Understanding request...

Customer context
  └─ Retrieved 47 relevant records

Recovery Agent
  └─ Reviewing payment history...

Dispute Agent
  └─ Checking invoice INV-10234...

Health Score
  └─ Updating relationship score...

Customer Assist
  └─ Preparing response...
```

The UI gets meaningful progress while internal reasoning remains private.

---

# 25. Event Model

The architecture naturally benefits from an event store.

Example:

```json
{
  "event_id": "EVT-12345",
  "customer_id": "CUST-001",
  "type": "PAYMENT_PROMISE_CREATED",
  "timestamp": "...",
  "source": "recovery_agent",
  "payload": {
    "amount": 200000,
    "due_date": "2026-08-20"
  }
}
```

Possible events:

```text
CUSTOMER_CREATED
MESSAGE_RECEIVED
MESSAGE_SENT

ORDER_CREATED
ORDER_UPDATED

PAYMENT_RECEIVED
PAYMENT_PROMISE_CREATED
PAYMENT_PROMISE_MODIFIED
PAYMENT_PROMISE_MISSED

DISPUTE_CREATED
DISPUTE_UPDATED
DISPUTE_CLOSED

APPROVAL_CREATED
APPROVAL_APPROVED
APPROVAL_REJECTED

RETURN_REQUESTED
RETURN_APPROVED
CREDIT_NOTE_CREATED

HEALTH_SCORE_UPDATED

SALES_CALL_CREATED
SALES_CALL_COMPLETED
```

Events can trigger future workflows.

---

# 26. LangGraph as the State Machine

The graph should be thought of as a state machine rather than simply a chain of LLM calls.

```text
                 START
                   │
                   ▼
          Context Ingestion
                   │
                   ▼
          Customer Resolution
                   │
                   ▼
             Intent Analysis
                   │
                   ▼
          Planning / Routing
                   │
        ┌──────────┼──────────┐
        ▼          ▼          ▼
      Agent A    Agent B    Agent C
        │          │          │
        └──────────┼──────────┘
                   ▼
             Result Review
                   │
             ┌─────┴─────┐
             │           │
          Complete    More work
             │           │
             ▼           └──────► Another Agent
         Response
             │
             ▼
        State Update
             │
             ▼
              END
```

Loops are allowed where necessary, but should be controlled by the orchestrator.

---

# 27. Agent-to-Agent Orchestration Principle

Avoid uncontrolled chains like:

```text
SA-2 → SA-3 → SA-4 → SA-7 → SA-2 → ...
```

Prefer:

```text
                 Customer Assist
                       │
                 determines plan
                       │
         ┌─────────────┼──────────────┐
         ▼             ▼              ▼
       SA-2           SA-3           SA-7
         │             │              │
         └─────────────┼──────────────┘
                       ▼
                 Customer Assist
```

Agents can request capabilities, but Customer Assist should normally control execution.

Benefits:

- Predictable execution
- Fewer loops
- Better observability
- Easier debugging
- Better cost control
- Easier permissions
- Easier testing

---

# 28. Human Approval Gateway

Not every action should be fully autonomous.

Recommended action modes:

```text
AUTO
AUTO + INFORM
HUMAN APPROVAL
```

Example:

| Action | Mode |
|---|---|
| Retrieve ledger | Auto |
| Explain invoice | Auto |
| Payment reminder | Auto |
| Payment promise | Auto |
| Create dispute | Auto |
| Create return request | Auto |
| Issue large credit note | Human |
| Special discount | Human |
| Change credit limit | Human |
| Large write-off | Human |
| Sensitive financial adjustment | Human |

The structured agent result can contain:

```python
requires_human = True
```

LangGraph can then pause until the human decision is available.

---

# 29. LLM Gateway

Keep the model provider separate from agent/orchestration logic.

```text
                   LLM GATEWAY
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
      OpenAI       NVIDIA NIM    Other Model
```

Agents request capabilities such as:

```text
structured_completion
classification
reasoning
summarization
long_context_analysis
```

The implementation can then select the appropriate provider/model.

Example model allocation:

```text
Customer Assist -> stronger reasoning model
General Agent   -> cheaper/faster model
Health Score    -> deterministic rules + smaller model
Call Prep       -> long-context model
Classification  -> fast model
```

---

# 30. Design Principles

## Keep

- Customer Assist as orchestrator
- Eight specialized agents
- LangGraph
- Structured outputs
- Streaming
- MongoDB
- Tally voucher data
- Event-driven updates
- Health score
- Sales-call intelligence

## Add

- Customer 360 state
- Domain tool layer
- Event store
- Agent permissions
- Human approval gateway
- Case/task lifecycle
- Audit trail
- Agent execution tracing
- Deterministic business rules
- LLM provider abstraction

## Avoid

- Agents directly modifying arbitrary MongoDB documents
- LLM calculating accounting numbers
- LLM inventing prices or discounts
- Uncontrolled agent-to-agent loops
- Putting all business logic inside prompts
- Treating LLM-generated health scores as the source of truth
- Making Customer Assist responsible for every business operation

---

# 31. Final Architecture Mental Model

The simplest way to understand the complete system is:

```text
             ┌──────────────────────────┐
             │      CUSTOMER ASSIST     │
             │                          │
             │      "What is needed?"   │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │        AGENTS            │
             │                          │
             │      "How to do it?"     │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │         TOOLS            │
             │                          │
             │      "Do the work."      │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │       DATA / SYSTEMS     │
             │                          │
             │ MongoDB / Tally / Events │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │      CUSTOMER 360        │
             │                          │
             │ "What is the new state?" │
             └────────────┬─────────────┘
                          │
                          ▼
             ┌──────────────────────────┐
             │      NEXT BEST ACTION    │
             └────────────┬─────────────┘
                          │
                          └──────────────► LOOP
```

## Core V1 Principle

The customer sends a message only once, but the customer relationship keeps generating:

```text
Messages
Payments
Promises
Orders
Returns
Credit Notes
Disputes
Approvals
Sales Calls
Events
Health Changes
```

Customer Assist should therefore not treat every email/chat as an isolated request.

It should continuously orchestrate around an evolving **Customer 360 state**.

That loop is the heart of the architecture.
