sf_tenant_6a33b5b2091da2fb4a7c3de4

# Customer Assist

Agentic orchestration over Tally data in MongoDB. Vision: `Docs/01vision.md`.
Roadmap and evaluation gates: `Docs/02phasesWithEval.md`.

Status: **Phase 0 complete** — contracts frozen, evaluation foundation running.
No agent is implemented yet.

## Layout

```
src/ca/contracts.py   frozen boundary types (Customer, Message, AgentResult, Event, ...)
src/ca/registry.py    agent + tool declarations, permissions, action modes
src/ca/config.py      settings, read-only tenant DB guard, app DB handle
src/ca/evals.py       dataset loader, graders, runner, report, regression
evals/datasets/       .jsonl cases, one directory per suite
evals/regression/     accepted baselines
scripts/run_evals.py  run a suite, diff against baseline
```

## Run

```bash
uv sync
uv run pytest                              # 25 tests: unit, negative, integration
uv run scripts/run_evals.py routing        # eval report + regression check
uv run scripts/run_evals.py routing --accept   # store the new baseline
```

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
- `vouchers` has no index beyond `_id` and `companyId_voucherGuid`. Phase 1 needs
  indexes before Customer 360 queries are usable.

## Next

Phase 1 — data + Customer 360 foundation: read services over the collections
above, ledger/outstanding calculation, and the data-quality tests in
`Docs/02phasesWithEval.md`.
