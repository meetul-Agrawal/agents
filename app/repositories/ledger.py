"""CustomerRepository — wraps the ledgers collection (Tally party masters)."""

from __future__ import annotations
from bson import ObjectId
from app.db.mongodb import get_db
from app.models.schemas import CustomerDoc, LedgerBalance


class CustomerRepository:
    @property
    def col(self):
        return get_db()["ledgers"]

    async def find_by_guid(self, ledger_guid: str) -> dict | None:
        return await self.col.find_one({"ledgerGuid": ledger_guid}, {"_id": 0})

    async def find_by_name(self, ledger_name: str) -> dict | None:
        return await self.col.find_one(
            {"ledgerName": {"$regex": f"^{ledger_name}$", "$options": "i"}},
            {"_id": 0},
        )

    async def search_by_name(self, query: str, limit: int = 10) -> list[dict]:
        cursor = self.col.find(
            {"ledgerName": {"$regex": query, "$options": "i"}},
            {"_id": 0, "ledgerGuid": 1, "ledgerName": 1, "partyDetails": 1, "balances": 1},
        ).limit(limit)
        return await cursor.to_list(limit)

    async def to_customer_doc(self, raw: dict) -> CustomerDoc:
        ob = raw.get("balances", {}).get("openingBalance", {})
        return CustomerDoc(
            ledger_guid=raw["ledgerGuid"],
            ledger_name=raw["ledgerName"],
            group_name=raw.get("groupName"),
            group_path=raw.get("groupPath"),
            mobile=raw.get("partyDetails", {}).get("mobile"),
            email=raw.get("partyDetails", {}).get("email"),
            opening_balance=ob.get("amount", 0.0) if ob else 0.0,
            balance_type=ob.get("type", "DEBIT") if ob else "DEBIT",
        )
