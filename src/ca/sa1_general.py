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

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable

from pydantic import BaseModel

from . import customer360 as c3
from .contracts import AgentResult, AgentTask, CustomerAssistState, ModelOutput, PaymentBehaviour, ToolCall


def _inr(amount: float) -> str:
    return f"₹{amount:,.2f}"


def _qty(n: float) -> str:
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.2f}"


def _format_rate(rate_val: Any) -> str:
    if rate_val is None:
        return ""
    if isinstance(rate_val, (int, float)):
        return _inr(float(rate_val))
    rate_str = str(rate_val).strip()
    if "/" in rate_str:
        num_part, unit_part = rate_str.split("/", 1)
        try:
            return f"{_inr(float(num_part.strip()))}/{unit_part.strip()}"
        except Exception:
            return rate_str
    try:
        return _inr(float(rate_str))
    except Exception:
        return rate_str


def _fmt_date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    if isinstance(value, str) and len(value) == 10 and value.count("-") == 2:
        try:
            return date.fromisoformat(value).strftime("%d %b %Y")
        except Exception:
            pass
    return str(value or "")


_WEEKDAYS = {d: i for i, d in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}


def resolve_date_fields(
    *, relative_days: int | None, weekday: str | None, end_of_month: bool,
    day: int | None, month: int | None, year: int | None, today: date,
) -> date | None:
    """Turns a model's structured date fields (a day count, a weekday, an
    explicit day/month/year) into a real `date` — our own arithmetic, never
    the model's guess. Shared by every agent that reads an open-ended date
    phrase (`sa2_recovery.parse_due_date`, SA-4's call-schedule extraction):
    one deterministic core, one hallucination-safe boundary, not a copy per
    caller. See `sa2_recovery.parse_due_date` for the full rationale."""
    if relative_days is not None:
        return today + timedelta(days=relative_days)
    if weekday in _WEEKDAYS:
        delta = (_WEEKDAYS[weekday] - today.weekday()) % 7
        return today + timedelta(days=delta or 7)  # the next one, never today
    if end_of_month:
        first_next = date(today.year + (today.month == 12), (today.month % 12) + 1, 1)
        return first_next - timedelta(days=1)
    if day is not None:
        resolved_month = month or today.month
        try:
            candidate = date(year or today.year, resolved_month, day)
        except ValueError:
            return None
        if year is None and candidate < today:
            # No year given and the date's already past: next occurrence.
            # A named month stays that month, next year; an unnamed one just
            # rolls to next month.
            next_month = resolved_month if month else (resolved_month % 12 + 1)
            try:
                candidate = date(today.year + 1, next_month, day)
            except ValueError:
                return None
        return candidate
    return None


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

    # The headline MUST be the bill-level figure (`o.outstanding`), never
    # `o.net_balance`. net_balance is the raw ledger closing balance, and in this
    # book that number is actively misleading: ~22% of receipts settle invoices
    # from before this book started, so subtracting them from the current book's
    # invoices makes genuine debtors look paid up or even in credit. Verified on
    # real data — Aadinath Traders: net_balance says "credit of ₹49,458", the
    # bill-level truth (cross-checked against an independent implementation) is
    # "owes ₹386,114". See Docs and `customer360.compute_outstanding`.
    if o.outstanding <= 0.01:
        return f"{o.ledger_name} — account is fully settled, nothing outstanding.", None

    line = f"{o.ledger_name} — outstanding {_inr(o.outstanding)} across {o.open_bill_count} invoice(s)."
    if o.open_bills:
        line += "\nOldest open invoice(s):\n" + "\n".join(
            f"- {b.voucher_number} dated {_fmt_date(b.invoice_date)}: {_inr(b.outstanding)}"
            for b in o.open_bills[:3]
        )
    return line, None


def _payments(cid: str, entities: dict, message: str, calls: list[ToolCall]) -> tuple[str | None, str | None]:
    query = c3.parse_payment_history_query(message)
    def _fetch():
        try:
            return c3.get_payment_history(cid, query)
        except TypeError:
            return c3.get_payment_history(cid)

    res = _read(calls, "get_payment_history", _fetch, customer_id=cid)
    if res is None:
        return "I couldn't retrieve your payment history just now; a colleague will follow up.", None

    if isinstance(res, PaymentBehaviour):
        if res.receipt_count == 0:
            return "We have no recorded payments from you yet.", None
        line = f"We have received {res.receipt_count} payment(s) totalling {_inr(res.total_received)}."
        if res.last_receipt:
            line += f" Your most recent payment was on {_fmt_date(res.last_receipt)}."
        if res.avg_days_to_settle is not None:
            line += f" On average, bills are settled in {res.avg_days_to_settle:.0f} days."
        return line, None

    if res.get("receipt_count", 0) == 0:
        if query.period and query.period != "all_time":
            return f"We have no recorded payments from you for {query.period.replace('_', ' ')}.", None
        return "We have no recorded payments from you yet.", None

    count = res["receipt_count"]
    total = res["total_received"]
    last_r = res.get("last_receipt")
    avg_speed = res.get("avg_days_to_settle")
    receipts = res.get("receipts") or []

    # 1. Specific voucher or UTR query
    if query.voucher_number and receipts:
        r0 = receipts[0]
        narr_info = f" (Narration: {r0['narration']})" if r0.get("narration") else ""
        return (
            f"Receipt {r0['voucher_number']} dated {_fmt_date(r0['date'])} of {_inr(r0['amount'])}{narr_info}.",
            None,
        )

    # 2. Recent receipts list requested
    if query.metric == "recent_payments" or (query.limit and query.limit <= 10 and query.metric != "total_amount"):
        listed = receipts[: (query.limit or 5)]
        lines = [f"Your {len(listed)} most recent payment(s):"]
        for r in listed:
            lines.append(f"- {r['voucher_number']} dated {_fmt_date(r['date'])}: {_inr(r['amount'])}")
        return "\n".join(lines), None

    # 3. Specific period total
    if query.period and query.period != "all_time":
        period_str = query.period.replace("_", " ")
        line = f"In {period_str}, we received {count} payment(s) totalling {_inr(total)}."
        if last_r:
            line += f" Your latest payment in this period was on {_fmt_date(last_r)}."
        return line, None

    # 4. Standard overview
    line = f"We have received {count} payment(s) totalling {_inr(total)}."
    if last_r:
        line += f" Your most recent payment was on {_fmt_date(last_r)}."
    if avg_speed is not None:
        line += f" On average, bills are settled in {avg_speed:.0f} days."
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

# "what did I order the most" names no product — it asks the aggregate itself.
_TOP_ITEM = re.compile(r"\b(most|top|highest|frequently|frequent|maximum|favou?rite)\b", re.I)


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

    if not matches and _TOP_ITEM.search(message or ""):
        top = _read(calls, "get_top_purchased_items", lambda: c3.top_purchased_items(cid), customer_id=cid)
        if not top:
            return "We have no sales invoices on record for you.", None
        best = top[0]
        line = (
            f"Your most-purchased item is {best['item']} — {_qty(best['total_qty'])} {best['unit']} "
            f"across {best['order_count']} order line(s)."
        )
        if len(top) > 1:
            line += "\nNext: " + ", ".join(
                f"{r['item']} ({_qty(r['total_qty'])} {r['unit']})" for r in top[1:5]
            )
        return line, None

    if len(matches) == 1:
        name, occ = matches[0], items[matches[0]]
        if occ["rate"] is None:
            return (
                f"I found {name} in your orders but no unit price was recorded on the latest one "
                f"({occ['voucher']}). Would you like me to check an earlier invoice?",
                None,
            )
        return (
            f"The last recorded price of {name} was {_format_rate(occ['rate'])} per unit "
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


_COMPOSE_SYSTEM = (
    "You write a short, professional customer-service reply for a "
    "business-to-business receivables desk, using ONLY the facts given to you.\n"
    "Absolute rule: never state a number, amount, date or reference that is not "
    "in the facts. Never state a decision or outcome the facts do not contain. "
    "Return only the reply text."
)


def compose_grounded(instruction: str, facts: dict[str, Any]) -> str | None:
    """Ask the model to write the whole customer-facing reply from a fixed set
    of facts — not just reword a pre-built template — then verify it stated no
    figure or reference the facts do not contain. Returns None when no
    provider is configured or the candidate fails grounding; callers keep one
    minimal, factual fallback line for that case, since something must reach
    the customer even with no model available.

    Not safe for a fact whose *polarity* is the whole message (approved vs.
    rejected, solved vs. dropped): measured this model stating the opposite
    decision in the same reply it correctly labelled elsewhere (e.g. "...has
    been approved. However, ... it was not approved.") — `_grounded` doesn't
    catch this, it only checks no extra figure was introduced. Callers with a
    decision like that keep the verdict sentence in code (see
    `sa4_approval.decision_message`, `sa3_dispute.resolution_message`) and use
    this only to compose the surrounding, non-decision-bearing text.
    """
    import os

    from . import llm

    if os.getenv("CA_PHRASE", "on").lower() == "off" or not llm.available():
        return None
    blob = json.dumps(facts, default=str)
    try:
        out = llm.complete_structured(
            _Phrasing, _COMPOSE_SYSTEM, f"{instruction}\n\nFacts:\n{blob}",
            capability="summarization", example={"text": "a short reply using only the facts above"},
        )
    except llm.LLMUnavailable:
        return None
    return out.text if out.text and _grounded(blob, out.text) else None


# --------------------------------------------------------------------------
# Tool-selection fallback — the LLM chooses which vetted read to run, we run it
# --------------------------------------------------------------------------
#
# Fires only when no fixed handler answered. The LLM never writes a query and
# never chooses the customer: it picks a tool name from this menu, we run the
# deterministic read scoped to *this* customer, and the composed answer is
# checked against the returned data (`_grounded`) so it cannot invent a figure.
# Cross-customer access and wrong-money are structurally impossible here.

# menu name -> (what it returns, the registered tool name, cid-scoped reader)
TOOL_MENU: dict[str, tuple[str, str, Callable[[str], Any]]] = {
    "outstanding": ("current balance, ageing and open bills", "get_outstanding",
                    lambda cid: c3.get_outstanding(cid)),
    "payment_history": ("how much/when they have paid, settle speed", "get_payment_history",
                        lambda cid: c3.get_payment_history(cid)),
    "sales_history": ("past invoices with line items (product, rate, qty)", "get_sales_history",
                      lambda cid: c3.get_sales_history(cid, limit=20)),
    "top_purchased_items": ("items ranked by quantity bought across ALL sales history — use for "
                             "'most/top/favourite item' questions", "get_top_purchased_items",
                             lambda cid: c3.top_purchased_items(cid)),
    "receipts": ("individual receipts and what each settled", "get_receipts",
                 lambda cid: c3.get_receipts(cid, limit=20)),
    "ledger": ("ledger postings with running balance", "get_customer_ledger",
               lambda cid: c3.get_customer_ledger(cid)),
    "timeline": ("recent activity in date order", "get_customer_timeline",
                 lambda cid: c3.get_customer_timeline(cid, limit=30)),
}

_PLAN_SYSTEM = (
    "You choose which read-only tools are needed to answer a customer's question "
    "about their own account. Return only names from the menu, at most three, and "
    "an empty list if none fit. Choose nothing you do not need."
)
_ANSWER_SYSTEM = (
    "You answer a customer's question using ONLY the JSON data provided. Quote "
    "figures, dates and references exactly as they appear in the data. If the data "
    "does not contain the answer, say you could not find it. Be concise and never "
    "invent a number, date or reference."
)


class _ToolPlan(ModelOutput):
    tools: list[str] = []


class _Answer(ModelOutput):
    text: str = ""


def _extras_on() -> bool:
    from . import llm

    return os.getenv("CA_SA1_TOOLS", "on").lower() != "off" and llm.available()


def _plan_tools(message: str) -> list[str] | None:
    """Tool names the model wants to run, or None when unavailable. Monkeypatched
    in tests so the path is exercised without a network."""
    if not _extras_on():
        return None
    from . import llm

    menu = "\n".join(f"- {name}: {desc}" for name, (desc, _, _) in TOOL_MENU.items())
    try:
        plan = llm.complete_structured(
            _ToolPlan, _PLAN_SYSTEM,
            f"Question: {message}\n\nTools:\n{menu}\n\nWhich tools are needed?",
            capability="classification", example={"tools": ["outstanding"]},
        )
    except llm.LLMUnavailable:
        return None
    chosen = [n for n in plan.tools if n in TOOL_MENU][:3]
    return chosen or None


def _compose_answer(message: str, data_json: str) -> str | None:
    if not _extras_on():
        return None
    from . import llm

    try:
        answer = llm.complete_structured(
            _Answer, _ANSWER_SYSTEM, f"Question: {message}\n\nData:\n{data_json}",
            capability="summarization",
        )
    except llm.LLMUnavailable:
        return None
    return answer.text or None


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _tool_fallback(cid: str, message: str, calls: list[ToolCall]) -> str | None:
    """Plan -> run -> compose, grounded. Returns a customer-ready answer or None.

    ponytail: the grounding check blocks a figure the tools never returned; it
    does not catch a real figure used in the wrong place. Add per-field checking
    if an eval shows the model misattributing values across tools.
    """
    names = _plan_tools(message)
    if not names:
        return None

    results: dict[str, Any] = {}
    for name in names:
        desc, tool, fn = TOOL_MENU[name]
        data = _read(calls, tool, lambda fn=fn: fn(cid), customer_id=cid)
        if data is not None:
            results[name] = _to_jsonable(data)
    if not results:
        return None

    blob = json.dumps(results, default=str)
    answer = _compose_answer(message, blob)
    return answer if answer and _grounded(blob, answer) else None


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

    if not sections:
        # No fixed handler answered — let the model pick a tool and answer from it.
        answer = _tool_fallback(cid, state.message, calls)
        if answer:
            return result("completed", answer, calls)

    if not sections and "unknown" in intents:
        sections.append(_HELP)

    if not sections:
        # Every read failed, or nothing SA-1 handles was asked. Say nothing and
        # let the orchestrator fall back rather than invent a reply.
        status = "needs_information" if any(not c.ok for c in calls) else "completed"
        return result(status, None, calls)

    return result(status, _phrase("\n\n".join(sections)), calls)
