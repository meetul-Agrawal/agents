sf_tenant_6a33b5b2091da2fb4a7c3de4

# Customer Assist

Agentic orchestration over Tally data in MongoDB. Vision: `Docs/01vision.md`.
Roadmap and evaluation gates: `Docs/02phasesWithEval.md`.

Status: **Phase 2 complete** — contracts frozen, evaluation foundation running,
Customer 360 answering from real data, and email/chat/webhook normalized onto
one conversation model. No agent is implemented yet.

## Layout

```
src/ca/contracts.py     frozen boundary types (Customer, Outstanding, AgentResult, Event, ...)
src/ca/registry.py      agent + tool declarations, permissions, action modes
src/ca/config.py        settings, read-only tenant DB guard, app DB handle
src/ca/customer360.py   resolution, outstanding, ledger, timeline, Customer 360
src/ca/inbox.py         email/chat/webhook parsing, threading, dedupe, ingestion
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
uv run pytest                                  # 114 tests: unit, negative, live integration
uv run scripts/run_evals.py all                # routing + resolution + conversation + customer360
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

Phase 3 — Customer Assist orchestrator: the LangGraph state machine
(`load_context → classify_intent → create_plan → route → execute → review →
respond → update_state`) over mock agents, graded by the routing suite that is
already in place.
