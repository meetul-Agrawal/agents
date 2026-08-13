"""ApprovalService — business rules around approval requests."""

from __future__ import annotations
from app.models.schemas import Approval
from app.repositories.approvals import ApprovalRepository


DECISION_MESSAGES = {
    "PENDING": "Your request is currently under management review.",
    "APPROVED": "Your request has been approved by management.",
    "REJECTED": "Your request was reviewed but could not be approved.",
    "CANCELLED": "Your request has been cancelled.",
}


class ApprovalService:
    def __init__(self):
        self._repo = ApprovalRepository()

    async def create_request(self, customer_id: str, case_id: str | None,
                              request_type: str, requested_action: str,
                              reason: str, supporting_context: dict) -> Approval:
        return await self._repo.create(
            customer_id, case_id, request_type, requested_action, reason, supporting_context
        )

    async def get_approval(self, customer_id: str, approval_id: str) -> Approval | None:
        approval = await self._repo.find_by_id(approval_id)
        if approval and approval.customer_id != customer_id:
            return None
        return approval

    async def get_decision(self, customer_id: str, case_id: str) -> dict:
        approvals = await self._repo.find_by_case(case_id)
        # Check scope
        approvals = [a for a in approvals if a.customer_id == customer_id]
        if not approvals:
            return {"found": False, "message": "No approval request found for this case."}
        latest = approvals[0]
        return {
            "found": True,
            "approval_id": latest.approval_id,
            "decision": latest.decision,
            "message": DECISION_MESSAGES.get(latest.decision, "Status unknown."),
            "notes": latest.decision_notes,
        }

    def customer_message_for_decision(self, decision: str) -> str:
        return DECISION_MESSAGES.get(decision, "The status of your request is unavailable.")
