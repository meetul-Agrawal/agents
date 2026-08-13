"""SalesRepository + ReceiptRepository — both live in the vouchers collection."""

from __future__ import annotations
from app.db.mongodb import get_db
from app.models.schemas import SalesDoc, ReceiptDoc, ReceiptAllocation


def _projection(*extra: str) -> dict:
    base = {
        "_id": 0, "voucherNumber": 1, "voucherCategory": 1, "partyLedgerName": 1,
        "dates": 1, "ledgerEntries": 1, "flags": 1, "narration": 1, "reference": 1,
    }
    for f in extra:
        base[f] = 1
    return base


def _parse_receipt(raw: dict) -> ReceiptDoc:
    allocs: list[ReceiptAllocation] = []
    for entry in raw.get("ledgerEntries", []):
        for ba in entry.get("billAllocations", []):
            allocs.append(ReceiptAllocation(
                bill_name=ba.get("name"),
                bill_type=ba.get("billType"),
                amount=ba.get("amount", 0.0),
                bill_date=str(ba["billDate"]) if ba.get("billDate") else None,
            ))
    total = sum(abs(e.get("amount", 0)) for e in raw.get("ledgerEntries", [])
                if e.get("isDeemedPositive") is True) or sum(
        abs(e.get("amount", 0)) for e in raw.get("ledgerEntries", []))
    return ReceiptDoc(
        voucher_number=raw.get("voucherNumber", ""),
        party_ledger_name=raw.get("partyLedgerName", ""),
        date=str(raw["dates"]["date"]) if raw.get("dates", {}).get("date") else None,
        total_amount=total / len(raw["ledgerEntries"]) if raw.get("ledgerEntries") else 0,
        allocations=allocs,
        is_cancelled=raw.get("flags", {}).get("isCancelled", False),
    )


def _parse_sales(raw: dict) -> SalesDoc:
    allocs: list[ReceiptAllocation] = []
    for entry in raw.get("ledgerEntries", []):
        for ba in entry.get("billAllocations", []):
            allocs.append(ReceiptAllocation(
                bill_name=ba.get("name"),
                bill_type=ba.get("billType"),
                amount=ba.get("amount", 0.0),
                bill_date=str(ba["billDate"]) if ba.get("billDate") else None,
            ))
    total = sum(abs(e.get("amount", 0)) for e in raw.get("ledgerEntries", []))
    return SalesDoc(
        voucher_number=raw.get("voucherNumber", ""),
        party_ledger_name=raw.get("partyLedgerName", ""),
        date=str(raw["dates"]["date"]) if raw.get("dates", {}).get("date") else None,
        total_amount=total,
        narration=raw.get("narration"),
        is_cancelled=raw.get("flags", {}).get("isCancelled", False),
        bill_allocations=allocs,
    )


class SalesRepository:
    @property
    def col(self):
        return get_db()["vouchers"]

    def _base_filter(self, customer_name: str) -> dict:
        return {
            "partyLedgerName": customer_name,
            "voucherCategory": "Sales",
            "flags.isDeleted": {"$ne": True},
        }

    async def get_invoices(self, customer_name: str, limit: int = 50, skip: int = 0) -> list[SalesDoc]:
        cursor = self.col.find(
            self._base_filter(customer_name), _projection()
        ).sort("dates.date", -1).skip(skip).limit(limit)
        return [_parse_sales(d) async for d in cursor]

    async def get_invoice_by_number(self, customer_name: str, voucher_number: str) -> SalesDoc | None:
        raw = await self.col.find_one(
            {**self._base_filter(customer_name), "voucherNumber": voucher_number},
            _projection(),
        )
        return _parse_sales(raw) if raw else None

    async def search_invoices(self, customer_name: str, query: str, limit: int = 20) -> list[SalesDoc]:
        cursor = self.col.find(
            {**self._base_filter(customer_name), "voucherNumber": {"$regex": query, "$options": "i"}},
            _projection(),
        ).limit(limit)
        return [_parse_sales(d) async for d in cursor]


class ReceiptRepository:
    @property
    def col(self):
        return get_db()["vouchers"]

    def _base_filter(self, customer_name: str) -> dict:
        return {
            "partyLedgerName": customer_name,
            "voucherCategory": "Receipt",
            "flags.isDeleted": {"$ne": True},
        }

    async def get_receipts(self, customer_name: str, limit: int = 50) -> list[ReceiptDoc]:
        cursor = self.col.find(
            self._base_filter(customer_name), _projection()
        ).sort("dates.date", -1).limit(limit)
        return [_parse_receipt(d) async for d in cursor]

    async def get_receipts_for_invoice(self, customer_name: str, invoice_number: str) -> list[ReceiptDoc]:
        """Receipts with Agst Ref pointing to this invoice's bill name."""
        receipts = await self.get_receipts(customer_name, limit=500)
        return [
            r for r in receipts
            if any(a.bill_name == invoice_number and a.bill_type == "Agst Ref"
                   for a in r.allocations)
        ]

    async def get_on_account_receipts(self, customer_name: str, limit: int = 50) -> list[ReceiptDoc]:
        """Receipts with New Ref or Advance allocations (not tied to a specific invoice)."""
        receipts = await self.get_receipts(customer_name, limit=500)
        return [r for r in receipts if r.on_account_allocations]

    async def get_receipt_by_number(self, customer_name: str, voucher_number: str) -> ReceiptDoc | None:
        raw = await self.col.find_one(
            {**self._base_filter(customer_name), "voucherNumber": voucher_number},
            _projection(),
        )
        return _parse_receipt(raw) if raw else None
