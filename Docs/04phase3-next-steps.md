# Phase 3 — What To Improve Next

Written 2026-08-14, from the failure data in `Docs/03phase3-evaluation.md`
(128 routing cases, 3 samples per configuration).

## Where we are

| Classifier                      | Routing         | Safety          | Spread over 3 runs   |
| ------------------------------- | --------------- | --------------- | -------------------- |
| `classify_rules`              | 64.1%           | 68.8%           | 0.0% (deterministic) |
| `classify_llm` (llama-3.1-8b) | **77.6%** | **80.7%** | 1.6%                 |

The 13.5-point gap is real — far outside the noise. Anything smaller than about
4 points is not, unless it is measured over repeated runs.

## The failure shape

28 failures, but they are mostly one problem rather than 28:

| Error                                        | Count |
| -------------------------------------------- | ----- |
| `sa2_recovery` added when it should not be | 9     |
| `sa1_general` dropped                      | 7     |
| `sa8_call_prep` dropped                    | 5     |
| `sa3_dispute` dropped                      | 2     |
| everything else                              | 5     |

**16 of 28 (57%) are the same failure**: the model collapses a multi-request
message onto its most salient intent and drops the quieter enquiry beside it.
That is a completeness problem, not a problem with how the intents are
described.

Failing tags: hinglish 9, multi_agent 8, recovery 5, call_prep 4.

### Not all failures cost the same

| Kind                                                  | Count | Consequence                                                           |
| ----------------------------------------------------- | ----- | --------------------------------------------------------------------- |
| Benign — an extra read-only agent                    | 5     | customer gets correct information plus a little more; no action taken |
| Harmful — an agent dropped, or an acting agent added | 23    | wrong or incomplete answer                                            |

Counting only harmful errors, effective routing is **82.0%**, not 77.6%. Worth
keeping in mind before optimising the headline number.

---

## Recommended order

### 1. Human-review the dataset — before any further model work

**Cost:** ~30 minutes of review. No compute.

All 128 expectations were written by Claude, so every score to date measures
agreement with one author's judgement, not with the business. Some recorded
failures look like errors in the answer key rather than the model:

- `RT-B-004` "Any update on my request?" — expected `unknown`, model said
  `outstanding_enquiry`. The model is arguably right.

`Docs/02phasesWithEval.md` asks for a *human-reviewed* Golden Dataset and we do
not have one. Everything downstream of a wrong answer key is wasted effort.

**This is blocking.** Highest value, lowest cost.

### 2. Test the 70B once

**Cost:** ~1.7h wall-clock, unattended. `uv run scripts/eval_report.py llm-70b`

The largest unknown in the system, untested only because the endpoint serves it
at ~48s per call. One run is enough to decide: a result above ~85% is outside
the noise band and settles the question. Currently we are choosing a model
without having measured the alternative.

### 3. Make the schema force completeness

**Cost:** small change to `Understanding`. Targets 57% of failures.

Have the model enumerate the distinct asks first, then classify each one,
instead of emitting a request list directly:

```python
class Understanding(ModelOutput):
    clauses: list[str]          # every distinct ask, verbatim
    requests: list[Request]     # one per clause
```

Structural pressure not to drop the second ask. No regex and no phrase
matching — the shape of the schema does the work.

### 4. Make omission cost more than addition

**Cost:** a few lines, deterministic.

An extra read-only agent is nearly free; a dropped agent is a wrong answer to a
customer. When confidence is low, include `sa1_general` rather than omit it.
Same asymmetry as the approval gate, and it converts harmful failures into
benign ones.

---

## What not to do

- **More prompt tuning.** Changes are now smaller than the ±4 point noise floor.
  Without three runs per variant you cannot distinguish improvement from luck.
- **A rules ∪ LLM ensemble.** It would raise recall, but it puts the regexes back
  into the live path, against the direction set for this work. Only revisit with
  measurement showing a clear win.

---

## The strategic question

**Is 77.6% routing over mock agents the right thing to optimise?**

No agent is real yet. Routing perfectly to a mock delivers nothing to a
customer. Phase 4 — a real SA-1 over the Customer 360 tools built in Phase 1 —
turns the most common routing target into actual value. Conveniently, the most
common benign error is an extra `sa1_general`, which stops mattering once SA-1
genuinely answers.

**Suggested plan:** do (1) now, start (2) in the background, then move to
Phase 4 and revisit routing once there is a real agent behind it. (3) and (4)
are worth doing, but they are refinements rather than blockers.

---

## Open items carried forward

- **Safety is 80.7%, not 100%.** `enforce_approval_gate` backstops the
  money-critical path deterministically, but the agent set itself is still wrong
  on roughly one message in five. Acceptable over mock agents; not acceptable
  once SA-2 can create a real payment promise.
- **The NVIDIA API key in `.env.example` is live and committed.** Rotate it, and
  restore the placeholder in the example file.
- **`vouchers` has no useful index.** One customer costs a ~280ms collection
  scan. Fine per conversation, not fine for batch fan-out.



My recommendation: do (1) now, kick off (2) in the background, then move to Phase 4 and revisit routing when there's a real agent behind it. (3) and (4) are worth doing but they're refinements, not blockers
