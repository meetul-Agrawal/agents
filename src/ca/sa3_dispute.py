"""Phase 6 — SA-3, the dispute agent.

Understands a customer's dispute, gathers evidence from the records that
actually exist for them, opens a case, and tells the customer what was found —
never who is right. Determining fault is a human call; SA-3's job stops at
"here is what our records show."

Evidence is gathered only from the same read tools SA-1 already uses
(`get_sales_history`, `get_receipts`, `get_outstanding`) — no raw voucher scan,
same domain-tool boundary every other agent respects.

A case is only opened once there is something concrete to attach to it:

* The message names a specific invoice (`entities["voucher_numbers"]`, already
  extracted and verified upstream). SA-3 looks each one up in this customer's
  own sales and receipts and reports what it finds — including the useful
  negative: an invoice number that does not exist on this account at all, which
  is itself evidence for a "wrong bill" or duplicate-invoice claim. When the
  found invoice has more than one line item and the message does not clearly
  name one, SA-3 asks which item is affected rather than opening a case that
  cannot say what is actually wrong.
* No invoice is named, but the complaint is about the account balance itself
  (the message says so — "balance", "ledger", "outstanding"). The current
  outstanding position is the relevant evidence here, so SA-3 uses it.
* No invoice is named and the complaint is not about the balance (damaged
  goods, wrong item, short supply, an unspecified issue). There is nothing SA-3
  can check yet, so it asks for the invoice number, the item, and what went
  wrong — no case, no evidence dump, no unrelated figure. Dumping the account's
  entire outstanding balance here was the exact failure this replaced: it
  answered a damaged-goods complaint with an unrelated ₹1+ crore balance across
  262 invoices.

This book has no credit-note or order vouchers (`customer360.capabilities()`),
so evidence about a return being "not yet reflected" the way the vision doc's
example describes cannot be checked here — SA-3 states what the records show
and nothing it cannot verify.

A case is resolved by a human (`services.resolve_case`, driven from the ops UI
— see `scripts/ui_server.py`), never by this agent. `resolution_message` builds
the customer-facing follow-up for that decision; it is templated and grounded
the same way every other reply here is, and it is the caller's job to actually
deliver it (`services.send_customer_message`) once a conversation is known.

What the complaint is actually about — a damaged-goods issue vs. a balance
issue vs. anything else — comes from `orchestrator.understand()`, the same
single structured LLM call that already classified the message as a dispute
in the first place (`Request.about_balance` / `.issue_label` /
`.item_mentioned`, threaded through as `entities["dispute_*"]`). No pattern
list here tries to enumerate every way a customer might describe a problem —
that list is open-ended, which is exactly the kind of judgement call a model
handles and a pattern list cannot. When no model ran at all (`classify_rules`,
no provider configured), there is no classification to read; the safe default
is to treat the complaint as not-about-balance, which routes it to
`run()`'s "ask for specifics" branch rather than guessing — a wrong "ask"
costs one extra turn, a wrong guess risks answering with the wrong evidence.
"""

from __future__ import annotations

from typing import Any

from . import customer360 as c3
from . import services
from .contracts import AgentResult, AgentTask, Case, CustomerAssistState, ProposedAction, ToolCall
from .sa1_general import _collect_items, _fmt_date, _inr, _match_product, _phrase, _read, compose_grounded


def _dispute_signal(entities: dict[str, Any]) -> tuple[bool, str | None]:
    """(about_balance, issue_label) for this dispute, from the model's own
    classification. Absent (no model ran) defaults to not-about-balance —
    the safe branch that asks rather than guesses."""
    return bool(entities.get("dispute_about_balance")), entities.get("dispute_issue")


def _find(rows: list[dict[str, Any]], voucher_number: str) -> dict[str, Any] | None:
    for row in rows:
        if row.get("voucher_number") == voucher_number:
            return row
    return None


def _item_reference_match(number: str, sales: list[dict[str, Any]]) -> str | None:
    """A ref this customer's own invoices never show as a voucher number, but
    that turns up inside one of their real product names, is more likely a
    stock-item code the model misread as an invoice ref than an actual invoice
    that simply doesn't exist. Grounded against this customer's own catalog —
    not a fixed naming-convention pattern — so it holds regardless of how a
    given client names their items. Returns the matching item name, or None.

    Measured need: asking the extraction model to tell an invoice ref from an
    item code by prompt instruction alone still guessed the item code as the
    ref in 5/5 samples (see sa3_dispute tests) — the model can't be trusted to
    self-police here, so this checks the claim against real data instead.
    """
    needle = number.lower()
    for sale in sales:
        for it in (sale.get("items") or []):
            name = it.get("name") or ""
            if needle in name.lower():
                return name
    return None


def _voucher_evidence(
    cid: str, voucher_numbers: list[str], message: str, calls: list[ToolCall]
) -> list[dict[str, Any]]:
    """One evidence entry per cited voucher: what our own records show for it —
    including its line items, so "which stock item" has an answer — the fact
    that it does not appear on this account at all — or, when it doesn't
    appear as a voucher but does appear inside a real product name, that it
    looks like an item reference rather than an invoice number."""
    sales = _read(calls, "get_sales_history", lambda: c3.get_sales_history(cid), customer_id=cid) or []
    receipts = _read(calls, "get_receipts", lambda: c3.get_receipts(cid), customer_id=cid) or []

    evidence: list[dict[str, Any]] = []
    for number in voucher_numbers:
        sale = _find(sales, number)
        receipt = _find(receipts, number)
        if sale:
            item_names = [it.get("name") for it in (sale.get("items") or []) if it.get("name")]
            matched = _match_product(message, item_names) if len(item_names) > 1 else item_names
            evidence.append({
                "type": "invoice_on_record", "voucher_number": number,
                "amount": sale.get("amount"), "date": str(sale.get("date") or ""),
                "items": item_names, "matched_items": matched,
            })
        if receipt:
            evidence.append({
                "type": "receipt_on_record", "voucher_number": number,
                "amount": receipt.get("amount"), "date": str(receipt.get("date") or ""),
                "against_bills": receipt.get("against_bills"),
            })
        if not sale and not receipt:
            item_match = _item_reference_match(number, sales)
            if item_match:
                evidence.append({
                    "type": "looks_like_item_ref", "ref_given": number, "item_name": item_match,
                })
            else:
                evidence.append({"type": "voucher_not_found", "voucher_number": number})
    return evidence


def _outstanding_evidence(cid: str, calls: list[ToolCall]) -> list[dict[str, Any]]:
    o = _read(calls, "get_outstanding", lambda: c3.get_outstanding(cid), customer_id=cid)
    if o is None:
        return []
    return [{
        "type": "outstanding_snapshot", "outstanding": o.outstanding,
        "open_bill_count": o.open_bill_count,
    }]


def _summarize(evidence: list[dict[str, Any]]) -> tuple[str, bool]:
    """Plain-language, grounded restatement of the evidence list — every figure
    and reference here already exists in `evidence`. Returns (text, needs_more)
    — `needs_more` is set when a found invoice has more than one item and none
    is clearly the one the customer meant."""
    lines: list[str] = []
    needs_more = False
    for item in evidence:
        kind = item["type"]
        if kind == "invoice_on_record":
            items = item.get("items") or []
            line = (
                f"Invoice {item['voucher_number']} is on your account, dated "
                f"{_fmt_date(item['date']) if item['date'] else 'unknown'}, for {_inr(item['amount'] or 0)}"
            )
            matched = item.get("matched_items") or []
            if len(items) == 1:
                line += f" (item: {items[0]})."
            elif len(items) > 1 and len(matched) == 1:
                line += f". We've noted this as the {matched[0]} on that invoice."
            elif len(items) > 1:
                line += f". This invoice has more than one item ({', '.join(items)})."
                needs_more = True
            else:
                line += "."
            lines.append(line)
        elif kind == "receipt_on_record":
            lines.append(
                f"A receipt against {item['voucher_number']} is on record for {_inr(item['amount'] or 0)}."
            )
        elif kind == "voucher_not_found":
            lines.append(f"We could not find {item['voucher_number']} on your account at all.")
        elif kind == "looks_like_item_ref":
            ref = item.get("ref_given") or item.get("voucher_number")
            lines.append(
                f"\"{ref}\" matches the item \"{item['item_name']}\" in your "
                "purchase history, not an invoice number."
            )
            needs_more = True
        elif kind == "outstanding_snapshot":
            lines.append(
                f"Your current outstanding is {_inr(item['outstanding'])} across "
                f"{item['open_bill_count']} invoice(s)."
            )
    return " ".join(lines), needs_more


_MONEY_FIELDS = ("amount", "outstanding")


def _grounding_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evidence with money fields pre-formatted (`_inr`) before it reaches the
    composer — so the model copies an already-correct ₹ figure verbatim rather
    than reformatting a raw number itself and guessing a currency symbol."""
    out = []
    for item in evidence:
        entry = dict(item)
        for field in _MONEY_FIELDS:
            if entry.get(field) is not None:
                entry[field] = _inr(entry[field])
        out.append(entry)
    return out


def _intents_of(task: AgentTask) -> list[str]:
    named = task.inputs.get("intents")
    if isinstance(named, list) and named:
        return named
    return [part for part in task.action.split("+") if part]


def run(task: AgentTask, state: CustomerAssistState) -> AgentResult:
    intents = _intents_of(task)
    entities = task.inputs.get("entities") or state.entities or {}
    message_id = state.entities.get("message_id")

    def result(status: str, message: str | None, calls: list[ToolCall],
               case_id: str | None = None, actions: list[ProposedAction] | None = None) -> AgentResult:
        return AgentResult(
            agent="sa3_dispute", agent_task_id=task.agent_task_id, status=status,
            summary=f"opened case {case_id}" if case_id else "no dispute case opened",
            customer_message=message, tool_calls=calls, actions=actions or [],
        )

    if "dispute" not in intents or not state.customer_id:
        return result("needs_information", None, [])

    cid = state.customer_id
    calls: list[ToolCall] = []
    voucher_numbers = entities.get("voucher_numbers") or []
    about_balance, issue_label = _dispute_signal(entities)
    item_hint = entities.get("dispute_item")

    # No invoice cited, but an item was named (e.g. "issue in aata"): fuzzy-match
    # it against this customer's own purchase history the same way `_sales` does,
    # and resolve to that item's most recent invoice so the flow below can run
    # without the customer having to dig up an invoice number themselves. A tie
    # (multiple variants, e.g. several "aata" SKUs) is a genuine ambiguity — ask
    # which exact item, don't silently guess and don't re-ask for everything.
    if not voucher_numbers and not about_balance and item_hint:
        rows = _read(calls, "get_sales_history", lambda: c3.get_sales_history(cid), customer_id=cid) or []
        items = _collect_items(rows)
        matches = _match_product(item_hint, items.keys())
        if len(matches) == 1:
            voucher_numbers = [items[matches[0]]["voucher"]]
        elif len(matches) > 1:
            composed = compose_grounded(
                "Write a short reply asking the customer exactly which of these items "
                "they mean, so we can look into their complaint.",
                {"candidates": matches},
            )
            ask = composed or _phrase(
                f"We found a few items matching that — did you mean "
                f"{', '.join(matches)}? Let us know which one and we'll open a case."
            )
            return result("needs_information", ask, calls)

    # Nothing concrete to check: no invoice cited (or resolved above), and the
    # complaint is not about the balance itself (where the balance IS the
    # relevant evidence). Ask for the specifics instead of opening an empty case
    # or answering with an unrelated figure — this is the fix for the ₹1+ crore
    # balance dump.
    if not voucher_numbers and not about_balance:
        composed = compose_grounded(
            "Write a short reply asking the customer for the invoice number, "
            "which item was affected, and a short description of the issue "
            "(for example: damaged, wrong item, or short quantity), so we can "
            "look into their complaint.",
            {},
        )
        ask = composed or _phrase(
            "Thanks for letting us know — to look into this, could you share the invoice "
            "number, which item was affected, and a short description of the issue (for "
            "example: damaged, wrong item, or short quantity)? Once we have that we'll open "
            "a case and take a look."
        )
        return result("needs_information", ask, calls)

    # Union, not either/or: item_hint may be a carried-forward slot from an
    # earlier turn (e.g. "issue in rawa" ... later "URD/113/6892" — the invoice
    # arrives with no item text of its own, so item_hint is the only source).
    # But when THIS turn's own message names the item ("Gangwal Sattu Aata
    # 500gm packages are leaking") a stale/vague item_hint from a prior turn
    # ("last order") must not shadow it — match against both.
    match_text = " ".join(t for t in (item_hint, state.message) if t)
    evidence = (
        _voucher_evidence(cid, voucher_numbers, match_text, calls)
        if voucher_numbers else _outstanding_evidence(cid, calls)
    )

    priority = "high" if state.urgency == "high" else "normal"
    label = issue_label or (', '.join(voucher_numbers) if voucher_numbers else 'account query')
    title = f"Dispute — {label}"
    case, created = services.create_case(
        cid, title, priority=priority, evidence=evidence,
        conversation_id=state.conversation_id, message_id=message_id,
    )
    calls.append(ToolCall(tool="create_dispute", arguments={"case_id": case.case_id}))
    actions = [ProposedAction(
        type="create_dispute", mode="auto", executed=True,
        payload={"case_id": case.case_id, "priority": priority},
    )]
    if created:
        services.record_event(
            cid, "DISPUTE_CREATED", "sa3_dispute", conversation_id=state.conversation_id,
            message_id=message_id, payload={"case_id": case.case_id},
        )
        calls.append(ToolCall(tool="create_event"))

    summary, ambiguous_item = _summarize(evidence)
    unfound = [e["voucher_number"] for e in evidence if e["type"] == "voucher_not_found"]
    item_refs = [e.get("ref_given") or e.get("voucher_number") for e in evidence if e["type"] == "looks_like_item_ref"]
    needs_more = bool(unfound) or bool(item_refs) or ambiguous_item

    facts: dict[str, Any] = {"case_id": case.case_id, "evidence": _grounding_evidence(evidence)}
    if item_refs:
        facts["ask"] = (
            "confirm the actual invoice/bill number — the number they gave matches an item "
            "name, not an invoice, on their account"
        )
    elif unfound:
        facts["ask"] = "confirm the invoice number, which could not be found on their account"
    elif ambiguous_item:
        facts["ask"] = "confirm which item on the invoice they mean"
    composed = compose_grounded(
        "Write a short reply telling the customer we've opened a case to look "
        "into their dispute, stating what the evidence facts show. "
        "If an 'ask' fact is present, end with that question. "
        "If there is NO 'ask' fact, do NOT ask any question — just confirm "
        "the case is opened, summarize the evidence, and say a colleague will review.",
        facts,
    )
    # Guard: if a question was requested ('ask' in facts) but LLM failed to ask it,
    # or if no question was requested ('ask' not in facts) but LLM invented one,
    # reject the composition and fall through to the verified deterministic template.
    if composed:
        has_question = "?" in composed
        wants_question = "ask" in facts
        if has_question != wants_question:
            composed = None
    message = composed or _phrase(
        f"Thank you for flagging this — we've opened case {case.case_id} to look into it. "
        f"{summary}"
        + (" Could you share the actual invoice/bill number?" if item_refs
           else " Could you double-check the invoice number?" if unfound
           else " Which item on that invoice did you mean?" if ambiguous_item else "")
        + " A colleague will review the details and get back to you."
    )
    return result(
        "needs_information" if needs_more else "completed", message, calls, case.case_id, actions,
    )


def resolution_message(case: Case, outcome: str, note: str = "") -> str:
    """The follow-up sent once a human resolves a case.

    The outcome sentence is fixed in code, driven by the `outcome` string
    that already came from `services.resolve_case` — never phrased by the
    model. See `compose_grounded`'s docstring: this model measurably states
    the opposite decision inside an otherwise-correct reply, so the one fact
    that must never be wrong is not entrusted to free text.

    `note` is ops' own words for the customer — appended verbatim, not run
    through the model. Measured `compose_grounded` silently replacing a
    substantive note ("more than 30 days, can't refund") with vague filler
    ("we appreciate your patience") — `_grounded` only blocks a new *number*,
    it does not check the note's content survived, so a paraphrase step here
    has a real chance of deleting the actual reason ops wrote."""
    if outcome == "solved":
        anchor = f"Update on your case {case.case_id} ({case.title}): this has been resolved."
    else:
        anchor = (
            f"Update on your case {case.case_id} ({case.title}): after review, we found no "
            "further action is needed."
        )
    return f"{anchor} {note}" if note else anchor
