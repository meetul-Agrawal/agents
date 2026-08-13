"""CaseService — business rules around case creation/update."""

from __future__ import annotations
from app.models.schemas import Case, CaseCreationResult
from app.repositories.cases import CaseRepository


class CaseService:
    def __init__(self):
        self._repo = CaseRepository()

    async def search(self, customer_id: str, query: str | None = None,
                     status: str | None = None) -> list[Case]:
        return await self._repo.search(customer_id, query, status)

    async def get(self, customer_id: str, case_id: str) -> Case | None:
        case = await self._repo.find_by_id(case_id)
        if case and case.customer_id != customer_id:
            return None  # enforce customer scope
        return case

    async def create(self, customer_id: str, case_type: str, subject: str,
                     description: str, related_entities: dict,
                     priority: str = "NORMAL") -> CaseCreationResult:
        # Duplicate check: open case with same type + related invoice
        related_inv = related_entities.get("invoice_id")
        if related_inv:
            existing = await self._repo.search(
                customer_id, query=related_inv, status="OPEN"
            )
            for c in existing:
                if (c.case_type == case_type
                        and c.related_entities.get("invoice_id") == related_inv):
                    return CaseCreationResult(
                        success=True,
                        case_id=c.case_id,
                        status="EXISTING",
                        error=f"Open case {c.case_id} already covers this issue.",
                    )

        try:
            case = await self._repo.create(
                customer_id, case_type, subject, description, related_entities, priority
            )
            return CaseCreationResult(success=True, case_id=case.case_id, status="CREATED")
        except Exception as exc:
            return CaseCreationResult(success=False, error=str(exc))

    async def update(self, customer_id: str, case_id: str, updates: dict) -> bool:
        case = await self._repo.find_by_id(case_id)
        if not case or case.customer_id != customer_id:
            return False
        return await self._repo.update(case_id, customer_id, updates)

    async def add_note(self, customer_id: str, case_id: str, note: str) -> bool:
        case = await self._repo.find_by_id(case_id)
        if not case or case.customer_id != customer_id:
            return False
        return await self._repo.add_note(case_id, customer_id, note)
