"""Phase 1 — data-quality checks over the tenant book.

These are the checks named in Docs/02phasesWithEval.md. They report; they never
repair, because the tenant database is read-only and Tally is the source of
truth. A finding is a fact about the book that downstream agents must not trip
over — e.g. a customer whose mobile is shared with another customer cannot be
resolved by phone number alone.

    uv run python -m ca.data_quality          # full report
    uv run python -m ca.data_quality --json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .config import CUSTOMER_GROUP_PATH_RE, tenant_db

Severity = str  # "P0" money/identity correctness, "P1" behaviour, "P2" cosmetic


@dataclass
class Finding:
    check: str
    severity: Severity
    count: int
    detail: str = ""
    examples: list[Any] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.count == 0

    def __str__(self) -> str:
        mark = "ok  " if self.clean else "FAIL"
        line = f"[{mark}] {self.severity} {self.check}: {self.count}"
        if self.detail:
            line += f" — {self.detail}"
        if self.examples:
            line += f"\n         e.g. {self.examples[:3]}"
        return line


_DEBTORS = {"groupPath": {"$regex": CUSTOMER_GROUP_PATH_RE}}
_POSTING = {"flags.isCancelled": False, "flags.isDeleted": False, "flags.isOptional": False}


def _dupes(collection: str, key: str, match: dict[str, Any]) -> list[dict[str, Any]]:
    return list(
        tenant_db()[collection].aggregate(
            [
                {"$match": {**match, key: {"$nin": [None, ""]}}},
                {"$group": {"_id": f"${key}", "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}},
                {"$sort": {"n": -1}},
                {"$limit": 200},
            ],
            allowDiskUse=True,
        )
    )


def check_duplicate_customer_names() -> Finding:
    dupes = _dupes("ledgers", "ledgerName", _DEBTORS)
    return Finding(
        "duplicate_customer_name",
        "P0",
        len(dupes),
        "same ledgerName on more than one debtor — the voucher join key would be ambiguous",
        [d["_id"] for d in dupes],
    )


def check_duplicate_customer_mobiles() -> Finding:
    dupes = _dupes("ledgers", "partyDetails.mobile", _DEBTORS)
    return Finding(
        "duplicate_customer_mobile",
        "P1",
        len(dupes),
        "phone shared by several customers — resolve_customer must not accept it alone",
        [f"{d['_id']} x{d['n']}" for d in dupes],
    )


def check_missing_customer_identity() -> Finding:
    n = tenant_db()["ledgers"].count_documents(
        {**_DEBTORS, "$or": [{"ledgerName": {"$in": [None, ""]}}, {"companyId": None}]}
    )
    return Finding("missing_customer_identity", "P0", n, "debtor without a name or company")


def check_missing_voucher_number() -> Finding:
    n = tenant_db()["vouchers"].count_documents(
        {**_POSTING, "voucherNumber": {"$in": [None, ""]}}
    )
    return Finding(
        "missing_voucher_number", "P0", n, "posting voucher with no number — cannot be cited or settled"
    )


def check_duplicate_voucher_numbers() -> Finding:
    dupes = list(
        tenant_db()["vouchers"].aggregate(
            [
                {"$match": {**_POSTING, "voucherNumber": {"$nin": [None, ""]}}},
                {
                    "$group": {
                        "_id": {"n": "$voucherNumber", "t": "$voucherTypeName"},
                        "n": {"$sum": 1},
                    }
                },
                {"$match": {"n": {"$gt": 1}}},
                {"$limit": 200},
            ],
            allowDiskUse=True,
        )
    )
    return Finding(
        "duplicate_voucher_number",
        "P1",
        len(dupes),
        "same number twice within a voucher type — bill allocation by name becomes ambiguous",
        [f"{d['_id']['n']} ({d['_id']['t']}) x{d['n']}" for d in dupes],
    )


def check_invalid_dates() -> Finding:
    n = tenant_db()["vouchers"].count_documents({**_POSTING, "dates.date": None})
    return Finding("invalid_voucher_date", "P0", n, "posting voucher with no date — cannot be aged")


def check_zero_amount_vouchers() -> Finding:
    n = tenant_db()["vouchers"].count_documents(
        {**_POSTING, "ledgerEntries": {"$size": 0}}
    )
    return Finding("voucher_without_entries", "P1", n, "posting voucher with no ledger entries")


def check_unbalanced_vouchers(limit: int = 20000) -> Finding:
    """Every voucher's ledger entries must sum to zero (double entry)."""
    bad = list(
        tenant_db()["vouchers"].aggregate(
            [
                {"$match": _POSTING},
                {"$project": {"voucherNumber": 1, "total": {"$sum": "$ledgerEntries.amount"}}},
                {"$match": {"$expr": {"$gt": [{"$abs": "$total"}, 0.5]}}},
                {"$limit": limit},
            ],
            allowDiskUse=True,
        )
    )
    return Finding(
        "unbalanced_voucher",
        "P0",
        len(bad),
        "ledger entries do not net to zero",
        [f"{b.get('voucherNumber')} off by {round(b['total'], 2)}" for b in bad],
    )


def check_receipt_allocations() -> Finding:
    """`Agst Ref` allocations whose invoice number is not a sales voucher of the
    same party. Known to be non-zero here: bills opened before this book starts."""
    receipts = list(
        tenant_db()["vouchers"].find(
            {**_POSTING, "voucherCategory": "Receipt"},
            {"partyLedgerName": 1, "ledgerEntries.ledgerName": 1,
             "ledgerEntries.billAllocations": 1},
        )
    )
    sales: dict[tuple[str, str], int] = {}
    for v in tenant_db()["vouchers"].find(
        {**_POSTING, "voucherCategory": "Sales"},
        {"voucherNumber": 1, "partyLedgerName": 1, "ledgerEntries.ledgerName": 1},
    ):
        for entry in v.get("ledgerEntries") or []:
            name = entry.get("ledgerName")
            if name and v.get("voucherNumber"):
                sales[(name, v["voucherNumber"])] = 1

    total = unmatched = 0
    examples: list[str] = []
    for v in receipts:
        for entry in v.get("ledgerEntries") or []:
            ledger = entry.get("ledgerName")
            for bill in entry.get("billAllocations") or []:
                if bill.get("billType") != "Agst Ref" or not bill.get("name"):
                    continue
                total += 1
                if (ledger, bill["name"]) not in sales:
                    unmatched += 1
                    if len(examples) < 3:
                        examples.append(f"{bill['name']} for {ledger}")
    rate = f"{100 * (total - unmatched) / total:.1f}%" if total else "n/a"
    return Finding(
        "receipt_against_unknown_invoice",
        "P1",
        unmatched,
        f"{total} Agst Ref allocations, {rate} resolve to an in-book invoice of the same party; "
        "the rest are bills predating the book and are reported, never netted off",
        examples,
    )


def check_negative_open_bills() -> Finding:
    """A bill allocated more than it was invoiced would mean a wrong balance."""
    from .customer360 import allocations, fetch_vouchers, invoice_totals

    names = [
        d["ledgerName"]
        for d in tenant_db()["ledgers"]
        .find(_DEBTORS, {"ledgerName": 1})
        .sort("ledgerName", 1)
        .limit(25)
    ]
    bad: list[str] = []
    for name in names:
        vs = fetch_vouchers(name)
        alloc = allocations(vs)
        for number, (_, amount) in invoice_totals(vs).items():
            if alloc.against_bill.get(number, 0.0) - amount > 1.0:
                bad.append(f"{number} ({name})")
    return Finding(
        "over_allocated_bill",
        "P0",
        len(bad),
        f"invoice paid beyond its value, sampled over {len(names)} debtors",
        bad,
    )


CHECKS: list[Callable[[], Finding]] = [
    check_duplicate_customer_names,
    check_duplicate_customer_mobiles,
    check_missing_customer_identity,
    check_missing_voucher_number,
    check_duplicate_voucher_numbers,
    check_invalid_dates,
    check_zero_amount_vouchers,
    check_unbalanced_vouchers,
    check_receipt_allocations,
    check_negative_open_bills,
]


def run_all() -> list[Finding]:
    return [check() for check in CHECKS]


def main(argv: list[str]) -> int:
    findings = run_all()
    if "--json" in argv:
        print(json.dumps([f.__dict__ for f in findings], indent=2, default=str))
    else:
        for f in findings:
            print(f)
    # P0 findings fail the gate; P1/P2 are reported and tracked.
    return 1 if any(f.severity == "P0" and not f.clean for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
