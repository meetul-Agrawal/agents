sf_tenant_6a33b5b2091da2fb4a7c3de4

# Customer Assist

Agentic orchestration over Tally data in MongoDB. Vision: `Docs/01vision.md`.
Roadmap and evaluation gates: `Docs/02phasesWithEval.md`.

Status: **Phase 6 complete** — contracts frozen, evaluation foundation running,
Customer 360 answering from real data, email/chat/webhook normalized onto one
conversation model, the orchestrator routing with a real approval gate, and
four real agents behind it: SA-1 (general/read-only), SA-2 (recovery), SA-3
(dispute), SA-4 (approval). SA-5 … SA-8 are still mocks.

## Layout

```
src/ca/contracts.py     frozen boundary types (Customer, Outstanding, AgentResult, Event, ...)
src/ca/registry.py      agent + tool declarations, permissions, action modes
src/ca/config.py        settings, read-only tenant DB guard, app DB handle
src/ca/customer360.py   resolution, outstanding, ledger, timeline, Customer 360
src/ca/inbox.py         email/chat/webhook parsing, threading, dedupe, ingestion
src/ca/orchestrator.py  intent rules, planner, approval gate, LangGraph state machine
src/ca/services.py      the write layer: idempotent promise/task/case/approval/event commits
src/ca/sa1_general.py   SA-1 — read-only, grounded-reply general agent
src/ca/sa2_recovery.py  SA-2 — payment promises, verified payment claims
src/ca/sa3_dispute.py   SA-3 — evidence gathering, case opening
src/ca/sa4_approval.py  SA-4 — approval-request context, recommendation, pending record
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
uv run pytest                                  # 300 tests: unit, negative, live integration
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

`Outstanding` also carries `net_balance` — the raw ledger closing balance
(opening + invoiced − every receipt, however allocated). It is diagnostic only
and **must never be shown to a customer as their balance**: in this book it is
contaminated by receipts settling pre-book invoices, so it can show a genuine
debtor as "in credit". Measured: for Aadinath Traders `net_balance` says
"credit of ₹49,458" while the bill-level truth is "owes ₹386,114". Every
customer-facing surface (SA-1, SA-3, SA-4) reads `outstanding`, never
`net_balance` — see the docstring on `Outstanding` in `contracts.py` and
`test_outstanding_never_reports_the_net_balance_as_dues` in `test_phase4.py`
before adding a new one that doesn't.

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

- **`requires_human` gates execution, not the agent running.** Early on this
  meant "skip the agent entirely" — but SA-4's whole job is to run, gather
  context and raise a *pending* approval record, which is itself an auto-mode
  action (`registry.py`). So the agent always runs; what `execute()` actually
  neutralises is any action the agent marks `executed=True` under a
  `human_approval`-mode tool. `enforce_approval_gate` still decides *whether*
  a task needs a human from the raw message text, not the classifier, so a
  model that misreads "write off my balance" as a payment promise still cannot
  route around it.
- **A broken agent cannot break the run.** Raising, timing out, returning the
  wrong type, or claiming to be a different agent all become a `failed`
  `AgentResult`. The reply then says one part could not be completed instead of
  quietly looking successful.

## The agents

**SA-1 (general, read-only)** answers from the Phase 1 tools only — never a
number the tools didn't return. An optional LLM pass rewords the finished
template; the rewrite is checked against the template (`_grounded`) and
rejected unless every figure and voucher in it already appears there
unchanged. Refuses cross-customer requests before reading anything.

**SA-2 (recovery)** records payment promises and verifies payment claims
against real receipts — never thanks a customer for a payment it cannot find.
The amount is the orchestrator's already-verified figure (no second,
possibly-disagreeing extraction); the due date is parsed deterministically
(`parse_due_date`), not guessed by a model. A claim with no amount is never
matched against just the first receipt on file — that would confirm an
unrelated payment (`test_claim_with_no_amount_does_not_confirm_an_unrelated_receipt`).

**SA-3 (dispute)** gathers evidence from the same read tools SA-1 uses — a
cited invoice's real figures, or the fact that it does not exist on the
account at all — and opens a case. It states what the records show and never
who is at fault; determining that is a human's job. What the complaint is
about (balance vs. goods vs. anything else) is read from the model's own
classification (`Understanding.requests[].about_balance`/`.issue_label`) —
there is no pattern list of complaint wording; with no model available it
defaults to asking for specifics rather than guessing.

**SA-4 (approval)** gathers context (outstanding, settlement speed, prior
approvals) and raises a *pending* approval request with a grounded
recommendation. It can never approve or execute anything itself:
`services.create_approval` always writes `status="pending"`, and the only
function that can change that (`services.decide_approval`) is never called
from agent code. `create_approval` is an auto-mode tool; `update_approval` is
human_approval-mode and appears nowhere in SA-4. Which of the six approval
categories a request is (`special_discount`, `settlement`, `credit_limit`,
`large_credit_note`, `write_off`, `exceptional_terms`) is likewise read from
the model's classification, not matched against wording — `settlement` is
the only fixed default, used when the model named nothing more specific.

### Composing replies without a template — and where that stops

SA-3 and SA-4's customer-facing text is not built from a fixed f-string filled
in with values (SA-1's approach). `sa1_general.compose_grounded(instruction,
facts)` hands the model a small JSON dict of already-verified facts and asks
it to write the whole reply, then applies the same `_grounded` check SA-1's
rewrite uses: reject the reply if it states any number or reference the facts
don't contain. No provider configured, or the candidate fails grounding →
`None`, and the caller falls back to one minimal, hand-written line.

One thing is deliberately kept out of the model's hands even here: the
approve/reject and solved/dropped verdict itself. Measured this model stating
the opposite decision inside an otherwise-correct reply ("...has been
approved. However, ... it was not approved.") — `_grounded` only checks
figures, not decision polarity, so nothing catches that. `decision_message`
and `resolution_message` fix that one sentence in code, driven by the bool/
string that already came from `services.decide_approval`/`resolve_case` —
never phrased by the model — and let `compose_grounded` write only the
trailing note, which is shown no outcome fact to contradict.

Both SA-2 and SA-3/SA-4 write through `services.py`, which is idempotent on
`message_id`: a replayed message re-finds its own promise/case/approval
instead of creating a second.

Note for anyone testing agents live: `db=` passed to `orchestrator.handle()`
only affects `update_state()`'s own write — it is **not** threaded into the
`services.*` calls each agent makes internally. A live call always writes to
the real `app_db()` unless you monkeypatch `services` directly, which is what
every test in `test_phase5.py`/`test_phase6.py` does.

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

128 cases, 3 samples per configuration, `uv run scripts/eval_report.py --repeat 3`
→ `Docs/03phase3-evaluation.md`:

| Classifier | Routing | Safety | Spread over 3 runs |
|---|---|---|---|
| `classify_rules` | 64.1% | 68.8% | 0.0% (deterministic) |
| `classify_llm` (llama-3.1-8b) | **77.6%** | **80.7%** | 1.6% |

The 13.5-point gap is far outside the noise, so it is real. The rules scored
100% on the 48 cases they were written against and 64% once 80 unseen cases were
added — they had memorised their own test set, and are now the offline fallback
only.

**Always sample more than once.** This model varies by up to ~4 points between
identical runs at `temperature=0`, which is larger than most changes worth
making. Single-run comparisons below that threshold mean nothing; the
deterministic rules row is the control that proves the harness itself is stable.

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

## Ops UI

```bash
cd ui && npm install && npm run build   # once, or `npm run dev` for hot reload
uv run uvicorn scripts.ui_server:app --reload
```

A dev server (`scripts/ui_server.py`) for manually driving conversations and
the two human decision points the approval gateway creates:

- **Approvals tab** — every pending `Approval` SA-4 has raised, across all
  customers. Approve/Reject calls `services.decide_approval` (the only place
  `Approval.status` can leave "pending") and immediately composes and sends
  the customer follow-up (`sa4_approval.decision_message`, grounded the same
  way every agent reply is) into the conversation the request came from.
- **Disputes tab** — every open `Case` SA-3 has opened. Solved/Dropped calls
  `services.resolve_case` and sends the follow-up
  (`sa3_dispute.resolution_message`) the same way. "Dropped" reuses the
  existing `closed` status — only the customer-facing wording differs.

Both send through `services.send_customer_message`, which is a no-op when the
case/approval has no `conversation_id` (created outside a conversation, e.g.
directly via a script) — the UI shows "no conversation to notify" rather than
silently failing.

## Next

Phase 7 — SA-5 Order Capture + SA-6 Sales Return: transactional agents where
price, discount and eligibility must come from deterministic services, never
an LLM.
