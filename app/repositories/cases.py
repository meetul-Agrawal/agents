"""CaseRepository — manages the cases collection (created by this agent)."""

from __future__ import annotations
import uuid
from datetime import datetime
from app.db.mongodb import get_db
from app.models.schemas import Case


class CaseRepository:
    @property
    def col(self):
        return get_db()["cases"]

    async def create(self, customer_id: str, case_type: str, subject: str,
                     description: str, related_entities: dict,
                     priority: str = "NORMAL") -> Case:
        case = Case(
            case_id=f"CASE-{uuid.uuid4().hex[:8].upper()}",
            customer_id=customer_id,
            case_type=case_type,
            subject=subject,
            description=description,
            priority=priority,
            related_entities=related_entities,
        )
        await self.col.insert_one(case.model_dump())
        return case

    async def find_by_id(self, case_id: str) -> Case | None:
        raw = await self.col.find_one({"case_id": case_id}, {"_id": 0})
        return Case(**raw) if raw else None

    async def find_by_customer(self, customer_id: str, status: str | None = None,
                               limit: int = 20) -> list[Case]:
        q: dict = {"customer_id": customer_id}
        if status:
            q["status"] = status
        cursor = self.col.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        return [Case(**d) async for d in cursor]

    async def search(self, customer_id: str, query: str | None = None,
                     status: str | None = None, limit: int = 20) -> list[Case]:
        q: dict = {"customer_id": customer_id}
        if status:
            q["status"] = status
        if query:
            q["$or"] = [
                {"subject": {"$regex": query, "$options": "i"}},
                {"description": {"$regex": query, "$options": "i"}},
                {"case_id": {"$regex": query, "$options": "i"}},
            ]
        cursor = self.col.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        return [Case(**d) async for d in cursor]

    async def update(self, case_id: str, customer_id: str, updates: dict) -> bool:
        updates["updated_at"] = datetime.utcnow()
        result = await self.col.update_one(
            {"case_id": case_id, "customer_id": customer_id},
            {"$set": updates},
        )
        return result.modified_count > 0

    async def add_note(self, case_id: str, customer_id: str, note: str, author: str = "agent") -> bool:
        result = await self.col.update_one(
            {"case_id": case_id, "customer_id": customer_id},
            {
                "$push": {"notes": {"note": note, "author": author, "at": datetime.utcnow()}},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )
        return result.modified_count > 0
