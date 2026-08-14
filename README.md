sf_tenant_6a33b5b2091da2fb4a7c3de4

# Customer Assist

Agentic orchestration over Tally data in MongoDB. Vision: `Docs/01vision.md`.
Roadmap and evaluation gates: `Docs/02phasesWithEval.md`.

Status: **Phase 3 complete** — contracts frozen, evaluation foundation running,
Customer 360 answering from real data, email/chat/webhook normalized onto one
conversation model, and the orchestrator routing over mock agents. The eight
real agents (SA-1 … SA-8) are still mocks.

## Layout

```
src/ca/contracts.py     frozen boundary types (Customer, Outstanding, AgentResult, Event, ...)
src/ca/registry.py      agent + tool declarations, permissions, action modes
src/ca/config.py        settings, read-only tenant DB guard, app DB handle
src/ca/customer360.py   resolution, outstanding, ledger, timeline, Customer 360
src/ca/inbox.py         email/chat/webhook parsing, threading, dedupe, ingestion
src/ca/orchestrator.py  intent rules, planner, approval gate, LangGraph state machine
src/ca/llm.py           LLM gateway: capability -> provider, structured output
src/ca/data_quality.py  the book's data-quality checks
src/ca/evals.py         dataset loader, graders, runner, report, regression
evals/datasets/         .jsonl cases, one directory per suite
evals/regression/       accepted baselines
scripts/run_evals.py    run a suite, diff against baseline
scripts/gen_golden.js   independent mongosh implementation that produces the golden values
```

## Run

```bash
uv sync
uv run pytest                                  # 175 tests: unit, negative, live integration
uv run scripts/run_evals.py all                # routing + resolution + conversation + customer360
uv run scripts/run_evals.py routing_llm        # same dataset, LLM classifier (costs tokens)
uv run scripts/run_evals.py all --accept       # store new baselines
uv run python -m ca.data_quality               # data-quality report (exit 1 on any P0)
```

## How the money is computed

Outstanding is **bill-level**, not a net balance, because a net balance is wrong
in this book: many receipts settle invoices that predate it, which makes a
customer look overpaid.

```
outstanding = Σ over in-book sales invoices of (invoice amount − Agst Ref allocations)
```

Receipts that point at an invoice this book does not contain are reported as
`pre_book_settlements` and never netted off. `on_account` and `advance` receipts
are reported separately too. Ageing is measured from the **invoice date** — this
book has no due dates, so nothing is described as "overdue".

The golden values in `evals/datasets/customer360/` are produced by
`scripts/gen_golden.js`, a separately written mongosh implementation of the same
rules. The suite passes only when two independent implementations agree.

## Data rules

- `sf_tenant_6a33b5b2091da2fb4a7c3de4` is Tally-synced and **read-only**. No new
  collections in it. `ca.config.tenant_db()` enforces both: unknown collection
  names and any write method raise `ReadOnlyDatabaseError`.
- Everything the platform writes goes to a separate database, `customer_assist`
  (`ca.config.app_db()`).

## What the data actually looks like

- A customer **is** a `ledgers` document under `Sundry Debtors` (6,345 of them;
  5,350 have a mobile, only 155 an email — so the customer resolver cannot lean
  on email).
- `vouchers` holds 380,989 documents: `voucherCategory` is `Sales` (261,749) or
  `Receipt` (119,239). There are no credit-note or order vouchers in this tenant.
- Vouchers join to ledgers by **`ledgerName` / `partyLedgerName` string only** —
  `ledgerId` is `null` in every voucher. `customer_id` is the ledger `_id`;
  `ledger_name` is the join key, and both live on `Customer`.
- Invoice↔receipt linkage is `ledgerEntries[].billAllocations[]` where
  `billType == "Agst Ref"` and `name` is the invoice number.
- On the party's ledger line, **sales post negative and receipts positive**, so
  what the customer owes is the negation of the stored amount.
- `vouchers` has no index beyond `_id` and `companyId_voucherGuid`, so one
  customer costs a ~280ms collection scan. Fine per conversation; add
  `{"ledgerEntries.ledgerName": 1}` before any batch fan-out.

## How a message becomes a conversation

```python
conversation, message, created = inbox.ingest("email", raw_rfc822)
```

`created is False` means this delivery was already ingested — the caller must
stop, or a retry becomes a second promise or a second reply. Deduplication is a
unique index on `(channel, external_id)` in the app database, not an in-process
check. A delivery with no id gets a content hash, so retries still collapse.

Threading is tried strongest signal first: explicit `conversation_id`, then
`In-Reply-To`/`References`, then a thread key (email subject, chat session id),
then a new conversation. A subject line alone only re-opens a thread for the
**same** customer within 30 days — otherwise every "Re: Invoice" in the mailbox
would merge into one.

The thread carries identity, not the sender line: a customer replying from a new
address stays on their conversation, and an anonymous thread back-fills its
customer as soon as one message identifies them.

## Orchestration

```
START → load_context → classify_intent → create_plan → route
      → execute → review → respond → update_state → END
```

```python
state = orchestrator.handle("I want to return 20 pieces from URD/NE/327.",
                            customer_id=cid)
```

Two rules the graph enforces rather than trusting agents with:

- **A task marked `requires_human` is never executed.** It becomes a pending
  action and the run reports `needs_approval`. `enforce_approval_gate` decides
  this from the *message text*, not from the classifier, so a model that misreads
  "write off my balance" as a payment promise still cannot route around it.
- **A broken agent cannot break the run.** Raising, timing out, returning the
  wrong type, or claiming to be a different agent all become a `failed`
  `AgentResult`. The reply then says one part could not be completed instead of
  quietly looking successful.

### One structured reading per message

```python
Understanding(
    language="hinglish",
    is_greeting_only=False,
    refers_to_other_party=None,
    requests=[Request(intent="payment_claim",
                      clause="NEFT kar diya hai 1,50,000 ka",
                      confidence=0.95,
                      amount=ExtractedValue(text="1,50,000", value=150000))],
)
```

One call answers everything the orchestrator needs: intents, entities, language,
and the cross-customer signal. Intents and entities read off the same memoized
object, so there is no second pass.

The model **parses**; the system **verifies**:

- A number is only real if its `text` appears **verbatim** in the message.
  Anything the model cannot point at is dropped.
- Even for a real span, the model's arithmetic is discarded and recomputed by
  `parse_number` — `text="2 lakh", value=2.0` becomes `200000.0`.
- `intent → agent` stays in `INTENT_AGENT`; the model names intents only.
- `enforce_approval_gate` reads the raw message, never the classification.

This replaced a closed list of unit nouns, which is why `15 bundle`,
`40 bottles` and `1,50,000 ka` now work at all.

### Rules vs LLM, measured

128 cases, same graders, `uv run scripts/eval_report.py` →
`Docs/03phase3-evaluation.md`:

| Classifier | Routing | Safety | Time |
|---|---|---|---|
| `classify_rules` | 64.1% | 68.8% | 32s |
| `classify_llm` (llama-3.1-8b) | **81.2%** | **85.9%** | 235s |

The rules scored 100% on the 48 cases they were written against and 64% once 80
unseen cases were added — they had memorised their own test set. They are now
the offline fallback only. The model wins where it matters: Hinglish 24/32 vs
7/32, disputes 12/14 vs 6/14, recovery 11/15 vs 6/15.

Of the 24 remaining failures, 18 are genuine agent-selection errors, 5 are the
single `intent` label collapsing a multi-request message, and 1 is an entity.

**Measuring a "no model" baseline:** pass the classifier explicitly. `None`
means *the default*, and the default is the model — twice that turned the rules
row into a second LLM row, scoring a plausible-looking 78-81%. The tell was the
clock, not the score.

## Known data findings

From `uv run python -m ca.data_quality` against this book:

| Check | Count | Meaning |
|---|---|---|
| `duplicate_customer_name` | 2 | P0 — the voucher join key is ambiguous for these two |
| `duplicate_customer_mobile` | 84 | a phone number alone cannot identify a customer |
| `missing_voucher_number` / `invalid_voucher_date` / `voucher_without_entries` | 3 each | the same 3 non-posting stubs |
| `duplicate_voucher_number` | 200+ | same number reused within a voucher type |
| `receipt_against_unknown_invoice` | 24,215 of 108,021 | 77.6% of allocations resolve in-book; the rest predate it |
| `unbalanced_voucher` | 0 | every voucher is double-entry balanced |
| `over_allocated_bill` | 0 | no invoice is paid beyond its value |

`resolve_customer` raises `AmbiguousCustomerError` rather than guessing whenever
a query matches more than one customer.

## Next

Phase 4 — SA-1 General Agent: replace the first mock in
`orchestrator.AGENT_RUNNERS` with a real read-only agent over the Phase 1 tools,
graded for factuality and grounding.
