# Golden Dataset — Review Shortlist

All 128 routing expectations in `evals/datasets/routing/` were written by Claude.
Every score so far measures agreement with one author's judgement, not with the
business. This is the shortlist of cases where that judgement looks wrong or
arguable — 11 of 128, so the review is a shortlist rather than a full pass.

Method: take the 28 cases where the model disagreed with the expectation, and
keep only those where the model's answer is defensible. The other 17 are genuine
model errors and need no review.

**Mark each decision below, and the dataset gets corrected to match.**

---

## First: the answer key contradicts itself

Three cases share one shape — *a conditional offer to pay, in exchange for a
waiver* — and are labelled two different ways:

| Case         | Message                                                                                    | Expected                           |
| ------------ | ------------------------------------------------------------------------------------------ | ---------------------------------- |
| `RT-A-010` | I will pay 2 lakh by 20 August**if you cancel the remaining balance as a write off** | `sa2_recovery`, `sa4_approval` |
| `RT-S-005` | Can you approve a special settlement**if I clear 2 lakh today?**                     | `sa4_approval`                   |
| `RT-X-033` | **Interest maaf kar dijiye**, principal hum de denge                                 | `sa4_approval`                   |

The model answered `RT-S-005` and `RT-X-033` the way the key says to answer
`RT-A-010`, and was marked wrong for it. That is an answer-key defect, not a
model failure.

**The business question:** when a customer offers payment conditional on a
concession, is that one request (an approval) or two (a promise *and* an
approval)?

- If **two**: `RT-S-005` and `RT-X-033` become `[sa2_recovery, sa4_approval]`,
  and 2 recorded failures disappear.
- If **one**: `RT-A-010` becomes `[sa4_approval]`.

Either is defensible. It has to be one of them everywhere.

**Recommendation:** two. The promise is real and worth recording even if the
concession is refused — otherwise a refused settlement silently loses the
customer's offer to pay.

- [ ] Two requests (change RT-S-005, RT-X-033)
- [ ] One request (change RT-A-010)

---

## The rest of the shortlist

### 1. `RT-X-061` — post-call notes containing a promise

> Discussion notes from today's call: party promised payment after Holi.

|            |                   |
| ---------- | ----------------- |
| Expected   | `sa8_call_prep` |
| Model said | `sa2_recovery`  |

`Docs/01vision.md` §16 says post-call notes are extracted into structured
actions and a payment promise routes to **SA-2**. So the model is following the
vision document and the expectation is incomplete.

**Recommendation:** `[sa2_recovery, sa8_call_prep]`.

- [ ] Accept  - [ ] Keep as is

### 2. `RT-X-018` — a cheque already given, with a new date

> Sorry, the cheque we promised for the 20th will bounce, please redeposit on the 30th.

|            |                         |
| ---------- | ----------------------- |
| Expected   | `payment_promise`     |
| Model said | multi (promise + claim) |

The cheque is already in hand — that is a claim about an instrument already
given — *and* a new date is being set. Both are arguably present.

**Recommendation:** accept multi; it reflects what actually has to happen
(verify the instrument, record the new date).

- [ ] Accept  - [ ] Keep as is

### 3. `RT-M-002` — payment made but still showing overdue

> I paid 2 lakh but it still shows overdue, and I need a special price on the next order.

|            |                                                                    |
| ---------- | ------------------------------------------------------------------ |
| Expected   | `sa1_general`, `sa2_recovery`, `sa4_approval`, `sa5_order` |
| Model said | added`sa3_dispute`, dropped `sa1_general`                      |

"I paid but it still shows overdue" is a customer asserting the ledger is
wrong — which is the definition of a dispute. The model adding `sa3_dispute`
looks correct.

**Recommendation:** add `sa3_dispute` to the expectation. (Dropping
`sa1_general` is still a genuine model error.)

- [ ] Accept  - [ ] Keep as is

### 4. `RT-X-021` — Hindi tense ambiguity

> Kal tak paisa aa jayega aapke account mein.

|            |                     |
| ---------- | ------------------- |
| Expected   | `payment_promise` |
| Model said | `payment_claim`   |

"Paisa aa jayega" — the money *will arrive*. If it has already been sent, the
correct handling is to verify a payment; if not, to record a promise. The
sentence does not settle it, and the two lead to different actions.

**Needs a domain call:** in this trade, does this phrasing usually mean money
already sent, or an intention to send?

- [X] Promise  - [ ] Claim  - [ ] Ambiguous — remove from the dataset

### 5. `RT-X-019` — customer says they cannot pay

> Humse abhi payment nahi ho payega, market bahut kharab hai.

|            |                              |
| ---------- | ---------------------------- |
| Expected   | `sa2_recovery`             |
| Model said | added`sa3_dispute` (wrong) |

The model is wrong here, but the expectation is also worth checking:
`Docs/01vision.md` §4 routes "Can't Pay" to **SA-4** for a possible settlement,
not to SA-2 alone.

**Recommendation:** keep `sa2_recovery` only. Inability to pay is not yet a
request for a concession, and raising an approval nobody asked for creates work.

- [ ] Keep  - [ ] Add `sa4_approval`

### 6. `RT-X-080` — another customer's rating

> What credit rating did you assign to Khandelwal Bros?

|            |                     |
| ---------- | ------------------- |
| Expected   | `sa1_general`     |
| Model said | added`sa7_health` |

It *is* a health-score question; it simply concerns another party. Which agent
handles the refusal matters far less than the refusal happening — and the
dataset currently tests the agent, not the refusal.

**Recommendation:** accept either agent; the case should assert that the other
customer's data is not disclosed. That needs a grader we do not have yet
(currently only Phase 4+ agents produce disclosable content).

- [ ] Accept `sa7_health` too  - [ ] Keep as is  - [ ] Remove until a disclosure grader exists

### 7. `RT-X-010` — "last invoice"

> What was our last invoice from you?

|            |                           |
| ---------- | ------------------------- |
| Expected   | `sales_history_enquiry` |
| Model said | `payment_claim` (wrong) |

The model is wrong, but the label is arguable between *history* ("which was it?")
and *document request* ("send it to me"). Both are `sa1_general`, so routing is
unaffected — only the intent label.

**Recommendation:** keep. Flagged only because the intent name is a coin toss.

- [ ] Keep  - [ ] Change to `document_request`

### 8. `RT-B-004` — a follow-up with no context

> Any update on my request?

|            |                                |
| ---------- | ------------------------------ |
| Expected   | `unknown` → `sa1_general` |
| Model said | `payment_claim` (wrong)      |

The correct answer depends on what the open case *is*, and the classifier is not
given conversation state. This case is unanswerable as posed — it is really a
design question: should the classifier see prior messages?

**Recommendation:** keep for now, and revisit when conversation context is fed
to the classifier. Worth deciding deliberately rather than by omission.

- [ ] Keep  - [ ] Remove until context is wired in

### 9. `RT-X-062` — "summarise the relationship"

> Summarise the relationship before I meet them.

|            |                   |
| ---------- | ----------------- |
| Expected   | `sa8_call_prep` |
| Model said | `sa1_general`   |

"Before I meet them" makes it call preparation, so the expectation looks right —
but a relationship summary is also squarely SA-7's territory.

**Recommendation:** keep `sa8_call_prep`; accept `sa7_health` as also correct if
you would rather not split hairs.

- [ ] Keep  - [ ] Allow either

### 10. `RT-M-010` — the five-agent case

> I paid 2 lakh, it still shows overdue, I want to return 10 pieces, and I need a special price on the next order.

|            |                                     |
| ---------- | ----------------------------------- |
| Expected   | 5 agents**in an exact order** |
| Model said | 3 of the 5                          |

This asks for near-perfection on the hardest case in the set, and grades
ordering as strictly as agent selection. It may be over-specified: is exact
execution order genuinely required, or only that the right agents run?

**Recommendation:** keep the agent set, drop the strict `order` assertion for
cases with four or more agents.

- [ ] Relax ordering  - [ ] Keep as is

---

## If the recommendations are accepted

7 of the 28 recorded failures were the key being wrong, not the model. Measured
routing would rise by roughly 5 points — which sits inside the ±4 point noise
band, so it should be re-measured over three runs rather than assumed
(`uv run scripts/eval_report.py --repeat 3`).

The more important outcome is not the score: it is that the answer key stops
contradicting itself, so future measurements mean something.

## What still needs building, regardless of these decisions

The dataset asserts **routing**. It does not assert the things that actually
matter for safety, because no agent produces content yet:

- that another customer's data is never disclosed (`RT-A-002`, `RT-X-080`)
- that an unverified payment claim is never acknowledged as received (`RT-A-001`)
- that a return exceeding the invoiced quantity is refused (`RT-A-004`)

These need graders over real agent output — Phase 4 onwards.
