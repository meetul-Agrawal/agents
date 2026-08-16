"""Phase 4 — SA-1, the read-only general agent.

The first real agent behind the orchestrator. It answers questions about a
customer's own records and does exactly one dangerous thing carefully: it never
states a number it did not read from the deterministic Customer-360 services.
The money is computed by `customer360`, never phrased into existence by a model.

Grounding is structural, not hoped-for. SA-1 assembles a reply out of the values
the read tools return and nothing else — there is no code path in which a figure
reaches the customer without having come from a tool. The critical Phase-4
failure tests (wrong customer, nonexistent invoice, conflicting/absent records,
hallucination) all pass because a template cannot invent a balance.

Two boundaries SA-1 owns, not the orchestrator:

* **Cross-customer refusal.** A request for another party's terms routes here and
  is refused here — SA-1 is the last guard before a data leak.
* **No guessing a voucher.** An ambiguous invoice reference is left to the
  orchestrator's clarification; SA-1 reads nothing and states nothing.

The reply is templated. An optional LLM pass rewrites the finished template into
warmer prose, but the template stays the source of truth: the rewrite is checked
against it and rejected unless every number and voucher in the rewrite also
appears in the template (`_grounded`). The model can only reword what is already
grounded — it is never shown the raw records, and it cannot introduce a figure.
Without a provider configured the pass is skipped and the template is sent as-is.

ponytail: each enquiry calls the read service afresh, so a message asking two
things scans the voucher book twice (~280ms each). Fine per conversation; thread
one `VoucherSet` through the handlers if a batch job ever fans this out.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable

from . import customer360 as c3
from .contracts import AgentResult, AgentTask, CustomerAssistState, ModelOutput, ToolCall


def _inr(amount: float) -> str:
    return f"₹{amount:,.2f}"


def _fmt_date(value: Any) -> str:
    return value.strftime("%d %b %Y") if isinstance(value, date) else str(value or "")


def _read(calls: list[ToolCall], tool: str, fn: Callable[[], Any], **arguments: Any) -> Any:
    """Call a read service, record the tool call, and swallow failure.

    A read that raises (customer gone, database unreachable) becomes a failed
    ToolCall and a `None` return — the handler degrades its line, the run never
    crashes. The tool name must be one the registry grants SA-1, or `review()`
    would flag the call as a permission breach.
    """
    call = ToolCall(tool=tool, arguments=arguments)
    try:
        value = fn()
    except Exception as exc:
        call.ok = False
        call.error = f"{type(exc).__name__}: {exc}"
        calls.append(call)
        return None
    calls.append(call)
    return value


# --------------------------------------------------------------------------
# Per-intent handlers — each returns one grounded line (or None) and records
# the tools it used. Numbers come only from the returned service objects.
# --------------------------------------------------------------------------


# A handler answers one intent: it returns (line, status). `status` is usually
# None (a plain completed answer) but may be "needs_information" when SA-1 has to
# ask a follow-up rather than guess.
Handler = Callable[[str, dict, str, list[ToolCall]], "tuple[str | None, str | None]"]


def _outstanding(cid: str, entities: dict, message: str, calls: list[ToolCall]) -> tuple[str | None, str | None]:
    o = _read(calls, "get_outstanding", lambda: c3.get_outstanding(cid), customer_id=cid)
    if o is None:
        return "I couldn't retrieve your balance just now; a colleague will follow up.", None
    if o.outstanding <= 0.01 and o.open_bill_count == 0:
        return "Your account is fully settled — there is no outstanding balance.", None

    line = f"Your current outstanding is {_inr(o.outstanding)} across {o.open_bill_count} open bill(s)."
    aged = {k: v for k, v in o.ageing.items() if v > 0}
    if aged:
        line += " Ageing — " + ", ".join(f"{k} days: {_inr(v)}" for k, v in aged.items()) + "."
    if o.open_bills:
        oldest = o.open_bills[:3]
        line += "\nOldest open bill(s):\n" + "\n".join(
            f"- {b.voucher_number} dated {_fmt_date(b.invoice_date)}: {_inr(b.outstanding)} outstanding"
            for b in oldest
        )
    return line, None


def _payments(cid: str, entities: dict, message: str, calls: list[ToolCall]) -> tuple[str | None, str | None]:
    b = _read(calls, "get_payment_history", lambda: c3.get_payment_history(cid), customer_id=cid)
    if b is None:
        return "I couldn't retrieve your payment history just now; a colleague will follow up.", None
    if b.receipt_count == 0:
        return "We have no recorded payments from you yet.", None

    line = f"We have received {b.receipt_count} payment(s) totalling {_inr(b.total_received)}."
    if b.last_receipt:
        line += f" Your most recent payment was on {_fmt_date(b.last_receipt)}."
    if b.avg_days_to_settle is not None:
        line += f" On average, bills are settled in {b.avg_days_to_settle:.0f} days."
    return line, None


# --------------------------------------------------------------------------
# Product identification — grounded in what the customer actually bought
# --------------------------------------------------------------------------

# Words that carry no product identity, so they never help match an item name.
_STOP = {
    "the", "a", "an", "of", "for", "to", "my", "our", "me", "i", "we", "you", "your",
    "want", "see", "show", "last", "latest", "recent", "price", "prices", "rate",
    "rates", "cost", "costs", "please", "give", "tell", "what", "whats", "which",
    "is", "was", "were", "on", "and", "as", "well", "also", "return", "returns",
    "past", "cause", "because", "due", "quality", "issue", "issues", "order",
    "orders", "purchase", "purchased", "purchases", "history", "buy", "bought",
    "get", "this", "that", "from", "in", "at", "it", "with", "about", "much", "how",
}

# A number glued to a unit — a strong sign the message names a physical product.
_SIZE = re.compile(
    r"\b\d+\s?(?:kg|kgs|g|gm|gms|gram|grams|ml|l|ltr|litre|liter|pcs|pc|pkt|packet|dozen|box|bag)\b",
    re.I,
)


def _norm(text: str) -> str:
    """'5 kg' -> '5kg', so a spaced size matches a joined one."""
    return re.sub(r"(\d)\s+(kg|kgs|g|gm|ml|l|ltr|pcs|pc|pkt)\b", r"\1\2", (text or "").lower())


def _sig_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", _norm(text)) if len(t) >= 2 and t not in _STOP}


def _collect_items(rows: list[dict]) -> dict[str, dict]:
    """item name -> its most recent line. `rows` are newest-first, so the first
    time a name appears is its latest price."""
    items: dict[str, dict] = {}
    for r in rows:
        for it in r.get("items") or []:
            name = (it.get("name") or "").strip()
            if name and name not in items:
                items[name] = {
                    "rate": it.get("rate"), "qty": it.get("qty"),
                    "date": r.get("date"), "voucher": r.get("voucher_number"),
                }
    return items


def _match_product(message: str, item_names) -> list[str]:
    """Item names the message points at, ranked by how many of their significant
    tokens it mentions. Only the top-scoring tier is returned, so a tie means a
    genuine ambiguity to ask about. Empty when no purchased product is named."""
    want = _sig_tokens(message)
    scored = [(len(_sig_tokens(name) & want), name) for name in item_names]
    scored = [(s, name) for s, name in scored if s]
    if not scored:
        return []
    best = max(s for s, _ in scored)
    return [name for s, name in sorted(scored, reverse=True) if s == best]


def _sales(cid: str, entities: dict, message: str, calls: list[ToolCall]) -> tuple[str | None, str | None]:
    rows = _read(calls, "get_sales_history", lambda: c3.get_sales_history(cid), customer_id=cid)
    if rows is None:
        return "I couldn't retrieve your purchase history just now; a colleague will follow up.", None
    if not rows:
        return "We have no sales invoices on record for you.", None

    items = _collect_items(rows)
    matches = _match_product(message, items.keys())

    if len(matches) == 1:
        name, occ = matches[0], items[matches[0]]
        if occ["rate"] is None:
            return (
                f"I found {name} in your orders but no unit price was recorded on the latest one "
                f"({occ['voucher']}). Would you like me to check an earlier invoice?",
                None,
            )
        return (
            f"The last recorded price of {name} was {_inr(float(occ['rate']))} per unit "
            f"(invoice {occ['voucher']} dated {_fmt_date(occ['date'])}).",
            None,
        )

    if len(matches) > 1:
        # Several products fit — ask which, rather than guess one.
        return (
            f"I have a few products matching that in your orders — {', '.join(matches[:5])}. "
            "Which one did you mean?",
            "needs_information",
        )

    if _SIZE.search(message or ""):
        # A product was clearly named but is not in their orders: ask, don't dump.
        return (
            "I couldn't find that product in your recent orders. Could you confirm the exact name "
            "and pack size?",
            "needs_information",
        )

    listed = [r for r in rows[:5] if r.get("voucher_number")]
    if not listed:
        return "We have no sales invoices on record for you.", None
    return (
        f"Your {len(listed)} most recent invoice(s):\n" + "\n".join(
            f"- {r['voucher_number']} dated {_fmt_date(r.get('date'))}: {_inr(r.get('amount') or 0)}"
            for r in listed
        ),
        None,
    )


def _document(cid: str, entities: dict, message: str, calls: list[ToolCall]) -> tuple[str | None, str | None]:
    # We hold no document-delivery capability, so this acknowledges rather than
    # promising something the system cannot do. No records are read.
    vouchers = entities.get("voucher_numbers") or []
    if vouchers:
        return (
            f"You asked for a copy of {', '.join(vouchers)}. I've logged the request and a "
            "colleague will send the document to your registered contact.",
            None,
        )
    return (
        "I've logged your document request. Could you confirm which invoice or statement you'd "
        "like, and we'll send it across.",
        None,
    )


HANDLERS: dict[str, Handler] = {
    "outstanding_enquiry": _outstanding,
    "payment_history_enquiry": _payments,
    "sales_history_enquiry": _sales,
    "document_request": _document,
}

_HELP = (
    "I can help with your account balance, recent invoices, payments and receipts. "
    "What would you like to know?"
)

_REFUSAL = (
    "For privacy and security, I can only share information about your own account, "
    "not another customer's."
)


# --------------------------------------------------------------------------
# Optional LLM phrasing — reword the grounded template, never the records
# --------------------------------------------------------------------------

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
# ponytail: local copy of the orchestrator's voucher pattern — importing it would
# make sa1 -> orchestrator, and orchestrator already imports sa1. Keep in sync.
_VOUCHER = re.compile(r"\b[A-Z]{2,6}(?:/[A-Z0-9]{1,6}){1,3}/\d+\b")


class _Phrasing(ModelOutput):
    text: str = ""


_PHRASE_SYSTEM = (
    "You rewrite a customer-service reply for a business-to-business receivables "
    "desk so it reads a little warmer and more natural. Keep it concise and "
    "professional.\n"
    "Absolute rule: never add, remove, or change any number, amount, date or "
    "invoice reference. Every figure in your reply must already appear in the "
    "input, unchanged. Invent nothing. Return only the rewritten reply."
)


def _numbers(text: str) -> set[float]:
    out: set[float] = set()
    for token in _NUM.findall(text):
        try:
            out.add(float(token.replace(",", "")))
        except ValueError:
            pass
    return out


def _grounded(template: str, candidate: str) -> bool:
    """A rewrite is allowed only if it introduces no new figure or voucher.
    Dropping detail is fine; inventing or altering one is not."""
    return (
        bool(candidate.strip())
        and _numbers(candidate) <= _numbers(template)
        and set(_VOUCHER.findall(candidate)) <= set(_VOUCHER.findall(template))
    )


def _llm_phrase(template: str) -> str | None:
    """One model call, or None when no provider is configured. Monkeypatched in
    tests so the phrasing path is exercised without a network."""
    import os

    from . import llm

    if os.getenv("CA_PHRASE", "on").lower() == "off" or not llm.available():
        return None
    try:
        out = llm.complete_structured(
            _Phrasing, _PHRASE_SYSTEM, f"Rewrite this reply:\n{template}",
            capability="summarization", example={"text": template},
        )
    except llm.LLMUnavailable:
        return None
    return out.text or None


def _phrase(template: str) -> str:
    candidate = _llm_phrase(template)
    return candidate if candidate and _grounded(template, candidate) else template


def _intents_of(task: AgentTask) -> list[str]:
    named = task.inputs.get("intents")
    if isinstance(named, list) and named:
        return named
    return [part for part in task.action.split("+") if part]


def run(task: AgentTask, state: CustomerAssistState) -> AgentResult:
    intents = _intents_of(task)
    entities = task.inputs.get("entities") or state.entities or {}

    def result(status: str, message: str | None, calls: list[ToolCall]) -> AgentResult:
        return AgentResult(
            agent="sa1_general",
            agent_task_id=task.agent_task_id,
            status=status,
            summary=f"answered {'+'.join(intents) or 'general enquiry'}",
            customer_message=message,
            tool_calls=calls,
        )

    # A request for another customer's information is refused before any read —
    # SA-1 is the last line against a cross-customer data leak.
    if "cross_customer_request" in intents:
        return result("completed", _REFUSAL, [])

    # An ambiguous voucher reference is the orchestrator's to clarify; SA-1 must
    # not guess which bill is meant, so it reads nothing and stays silent.
    if "ambiguous_reference" in intents:
        return result("completed", None, [])

    if not state.customer_id:
        return result("needs_information", None, [])

    cid = state.customer_id
    calls: list[ToolCall] = []
    sections: list[str] = []
    status = "completed"
    for name in intents:
        handler = HANDLERS.get(name)
        if handler is None:
            continue
        line, st = handler(cid, entities, state.message, calls)
        if line:
            sections.append(line)
        if st:
            status = st

    if not sections and "unknown" in intents:
        sections.append(_HELP)

    if not sections:
        # Every read failed, or nothing SA-1 handles was asked. Say nothing and
        # let the orchestrator fall back rather than invent a reply.
        status = "needs_information" if any(not c.ok for c in calls) else "completed"
        return result(status, None, calls)

    return result(status, _phrase("\n\n".join(sections)), calls)
