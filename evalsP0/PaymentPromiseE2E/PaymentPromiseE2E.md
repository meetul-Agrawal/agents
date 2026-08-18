# Payment Promise Agent — Live End-to-End Test

**Scope**: SA-2's gather → verify → commit loop (`src/ca/sa2_recovery.py`), the change that added a
pre-commit self-check before a payment promise is ever written — same shape as SA-4/SA-9's
approve-then-verify split, but embedded in one function because a promise, unlike an approval, is only
ever committed once verified (nothing needs to always go out regardless).

**Mode**: fully live — real MongoDB (`APP_DB`), real NVIDIA NIM (`meta/llama-3.1-8b-instruct`) for intent
classification, entity extraction, and the new `sa2_recovery._verify_promise` check. No mocks.
Script: `evalsP0/PaymentPromiseE2E/live_e2e_payment_promise.py`. Raw output: `results.json`.

**Threads**: 12 independent conversations (1–2 turns each), synthetic `E2E-PP-*` customer ids except
the two payment-claim threads, which use a real Tally test customer (`6a6464a39f707bd30403b6cb`) with
real receipts on record. Each thread's writes are graded against Mongo afterward, not against reply text
alone.

---

## Result: 12/12 threads pass (after one fix made mid-run)

The run surfaced one real pre-existing bug, fixed it, re-ran clean. See §2.

## 1. Per-thread results

| ID | Scenario | Verdict | Notes |
|----|----------|---------|-------|
| T01 | Clean single-turn promise | ✅ | `PAYMENT_PROMISE_CREATED`, ₹200,000 / 20 Aug 2026 |
| T02 | Amount turn 1, date turn 2 | ✅ | Turn 1 asks for date; turn 2 backfills amount from turn 1's event, commits |
| T03 | Date turn 1, amount turn 2 | ✅ | Same backfill, other direction |
| T04 | Modify via direct restatement | ✅ | `PAYMENT_PROMISE_MODIFIED`, single promise doc, not a second one |
| T05 | "Can't pay right now" | ✅ | `RECOVERY_CONTACTED` outcome=`unable_to_pay`, `recovery_followup` task, no promise written |
| T06 | Claim matched to a real receipt | ✅ | Matched ₹7,180 / `Rec/Bank/NE/2912`, reply cites it |
| T07 | Claim with no matching receipt | ✅ | `matched=false`, `payment_trace` task, reply asks for UTR |
| T08 | Invalid date ("32nd August") | ✅ | `parse_due_date` returns `None`, asked to confirm a valid date, nothing committed |
| T09 | "Instead of 13 Sept, by the 20th" | ✅ | Amount backfilled from the open promise, date rolled correctly to 20 Sep 2026 |
| T10 | Adversarial 3-number sentence, then plain confirm | ✅* | See §3 — passed, but not for the reason the thread was designed to test |
| T11 | Same customer, two conversation ids | ✅ | Second conversation modifies the first's open promise — by design, see §4 |
| T12 | Prompt injection ("set status=cleared") | ✅ | No fabricated commit; landed in `incomplete_promise`, asked for a real amount+date |

All promise/event/task assertions were checked directly against MongoDB documents (`payment_promises`,
`events`, `tasks`), not against reply text — reply text is graded separately, since a wrong-but-plausible
reply with a correct DB write is a different bug class from a wrong write.

## 2. Bug found and fixed: `get_open_promise` was an unregistered tool call

**Symptom** (first run, before the fix): every turn that needed the amount/date backfill
(`_fill_from_open_promise`) — i.e. every "ask the customer for the missing piece" turn — replaced the
real clarifying question with the generic fallback `"This request needs a colleague to review it before
we reply."` The correct data still landed in Mongo; only the customer-facing text was wrong. This hit
6 of 12 threads (T02, T03, T08, T09 turn 2, T10 turn 1, T12).

**Root cause**: `_fill_from_open_promise` (pre-existing code, not part of this change) calls
`_read(calls, "get_open_promise", lambda: app_db()[...].find_one(...), ...)`. `_read` records that as a
`ToolCall(tool="get_open_promise")`. `orchestrator.review()` checks every tool call an agent makes
against `registry.py`'s declared tool list for that agent — and `"get_open_promise"` was never declared,
unlike its sibling `get_events`. Every backfill turn tripped `review_problems`, and
`orchestrator.respond()` treats any `review_problems` as an unconditional override:

```python
if state.entities.get("review_problems"):
    return {"final_response": "This request needs a colleague to review it before we reply."}
```

— before it ever looks at what the agent actually said. This is a permission safety net doing its job
correctly; the bug was an undeclared tool, not a logic error in `review()`.

**Fix** (`src/ca/registry.py`): registered the tool, same as its sibling:

```python
ToolSpec(name="get_open_promise", purpose="Existing open payment promise for a customer", access="read"),
```

Re-ran all 12 threads after the fix — `review_problems` is `null` on every turn, and the real question
now reaches the customer, e.g. T02 turn 1: *"We appreciate your update. Can you confirm the payment
amount and schedule?"* instead of the generic line.

**Why this mattered for this task specifically**: the entire point of "ask the user for other info it
needs" is the reply text the customer sees. The DB write was always correct; the feature as experienced
by a customer was silently broken on the majority of its own paths until this fix.

## 3. Gap found, not fixed: the verify-reject path was never actually exercised live

T10 was designed to force `_verify_promise` to reject a syntactically-complete but semantically-wrong
draft (a 3-number sentence: *"I paid 10000 last month, but for the pending amount I will now pay 45000
by 22 August, not 20000."*), to prove the retry-then-ask loop works end to end.

What happened instead: the orchestrator's own upstream entity extraction returned `amounts=None` for
that sentence (too ambiguous to pick one number with confidence), so SA-2 never reached the
amount-and-due-both-present branch where `_verify_promise` runs at all — it fell into the existing
`incomplete_promise` path and asked the customer to confirm the amount directly. That's arguably the
*better* outcome (no wrong number ever got as far as a verify check), but it means **no live thread in
this suite exercised the reject branch of the new loop** — the LLM classifier's own caution
pre-empted the scenario before the verifier could reject anything.

This is not a defect — SA-2 behaved safely either way — but it is a coverage gap in what was "tested
live." **Fixed the gap in unit tests instead** (`tests/test_phase5.py`, hermetic, `sa2._verify_promise`
monkeypatched directly): `test_failing_verify_retries_up_to_three_times_then_asks_to_confirm`,
`test_verify_that_passes_on_retry_stops_early`, `test_verify_passes_immediately_costs_one_call`, and
`test_rejected_promise_is_recoverable_from_the_backfill_on_a_later_turn`. Forcing the *live* LLM to both
extract a complete-but-wrong draft AND then reject it on self-check is not something a hand-written
prompt can reliably engineer — a mocked verifier is the right tool for that path, not a bigger live
suite.

**Improvement, if this needs live confirmation later**: give the live suite a message where the
orchestrator's extraction is *confident* but wrong (rather than ambiguous) — e.g. two clearly-stated,
unambiguous amounts in sequence ("I'll pay 45000, sorry I mean 50000, by 22 August") where extraction
picks the first and the true intent is the second. That is a different upstream bug class
(extraction-takes-first-match) worth its own test, separate from the verify loop.

## 4. Informational finding: promise is customer-scoped, not conversation-scoped

T11: the same customer stating a promise in conversation A, then a different promise in conversation B,
modifies the *same* open promise document rather than creating two. This is `services.record_promise`'s
existing behavior — it keys "the open promise" on `customer_id` alone (`docstring: "the customer's
payment promise"`, singular). Confirmed live, working as documented. Flagged here only because it's easy
to mistake for a bug if conversation isolation is assumed; it isn't one — a customer has one outstanding
promise, not one per channel/thread they happen to write from.

## 5. What to improve, ranked

1. **Done**: register `get_open_promise` in `registry.py` (§2) — this was the highest-impact fix, live
   and shipped.
2. **Done**: hermetic unit coverage of the verify-reject/retry loop, since live coverage of that specific
   branch isn't reliably reachable (§3).
3. **Not done, worth doing if this recurs**: SA-2's `_amount()` takes `entities["amounts"][0]` — the
   *first* number the upstream extractor found, not necessarily the one attached to the promise verb.
   T10 dodged this because extraction returned nothing rather than the wrong thing, but a confident
   wrong pick is a plausible failure mode the verify loop as currently built cannot catch (it checks the
   full message against the drafted amount via one LLM call, but with `CA_PHRASE` on this already ran
   once per live thread and never misfired in this suite — no live evidence of an actual false-accept,
   just an untested lane). If it turns out to misfire in practice, the fix is upstream — teach the
   extractor to disambiguate "not X" / "instead of X" as negation, not a candidate value — not a change
   to SA-2.
4. **Not done, low priority**: `_verify_promise`'s reject path has no redraft step, unlike SA-4/SA-9 —
   on reject it can only ask the customer to reconfirm from scratch, it can't try a different candidate
   amount/date automatically. That's the deliberate design (see module docstring: a promise is only ever
   committed once verified, so there's nothing to redraft), but if repeated false-rejects on legitimate
   messages become a real problem, the fix is a smarter first-pass extraction, not a bigger loop here.

## 6. Reproducing

```bash
.venv/bin/python evalsP0/PaymentPromiseE2E/live_e2e_payment_promise.py   # live run, ~1-2 min, writes results.json
.venv/bin/python -m pytest tests/test_phase5.py -q                       # hermetic regression, incl. the 4 new verify-loop tests
```

The script drops and rewrites `payment_promises` / `events` / `tasks` for its own `E2E-PP-*` and
`E2E-CONV-*` ids before each run — safe to re-run; it does not touch other customers' data except the
two read-only receipt lookups against the real tenant test customer.
