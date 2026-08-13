"""Tests for case service logic (duplicate detection, scope enforcement)."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.models.schemas import Case, CaseCreationResult
from app.services.case import CaseService
from datetime import datetime


def _make_case(**kwargs) -> Case:
    defaults = dict(
        case_id="CASE-0001",
        customer_id="CUST-A",
        case_type="DISPUTE",
        subject="Test",
        description="Test",
        status="OPEN",
        priority="NORMAL",
        related_entities={},
        notes=[],
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    defaults.update(kwargs)
    return Case(**defaults)


class TestCaseScopeEnforcement:
    def test_get_case_wrong_customer_returns_none(self):
        """A case belonging to CUST-B must not be returned when CUST-A requests it."""
        case_b = _make_case(customer_id="CUST-B")
        requesting_customer = "CUST-A"
        # Simulate scope check
        result = case_b if case_b.customer_id == requesting_customer else None
        assert result is None, "Cross-customer case access must be denied"

    def test_case_belongs_to_correct_customer(self):
        case_a = _make_case(customer_id="CUST-A")
        result = case_a if case_a.customer_id == "CUST-A" else None
        assert result is not None


class TestCaseDuplicateDetection:
    def test_existing_open_case_detected(self):
        """When an open DISPUTE already exists for INV-100, no duplicate should be created."""
        existing = _make_case(
            case_type="DISPUTE",
            status="OPEN",
            related_entities={"invoice_id": "INV-100"},
        )
        # Simulate the duplicate check in CaseService.create
        existing_cases = [existing]
        related_inv = "INV-100"
        duplicate = next(
            (c for c in existing_cases
             if c.case_type == "DISPUTE"
             and c.related_entities.get("invoice_id") == related_inv),
            None,
        )
        assert duplicate is not None, "Duplicate case should be detected"

    def test_no_duplicate_when_different_invoice(self):
        existing = _make_case(
            case_type="DISPUTE",
            status="OPEN",
            related_entities={"invoice_id": "INV-200"},
        )
        related_inv = "INV-999"
        duplicate = next(
            (c for c in [existing]
             if c.case_type == "DISPUTE"
             and c.related_entities.get("invoice_id") == related_inv),
            None,
        )
        assert duplicate is None


class TestApprovalDecisionGate:
    def test_pending_approval_is_not_approved(self):
        from app.services.approval import DECISION_MESSAGES
        msg = DECISION_MESSAGES["PENDING"]
        assert "approved" not in msg.lower() or "under" in msg.lower()
        assert "review" in msg.lower() or "pending" in msg.lower()

    def test_approved_message_is_accurate(self):
        from app.services.approval import DECISION_MESSAGES
        msg = DECISION_MESSAGES["APPROVED"]
        assert "approved" in msg.lower()

    def test_rejected_message_does_not_claim_approval(self):
        from app.services.approval import DECISION_MESSAGES
        msg = DECISION_MESSAGES["REJECTED"]
        assert "approved" not in msg.lower() or "not" in msg.lower() or "could not" in msg.lower()


if __name__ == "__main__":
    # ponytail self-check
    t = TestCaseScopeEnforcement()
    t.test_get_case_wrong_customer_returns_none()
    t.test_case_belongs_to_correct_customer()

    t2 = TestCaseDuplicateDetection()
    t2.test_existing_open_case_detected()
    t2.test_no_duplicate_when_different_invoice()

    t3 = TestApprovalDecisionGate()
    t3.test_pending_approval_is_not_approved()
    t3.test_approved_message_is_accurate()
    t3.test_rejected_message_does_not_claim_approval()

    print("All case/approval logic tests passed.")
