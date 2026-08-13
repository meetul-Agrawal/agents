"""HTTP REST endpoints (health, session info)."""

from __future__ import annotations
from fastapi import APIRouter, HTTPException
from app.repositories.ledger import CustomerRepository
from app.repositories.cases import CaseRepository
from app.repositories.approvals import ApprovalRepository

router = APIRouter()
_cust = CustomerRepository()
_cases = CaseRepository()
_approvals = ApprovalRepository()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/customers/{customer_id}/profile")
async def get_profile(customer_id: str):
    raw = await _cust.find_by_guid(customer_id)
    if not raw:
        raise HTTPException(404, "Customer not found")
    doc = await _cust.to_customer_doc(raw)
    return doc.model_dump()


@router.get("/customers/{customer_id}/cases")
async def get_cases(customer_id: str, status: str | None = None):
    cases = await _cases.find_by_customer(customer_id, status=status)
    return [c.model_dump() for c in cases]


@router.get("/customers/{customer_id}/approvals")
async def get_approvals(customer_id: str):
    approvals = await _approvals.find_by_customer(customer_id)
    return [a.model_dump() for a in approvals]


# Management-only: set approval decision (would be auth-gated in production)
@router.post("/approvals/{approval_id}/decision")
async def set_decision(approval_id: str, decision: str, notes: str = ""):
    ok = await _approvals.set_decision(approval_id, decision.upper(), notes or None)
    if not ok:
        raise HTTPException(404, "Approval not found")
    return {"success": True}
