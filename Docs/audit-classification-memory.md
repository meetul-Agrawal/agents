# Audit — intent classification, dispute & approval, and conversational memory

**Scope:** `src/ca/orchestrator.py`, `sa3_dispute.py`, `sa4_approval.py`,
`sa9_verifier.py`, `sa2_recovery.py`, `sa1_general.py`, `contracts.py`, `llm.py`.
**Question asked:** why misclassification and lost memory still happen, and how to
get to ~99% structured-output-only with *no regex classifier, no hardcoded
classifier, no prompt overfitting*.

---

## Verdict

The architecture is already the right one. There is **one** structured reading of
each message (`understand()` → `Understanding` Pydantic object), routing/arithmetic/
approval gating are deterministic and *not* trusted to the model, and the anchoring-
bias and negative-boundary fixes from `improvements.md` are already in. You are not
far from the goal.

The two symptoms you feel have two specific causes, and neither is "the prompt needs
more rules":

1. **Misclassification** is a *model-capacity ceiling*, not a prompt gap. Everything
   runs on `llama-3.1-8b`. The remaining ~6% failures are exactly the boundary cases
   an 8B model loses (payment_claim vs history, return vs order vs dispute, dispute+
   settlement). Measured: 93.8% strict, `sa3_dispute` precision 80% (2 false-positives).
2. **Lost memory** is *architectural*. There is no single conversation-state object the
   classifier reads. "Memory" is three overlapping, **per-intent hardcoded** patches
   (history-bias text, continuity-fallback, slot carry-forward) that were hand-wired
   for exactly three intents — `dispute`, `payment_promise`, `call_schedule_request`.
   Any other multi-turn flow has *no* continuity, and even those three depend on a
   metadata side-channel that isn't guaranteed to be written.

Below, each constraint scored honestly, then the fix plan.

---

## 1. "No regex" — inventory

Regex is **not** one thing here. Two very different jobs wear the same syntax:

### Keep (arithmetic / grounding / safety — do **not** remove)
These are not classifiers. They are what stops the model hallucinating money.
Ponytail rule: never simplify away money/security paths.

| Where | What | Why it stays |
|---|---|---|
| `orchestrator.parse_number` + `SCALES` | Indian grouping / lakh-crore arithmetic | The number an agent acts on must be *ours*, never the model's |
| `orchestrator.verify_value` | verbatim-span check before trusting an amount | hallucinated figure has no way in |
| `orchestrator._clause_grounded`, `sa1._grounded` | reply/clause must overlap the real message | blocks fabricated requests & invented figures |
| `contracts._ID_RE`, `inbox` reply/quote strip | id shape, email cleanup | plumbing, not classification |

### Cut or demote (regex doing the model's job — this is what you actually mean)
These are redundant now that `understand()` extracts the same thing, and each one
creates a *second path* that disagrees with the model — the exact "sometimes wrong"
feeling.

| Where | What it does | Recommendation |
|---|---|---|
| `orchestrator.AMBIGUOUS_REFERENCE` (L67) | regex fires an `ambiguous_reference` **intent**, overriding the model | Keep the *disambiguation* (it's driven by DB voucher count, legitimate) but it is the one place a regex still emits an intent — flag, low priority |
| `orchestrator.ENTITY_PATTERNS["amounts"|"quantities"]` (L90-98) merged in `classify_intent` L998 | regex amount/qty extraction merged *under* the model's verified extraction | Redundant with `entities_from` + `verify_value`. Drop the amount/qty regex; keep the voucher one (cheap, feeds carry-forward) |
| `sa2_recovery._UNABLE` (L54, used L260) | keyword regex ("can't pay", "no money") firing a `RECOVERY_CONTACTED` event | The catalog already routes "unable to pay" to `payment_promise`. This is a redundant hardcoded keyword classifier — delete, let the structured signal drive the event |
| `sa1_general._TOP_ITEM` (L271) | regex ("most/top/favourite") to pick the top-items tool | Hardcoded tool-selection classifier; the LLM tool-menu path (L553) already covers it — candidate for deletion |

**Net:** you can honestly say "no regex classifier" after cutting four things. The
arithmetic/grounding regex is not a classifier and should stay — calling it "regex we
must remove" would delete your hallucination guard.

---

## 2. "No hardcoded classifier" — inventory

The live classification path is **already model-only** (`default_classifier()` →
`classify_llm`, no rules baseline in production; the 100%-passing "deterministic rules"
in the evals is an eval-only comparator, not wired into `handle()`). Good.

The hardcoded logic that remains is **not** in first-pass classification — it's in the
*memory/continuity* layer, and that's the problem area (§4):

- `classify_intent` L956-991 — three near-identical hardcoded prompt-injection blocks,
  one each for dispute / payment_promise / call_schedule.
- `_continuity_fallback` L589-610 — hardcoded routing to those same three intents when
  the model returns nothing.
- `_conversation_context` L899 — carries forward vouchers + dispute slots only.

These are hardcoded *per intent*. Adding a fourth multi-turn intent means copy-pasting
a fourth block in three places. That's the overfitting-by-accretion you want gone.

---

## 3. "No prompt overfitting" — scored

Mostly clean, two real findings:

- **Clean:** `INTENT_CATALOG` is defined by business event + negative boundary
  (`not_when`), deliberately free of sample customer wording (L133-137). The
  `Understanding` example is neutral (empty `requests`). The 1-shot `payment_promise`
  anchoring bias from `improvements.md` §3.1 is fixed. This is genuinely non-overfit.
- **Finding A (cheap win): schema field order defeats Chain-of-Thought.**
  `improvements.md` §3 says small models must emit *reasoning before the label*.
  But `Request` (contracts.py L431) emits `intent` as field **#1**, with `clause` #2
  and `reason` #8. The model commits the label token before it has written its
  evidence. Reordering to `clause` → `reason` → `intent` is a zero-overfit, zero-new-
  dependency accuracy lever the doc itself prescribes and that was never applied.
- **Finding B: single-model tier.** `improvements.md` §3.4/§3.5 prescribe routing
  compound/adversarial messages to `llama-3.3-70b`. `MODELS["classification"]` is 8B
  for *everything* (llm.py L34). The three eval failures (`GN-S-016`, `GN-M-004`,
  `GN-A-010`) are all compound/adversarial — precisely the 70B bucket. The capacity
  is configured and unused.

---

## 4. Root cause of lost memory (the important one)

The pipeline is **stateless per turn**. `handle()` defaults `thread_id` to a fresh
UUID per message (orchestrator.py L1299), so the LangGraph checkpointer does **not**
carry state across turns unless the caller passes `thread_id=conversation_id`. Real
continuity is *reconstructed from Mongo every turn* by `format_recent_history` +
`_conversation_context`. That reconstruction has three fragilities:

1. **It hinges on a metadata side-channel.** `_recent_intent_turn` (L859) only works if
   each prior inbound message was persisted with `metadata.classification.intents`.
   That write happens in `scripts/ui_server.py`. Call `handle()` any other way (tests,
   API, `persist=False`) and the continuity signal is silently absent — the classifier
   "forgets" even though the code "supports memory". This is very likely what you're
   hitting when it works in the UI but not elsewhere.
2. **Continuity exists for exactly 3 intents.** dispute, payment_promise,
   call_schedule. A follow-up to an order, a return, a health query, or *anything new*
   gets no bias and no fallback — a bare "yes" or a bare number after those reads as a
   fresh, unrelated message and lands on sa1_general's "couldn't find that". This is
   the "classified right once, then forgets" symptom, exactly.
3. **Three mechanisms, overlapping, none authoritative.** history-bias (in-prompt),
   continuity-fallback (post-model), slot carry-forward (entities). They partly
   duplicate and can disagree.

**The fix is one object, not more patches:** build a single `conversation_state` in
`load_context` — `{open_case?, open_promise?, pending_approval?, last_intent,
awaiting_slot}` derived from the DB (cases/approvals/promises collections, which are
authoritative and don't depend on message metadata) — and put it in the classifier
prompt *once*, generically ("there is an open <X> awaiting <slot>; a terse reply likely
continues it"). That replaces all three per-intent blocks **and** `_continuity_fallback`
**and** removes the metadata-side-channel dependency, because cases/approvals/promises
are written by the agents regardless of who called `handle()`. Fewer lines, works for
every intent, no new overfitting.

---

## Prioritized plan (all non-overfitting, no new deps)

| # | Change | Effort | Payoff |
|---|---|---|---|
| 1 | **Unify memory** into one DB-derived `conversation_state` in `load_context`; delete the 3 hardcoded history blocks, `_continuity_fallback`, and the metadata-side-channel dependency | M | Fixes "forgets after one turn" for *all* intents; removes the biggest chunk of hardcoded logic |
| 2 | **Reorder `Request`** fields to `clause → reason → intent` (CoT-first) | XS | Free accuracy on boundary cases, prescribed by your own doc |
| 3 | **Two-tier model:** route compound (>2 clauses) / adversarial to `llama-3.3-70b` via existing `reasoning` capability | S | Targets the exact failing bucket; ~80% of traffic stays cheap on 8B |
| 4 | **Delete redundant regex classifiers:** `sa2._UNABLE`, `sa1._TOP_ITEM`, `ENTITY_PATTERNS` amount/qty | S | Removes the second-path disagreements; lets you truthfully claim "no regex classifier" |
| 5 | Keep `thread_id` reconstruction but make continuity independent of it (covered by #1) | — | — |

**Do NOT touch:** `parse_number`, `verify_value`, `_clause_grounded`, `_grounded`, the
voucher regex, approval-gate constants (`HUMAN_APPROVAL_INTENTS`). Those are the money/
safety floor, not classifiers.

## Honest ceiling note

"99% structured output" is already true in *form* — every model call goes through
`complete_structured` with a strict Pydantic parse. The gap to 99% *accuracy* is the
8B ceiling (item #3) and the memory unification (item #1), not more prompt text. More
rules in the prompt will move you the wrong way.
