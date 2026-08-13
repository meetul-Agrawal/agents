"""ApprovalRepository — manages the approvals collection."""

from __future__ import annotations
import uuid
from datetime import datetime
from app.db.mongodb import get_db
from app.models.schemas import Approval


class ApprovalRepository:
    @property
    def col(self):
        return get_db()["approvals"]

    async def create(self, customer_id: str, case_id: str | None, request_type: str,
                     requested_action: str, reason: str, supporting_context: dict) -> Approval:
        approval = Approval(
            approval_id=f"APR-{uuid.uuid4().hex[:8].upper()}",
            customer_id=customer_id,
            case_id=case_id,
            request_type=request_type,
            requested_action=requested_action,
            reason=reason,
            supporting_context=supporting_context,
        )
        await self.col.insert_one(approval.model_dump())
        return approval

    async def find_by_id(self, approval_id: str) -> Approval | None:
        raw = await self.col.find_one({"approval_id": approval_id}, {"_id": 0})
        return Approval(**raw) if raw else None

    async def find_by_case(self, case_id: str) -> list[Approval]:
        cursor = self.col.find({"case_id": case_id}, {"_id": 0}).sort("created_at", -1)
        return [Approval(**d) async for d in cursor]

    async def find_by_customer(self, customer_id: str) -> list[Approval]:
        cursor = self.col.find({"customer_id": customer_id}, {"_id": 0}).sort("created_at", -1)
        return [Approval(**d) async for d in cursor]

    async def set_decision(self, approval_id: str, decision: str, notes: str | None = None) -> bool:
        result = await self.col.update_one(
            {"approval_id": approval_id},
            {"$set": {"decision": decision, "decision_notes": notes, "decided_at": datetime.utcnow()}},
        )
        return result.modified_count > 0
