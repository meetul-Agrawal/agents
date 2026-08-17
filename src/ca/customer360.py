"""Phase 1 — data backbone and Customer 360.

The money rules, established by measuring this book rather than by assuming
Tally conventions:

* A customer is a `ledgers` document under `Sundry Debtors`.
* Vouchers reference it by `ledgerName` / `partyLedgerName` only — `ledgerId`
  is null everywhere — so the name is the join key.
* On the party's own ledger line, **sales post negative and receipts positive**.
  What the customer owes is therefore the *negation* of that amount.
* Only receipts carry `billAllocations`; `billType == "Agst Ref"` with `name`
  holding the sales voucher number is what links a payment to an invoice.
* Cancelled and optional vouchers do not post and are excluded.
* This book has no credit notes, no orders, no due dates and no credit limits.
  Those services return empty and say so via `capabilities()` — they never
  guess.

Everything here is deterministic. No LLM touches these numbers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .config import CUSTOMER_GROUP_PATH_RE, app_db, tenant_db
from .contracts import (
    Customer,
    Customer360,
    DataCapability,
    LedgerLine,
    OpenBill,
    Outstanding,
    PaymentBehaviour,
    SalesHistoryQuery,
    TimelineEvent,
    utcnow,
)
from .llm import LLMUnavailable, complete_structured

# Vouchers that do not post to the books.
_POSTING = {"flags.isCancelled": False, "flags.isDeleted": False, "flags.isOptional": False}

AGEING_BUCKETS = ("0-30", "31-60", "61-90", "90+")


class CustomerNotFoundError(LookupError):
    pass


class AmbiguousCustomerError(LookupError):
    """More than one customer matches. Never guess — the orchestrator must ask."""

    def __init__(self, query: str, matches: list[Customer]):
        super().__init__(f"{query!r} matches {len(matches)} customers")
        self.matches = matches


def capabilities() -> DataCapability:
    return DataCapability(
        note=(
            "This book contains only Sales and Receipt vouchers. Credit notes, "
            "orders, due dates and credit limits do not exist in it; ageing is "
            "measured from the invoice date, not from a due date."
        )
    )


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------


def _to_customer(doc: dict[str, Any]) -> Customer:
    party = doc.get("partyDetails") or {}
    opening = ((doc.get("balances") or {}).get("openingBalance") or {}).get("amount") or 0.0
    return Customer(
        customer_id=str(doc["_id"]),
        ledger_name=doc["ledgerName"],
        company_id=str(doc.get("companyId", "")),
        display_name=doc["ledgerName"],
        ledger_code=doc.get("ledgerCode"),
        group_path=doc.get("groupPath"),
        email=party.get("email") or None,
        mobile=party.get("mobile") or None,
        gstin=party.get("gstin") or None,
        state=party.get("gstState") or None,
        # Stored negative when the customer owes; expose it the way we report money.
        opening_balance=-float(opening),
    )


def _debtor_filter(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    q: dict[str, Any] = {"groupPath": {"$regex": CUSTOMER_GROUP_PATH_RE}}
    if extra:
        q.update(extra)
    return q


def get_customer(customer_id: str) -> Customer:
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        oid: Any = ObjectId(customer_id)
    except (InvalidId, TypeError):
        oid = customer_id
    doc = tenant_db()["ledgers"].find_one({"_id": oid})
    if not doc:
        raise CustomerNotFoundError(customer_id)
    return _to_customer(doc)


def resolve_customer(query: str, *, limit: int = 10) -> Customer:
    """Find exactly one customer by ledger id, exact name, mobile, email or a
    name fragment. Raises rather than guessing when several match."""
    matches = find_customers(query, limit=limit)
    if not matches:
        raise CustomerNotFoundError(query)
    if len(matches) > 1:
        raise AmbiguousCustomerError(query, matches)
    return matches[0]


def find_customers(query: str, *, limit: int = 10) -> list[Customer]:
    """Ranked candidates: exact identifiers first, then a name fragment.

    ponytail: staged exact-then-substring lookup, no fuzzy scoring. Add
    rapidfuzz only if real inbound names start missing.
    """
    query = (query or "").strip()
    if not query:
        return []
    ledgers = tenant_db()["ledgers"]

    try:
        from bson import ObjectId

        doc = ledgers.find_one({"_id": ObjectId(query)})
        if doc:
            return [_to_customer(doc)]
    except Exception:
        pass

    digits = re.sub(r"\D", "", query)
    stages: list[dict[str, Any]] = [
        {"ledgerName": query},
        {"ledgerCode": query},
        {"partyDetails.gstin": query.upper()},
    ]
    if "@" in query:
        stages.append({"partyDetails.email": query})
    if len(digits) >= 10:
        stages.append({"partyDetails.mobile": {"$regex": f"{digits[-10:]}$"}})
    stages.append({"ledgerName": {"$regex": re.escape(query), "$options": "i"}})

    for stage in stages:
        found = list(ledgers.find(_debtor_filter(stage)).limit(limit + 1))
        if found:
            return [_to_customer(d) for d in found[:limit]]
    return []


# --------------------------------------------------------------------------
# Voucher access — one scan per customer, everything else computed in memory
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VoucherSet:
    """All posting vouchers touching one customer's ledger."""

    ledger_name: str
    vouchers: list[dict[str, Any]]

    def by_category(self, category: str) -> list[dict[str, Any]]:
        return [v for v in self.vouchers if v.get("voucherCategory") == category]

    @property
    def sales(self) -> list[dict[str, Any]]:
        return self.by_category("Sales")

    @property
    def receipts(self) -> list[dict[str, Any]]:
        return self.by_category("Receipt")


def fetch_vouchers(ledger_name: str) -> VoucherSet:
    """ponytail: `vouchers` has no index beyond _id, so this is a ~280ms
    collection scan per customer. Fine for one conversation; add a
    {'ledgerEntries.ledgerName': 1} index if batch jobs ever fan out over
    thousands of customers.
    """
    query = {
        **_POSTING,
        "$or": [
            {"partyLedgerName": ledger_name},
            {"ledgerEntries.ledgerName": ledger_name},
        ],
    }
    docs = list(tenant_db()["vouchers"].find(query))
    docs.sort(key=lambda v: (_vdate(v) or date.min, v.get("voucherNumber") or ""))
    return VoucherSet(ledger_name, docs)


def _vdate(voucher: dict[str, Any]) -> date | None:
    raw = (voucher.get("dates") or {}).get("date")
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _party_entries(voucher: dict[str, Any], ledger_name: str) -> list[dict[str, Any]]:
    return [e for e in voucher.get("ledgerEntries") or [] if e.get("ledgerName") == ledger_name]


def party_amount(voucher: dict[str, Any], ledger_name: str) -> float:
    """What the customer owes because of this voucher: positive for a sale,
    negative for a receipt. (The stored sign is the other way round.)"""
    return -sum(float(e.get("amount") or 0) for e in _party_entries(voucher, ledger_name))


# --------------------------------------------------------------------------
# Bill-level outstanding
# --------------------------------------------------------------------------


def invoice_totals(vs: VoucherSet) -> dict[str, tuple[date | None, float]]:
    """voucher_number -> (invoice date, amount owed). Duplicated numbers are
    summed, which is what the ledger does."""
    out: dict[str, tuple[date | None, float]] = {}
    for v in vs.sales:
        number = v.get("voucherNumber")
        if not number:
            continue
        prior_date, prior_amount = out.get(number, (None, 0.0))
        out[number] = (
            prior_date or _vdate(v),
            prior_amount + party_amount(v, vs.ledger_name),
        )
    return out


@dataclass
class Allocations:
    against_bill: dict[str, float]
    on_account: float = 0.0
    advance: float = 0.0
    new_ref: float = 0.0
    total_receipted: float = 0.0


def allocations(vs: VoucherSet) -> Allocations:
    """Split every receipt into what it settles.

    `New Ref` is a bill opened by the receipt itself (an advance against a
    future invoice), so it is not an allocation against an existing bill.
    """
    result = Allocations(against_bill=defaultdict(float))
    for v in vs.receipts:
        result.total_receipted += -party_amount(v, vs.ledger_name)
        for entry in _party_entries(v, vs.ledger_name):
            for bill in entry.get("billAllocations") or []:
                amount = float(bill.get("amount") or 0)
                bill_type = bill.get("billType")
                name = bill.get("name")
                if bill_type == "Agst Ref" and name:
                    result.against_bill[name] += amount
                elif bill_type == "Advance":
                    result.advance += amount
                elif bill_type == "New Ref":
                    result.new_ref += amount
                elif bill_type == "On Account":
                    result.on_account += amount
    result.against_bill = dict(result.against_bill)
    return result


def _bucket(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 60:
        return "31-60"
    if age_days <= 90:
        return "61-90"
    return "90+"


def compute_outstanding(
    customer: Customer, vs: VoucherSet, *, as_of: date | None = None
) -> Outstanding:
    as_of = as_of or utcnow().date()
    invoices = invoice_totals(vs)
    alloc = allocations(vs)

    open_bills: list[OpenBill] = []
    allocated_total = 0.0
    for number, (invoice_date, amount) in invoices.items():
        paid = alloc.against_bill.get(number, 0.0)
        allocated_total += paid
        remaining = round(amount - paid, 2)
        if remaining <= 0.01:
            continue
        invoice_date = invoice_date or as_of
        age = (as_of - invoice_date).days
        open_bills.append(
            OpenBill(
                voucher_number=number,
                invoice_date=invoice_date,
                invoice_amount=round(amount, 2),
                allocated=round(paid, 2),
                outstanding=remaining,
                age_days=age,
                bucket=_bucket(age),
            )
        )
    open_bills.sort(key=lambda b: b.invoice_date)

    ageing = {b: 0.0 for b in AGEING_BUCKETS}
    for bill in open_bills:
        ageing[bill.bucket] = round(ageing[bill.bucket] + bill.outstanding, 2)

    # Receipts pointing at invoices that predate this book. Reported, not netted.
    pre_book = round(
        sum(v for k, v in alloc.against_bill.items() if k not in invoices), 2
    )

    invoiced_total = round(sum(a for _, a in invoices.values()), 2)
    # The true running balance: what they owe once every receipt is subtracted,
    # whether it was booked Against Ref, On Account, as an advance, or against a
    # bill that predates this book. This is the ledger closing balance.
    net_balance = round(customer.opening_balance + invoiced_total - alloc.total_receipted, 2)

    return Outstanding(
        customer_id=customer.customer_id,
        ledger_name=customer.ledger_name,
        as_of=as_of,
        outstanding=round(sum(b.outstanding for b in open_bills), 2),
        net_balance=net_balance,
        open_bill_count=len(open_bills),
        invoiced_total=invoiced_total,
        receipted_total=round(alloc.total_receipted, 2),
        allocated_total=round(allocated_total, 2),
        pre_book_settlements=pre_book,
        on_account=round(alloc.on_account, 2),
        advance=round(alloc.advance + alloc.new_ref, 2),
        opening_balance=customer.opening_balance,
        ageing=ageing,
        open_bills=open_bills,
    )


def get_outstanding(customer_id: str, *, as_of: date | None = None) -> Outstanding:
    customer = get_customer(customer_id)
    return compute_outstanding(customer, fetch_vouchers(customer.ledger_name), as_of=as_of)


# --------------------------------------------------------------------------
# Ledger, history, behaviour
# --------------------------------------------------------------------------


def build_ledger(vs: VoucherSet, *, opening_balance: float = 0.0) -> list[LedgerLine]:
    lines: list[LedgerLine] = []
    balance = opening_balance
    for v in vs.vouchers:
        amount = party_amount(v, vs.ledger_name)
        balance += amount
        category = v.get("voucherCategory")
        against = [
            b.get("name")
            for e in _party_entries(v, vs.ledger_name)
            for b in e.get("billAllocations") or []
            if b.get("billType") == "Agst Ref" and b.get("name")
        ]
        lines.append(
            LedgerLine(
                date=_vdate(v) or date.min,
                voucher_number=v.get("voucherNumber") or "(unnumbered)",
                category=category if category in ("Sales", "Receipt") else "Other",
                debit=round(amount, 2) if amount > 0 else 0.0,
                credit=round(-amount, 2) if amount < 0 else 0.0,
                balance=round(balance, 2),
                against_bills=against,
            )
        )
    return lines


def get_customer_ledger(customer_id: str) -> list[LedgerLine]:
    customer = get_customer(customer_id)
    return build_ledger(
        fetch_vouchers(customer.ledger_name), opening_balance=customer.opening_balance
    )


def _voucher_summary(v: dict[str, Any], ledger_name: str) -> dict[str, Any]:
    return {
        "voucher_number": v.get("voucherNumber"),
        "date": _vdate(v),
        "voucher_type": v.get("voucherTypeName"),
        "amount": round(abs(party_amount(v, ledger_name)), 2),
        "items": [
            {
                "name": i.get("stockItemName"),
                "qty": i.get("billedQty"),
                "rate": i.get("rate"),
                "amount": i.get("amount"),
            }
            for i in v.get("inventoryEntries") or []
        ],
    }


def get_sales_history(customer_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    customer = get_customer(customer_id)
    vs = fetch_vouchers(customer.ledger_name)
    rows = [_voucher_summary(v, vs.ledger_name) for v in vs.sales]
    rows.reverse()  # newest first
    return rows[:limit] if limit else rows


SALES_HISTORY_SYSTEM_PROMPT = (
    "You parse customer enquiries about their sales and purchase history for a "
    "B2B receivables and sales desk.\n\n"
    "### Extraction Rules:\n"
    "1. item_query: Product or SKU name (e.g. 'Sattu Aata', 'Khaman Mix', 'Poha'). "
    "Omit non-product words like 'price', 'rate', 'latest', 'bill', 'order'.\n"
    "2. voucher_number: Exact invoice or bill number if referenced (e.g. 'Blk/RD/26-27/149').\n"
    "3. metric: 'rate' (asking for price/rate), 'quantity' (asking for units/volume), "
    "'invoices' (asking for bills/invoice list), or 'all' (general history).\n"
    "4. period: 'all_time', 'last_30_days', 'last_3_months', 'last_6_months', 'this_month', 'last_month', "
    "'this_year', 'last_year', or financial year (e.g. 'fy_25_26', 'fy_26_27').\n"
    "5. limit: Number of orders/invoices requested (e.g. 1 for 'latest/last rate', 3 for 'last 3 invoices', 5 for 'last 5'). Omit if not specified.\n"
    "6. start_date / end_date: Specific YYYY-MM-DD dates if an explicit date range was stated."
)


def parse_sales_history_query(
    message: str,
    history: str = "",
    reference_date: date | None = None,
) -> SalesHistoryQuery:
    today = reference_date or utcnow().date()
    parts = []
    if history.strip():
        parts.append(
            "<recent_conversation_history>\n"
            f"{history.strip()}\n"
            "</recent_conversation_history>\n"
        )
    parts.append(
        f"Today's date is: {today.isoformat()}\n\n"
        f"<customer_inbound_message>\n{message}\n</customer_inbound_message>\n\n"
        "Extract the sales history query parameters."
    )
    try:
        return complete_structured(
            SalesHistoryQuery,
            SALES_HISTORY_SYSTEM_PROMPT,
            "\n".join(parts),
            capability="structured_completion",
            example={
                "item_query": "Sattu Aata 500gm",
                "voucher_number": None,
                "start_date": None,
                "end_date": None,
                "period": "last_3_months",
                "limit": 3,
                "metric": "rate",
            },
        )
    except Exception:
        return SalesHistoryQuery()


def _resolve_date_range(
    period: str | None,
    start: date | None,
    end: date | None,
    reference_date: date | None = None,
) -> tuple[date | None, date | None]:
    ref = reference_date or utcnow().date()
    if start or end:
        return start, end
    p = (period or "all_time").lower().strip()
    if p in ("all_time", "all", ""):
        return None, None
    if p in ("last_30_days", "last_month", "past_month"):
        return ref - timedelta(days=30), ref
    if p in ("last_3_months", "last_quarter", "past_3_months"):
        return ref - timedelta(days=90), ref
    if p in ("last_6_months", "past_6_months"):
        return ref - timedelta(days=180), ref
    if p in ("last_year", "past_year", "last_12_months"):
        return ref - timedelta(days=365), ref
    if p == "this_month":
        return date(ref.year, ref.month, 1), ref
    if p == "this_year":
        return date(ref.year, 1, 1), ref
    fy_match = re.search(r"(\d{2,4})[-_](\d{2,4})", p)
    if fy_match:
        y1 = int(fy_match.group(1))
        if y1 < 100:
            y1 += 2000
        return date(y1, 4, 1), date(y1 + 1, 3, 31)
    return None, None


def _sig_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= 2}


def query_sales_history(
    customer_id: str,
    query: SalesHistoryQuery | None = None,
    *,
    reference_date: date | None = None,
) -> dict[str, Any]:
    """Execute a structured sales history query against the customer's sales vouchers."""
    q = query or SalesHistoryQuery()
    customer = get_customer(customer_id)
    vs = fetch_vouchers(customer.ledger_name)
    all_rows = [_voucher_summary(v, vs.ledger_name) for v in vs.sales]
    all_rows.reverse()  # newest first

    start_d, end_d = _resolve_date_range(q.period, q.start_date, q.end_date, reference_date)

    # 1. Date filter
    filtered_rows = []
    for r in all_rows:
        vdate = r.get("date")
        if vdate:
            if start_d and vdate < start_d:
                continue
            if end_d and vdate > end_d:
                continue
        filtered_rows.append(r)

    # 2. Voucher number filter if specified
    if q.voucher_number:
        v_upper = q.voucher_number.upper()
        filtered_rows = [r for r in filtered_rows if (r.get("voucher_number") or "").upper() == v_upper]

    # 3. Item filtering & matching if specified
    if q.item_query and q.item_query.strip():
        want = _sig_tokens(q.item_query)
        all_item_names = set(it["name"] for r in all_rows for it in (r.get("items") or []) if it.get("name"))
        scored = [(len(_sig_tokens(name) & want), name) for name in all_item_names]
        scored = [(s, name) for s, name in scored if s > 0]
        if not scored:
            return {
                "customer_id": customer_id,
                "customer_name": customer.display_name,
                "found": False,
                "item_matched": None,
                "message": f"Could not find any purchases matching '{q.item_query}'.",
                "records": [],
            }

        best_score = max(s for s, _ in scored)
        best_matches = [name for s, name in scored if s == best_score]
        matched_item = best_matches[0]

        item_lines = []
        for r in filtered_rows:
            for it in (r.get("items") or []):
                if it.get("name") == matched_item:
                    item_lines.append({
                        "voucher_number": r.get("voucher_number"),
                        "date": r.get("date"),
                        "qty": it.get("qty"),
                        "rate": it.get("rate"),
                        "amount": it.get("amount"),
                    })

        limited_lines = item_lines[:q.limit] if q.limit else item_lines
        latest = item_lines[0] if item_lines else None
        return {
            "customer_id": customer_id,
            "customer_name": customer.display_name,
            "found": True,
            "query_metric": q.metric,
            "item_matched": matched_item,
            "total_purchases": len(item_lines),
            "latest_rate": latest.get("rate") if latest else None,
            "latest_date": latest.get("date") if latest else None,
            "latest_voucher": latest.get("voucher_number") if latest else None,
            "records": limited_lines,
        }

    # 4. General invoice list
    limited_rows = filtered_rows[:q.limit] if q.limit else filtered_rows
    total_amount = round(sum(r.get("amount") or 0.0 for r in filtered_rows), 2)
    return {
        "customer_id": customer_id,
        "customer_name": customer.display_name,
        "found": True,
        "query_metric": q.metric,
        "total_invoices": len(filtered_rows),
        "total_invoiced_amount": total_amount,
        "start_date": start_d,
        "end_date": end_d,
        "period": q.period,
        "records": limited_rows,
    }


def get_receipts(customer_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    customer = get_customer(customer_id)
    vs = fetch_vouchers(customer.ledger_name)
    rows = []
    for v in reversed(vs.receipts):
        row = _voucher_summary(v, vs.ledger_name)
        row["against_bills"] = [
            {"bill": b.get("name"), "type": b.get("billType"), "amount": b.get("amount")}
            for e in _party_entries(v, vs.ledger_name)
            for b in e.get("billAllocations") or []
            if b.get("billType")
        ]
        rows.append(row)
    return rows[:limit] if limit else rows


def get_credit_notes(customer_id: str) -> list[dict[str, Any]]:
    """Empty by construction: this book has no credit-note vouchers."""
    return []


def get_open_orders(customer_id: str) -> list[dict[str, Any]]:
    """Empty by construction: this book has no order vouchers."""
    return []


def payment_behaviour(vs: VoucherSet, *, as_of: date | None = None) -> PaymentBehaviour:
    """Settlement speed measured invoice date -> settling receipt date."""
    invoices = invoice_totals(vs)
    receipts = vs.receipts
    lags: list[int] = []
    settled: set[str] = set()
    dates: list[date] = []
    total = 0.0

    for v in receipts:
        rdate = _vdate(v)
        total += -party_amount(v, vs.ledger_name)
        if rdate:
            dates.append(rdate)
        for entry in _party_entries(v, vs.ledger_name):
            for bill in entry.get("billAllocations") or []:
                name = bill.get("name")
                if bill.get("billType") != "Agst Ref" or name not in invoices:
                    continue
                settled.add(name)
                idate = invoices[name][0]
                if idate and rdate:
                    lags.append((rdate - idate).days)

    return PaymentBehaviour(
        receipt_count=len(receipts),
        total_received=round(total, 2),
        first_receipt=min(dates) if dates else None,
        last_receipt=max(dates) if dates else None,
        avg_days_to_settle=round(sum(lags) / len(lags), 1) if lags else None,
        settled_bill_count=len(settled),
    )


def get_payment_history(customer_id: str) -> PaymentBehaviour:
    customer = get_customer(customer_id)
    return payment_behaviour(fetch_vouchers(customer.ledger_name))


# --------------------------------------------------------------------------
# App-database reads (conversations, cases, promises, health, events)
# --------------------------------------------------------------------------


def _app_find(collection: str, customer_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    cursor = (
        app_db()[collection]
        .find({"customer_id": customer_id}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    return list(cursor)


def get_disputes(customer_id: str) -> list[dict[str, Any]]:
    return _app_find("cases", customer_id)


def get_approvals(customer_id: str) -> list[dict[str, Any]]:
    return _app_find("approvals", customer_id)


def get_payment_promises(customer_id: str) -> list[dict[str, Any]]:
    return _app_find("payment_promises", customer_id)


def get_customer_health(customer_id: str) -> dict[str, Any] | None:
    docs = list(
        app_db()["health_scores"]
        .find({"customer_id": customer_id}, {"_id": 0})
        .sort("computed_at", -1)
        .limit(1)
    )
    return docs[0] if docs else None


def get_conversation_history(customer_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    cursor = (
        app_db()["messages"]
        .find({"customer_id": customer_id}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return list(cursor)


def get_events(customer_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    cursor = (
        app_db()["events"]
        .find({"customer_id": customer_id}, {"_id": 0})
        .sort("timestamp", -1)
        .limit(limit)
    )
    return list(cursor)


# --------------------------------------------------------------------------
# Timeline and Customer 360
# --------------------------------------------------------------------------


def build_timeline(
    customer: Customer, vs: VoucherSet, extra: Iterable[dict[str, Any]] = ()
) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for v in vs.vouchers:
        when = _vdate(v)
        if not when:
            continue
        category = v.get("voucherCategory")
        amount = abs(party_amount(v, vs.ledger_name))
        kind = "invoice" if category == "Sales" else "receipt" if category == "Receipt" else "voucher"
        events.append(
            TimelineEvent(
                customer_id=customer.customer_id,
                at=datetime(when.year, when.month, when.day, tzinfo=timezone.utc),
                kind=kind,
                title=f"{kind.title()} {v.get('voucherNumber')} — {amount:,.2f}",
                ref=v.get("voucherNumber"),
                payload={"amount": round(amount, 2), "voucher_type": v.get("voucherTypeName")},
            )
        )
    for doc in extra:
        ts = doc.get("timestamp") or doc.get("created_at")
        if not isinstance(ts, datetime):
            continue
        events.append(
            TimelineEvent(
                customer_id=customer.customer_id,
                at=ts,
                kind=str(doc.get("kind") or doc.get("type") or "event"),
                title=str(doc.get("title") or doc.get("type") or "event"),
                ref=doc.get("event_id") or doc.get("case_id"),
                payload=doc.get("payload") or {},
            )
        )
    events.sort(key=lambda e: e.at, reverse=True)
    return events


def get_customer_timeline(customer_id: str, *, limit: int = 100) -> list[TimelineEvent]:
    customer = get_customer(customer_id)
    vs = fetch_vouchers(customer.ledger_name)
    return build_timeline(customer, vs, get_events(customer_id))[:limit]


def build_customer_360(customer_id: str, *, as_of: date | None = None) -> Customer360:
    """One voucher scan, every section derived from it."""
    customer = get_customer(customer_id)
    vs = fetch_vouchers(customer.ledger_name)
    outstanding = compute_outstanding(customer, vs, as_of=as_of)
    behaviour = payment_behaviour(vs)
    health = get_customer_health(customer_id)

    return Customer360(
        customer=customer,
        financial={
            "outstanding": outstanding.outstanding,
            "open_bill_count": outstanding.open_bill_count,
            "ageing": outstanding.ageing,
            "oldest_open_bill_days": max((b.age_days for b in outstanding.open_bills), default=0),
            "opening_balance": customer.opening_balance,
            "pre_book_settlements": outstanding.pre_book_settlements,
            "on_account": outstanding.on_account,
            "advance": outstanding.advance,
            "payment_behaviour": behaviour.model_dump(mode="json"),
            "detail": outstanding.model_dump(mode="json"),
        },
        commercial={
            "invoice_count": len(vs.sales),
            "invoiced_total": outstanding.invoiced_total,
            "receipt_count": len(vs.receipts),
            "receipted_total": outstanding.receipted_total,
            "last_invoice_date": max(
                (d for d, _ in invoice_totals(vs).values() if d), default=None
            ),
            "credit_notes": [],
            "orders": [],
            "capabilities": capabilities().model_dump(),
        },
        communication={
            "messages": get_conversation_history(customer_id, limit=20),
        },
        relationship={"health": health},
        operational={
            "cases": get_disputes(customer_id),
            "approvals": get_approvals(customer_id),
            "promises": get_payment_promises(customer_id),
            "events": get_events(customer_id, limit=20),
        },
        agent_state={},
    )
