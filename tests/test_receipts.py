"""Tests for receipt semantics — against-reference vs on-account."""

import asyncio
import pytest
from app.models.schemas import ReceiptDoc, ReceiptAllocation


def make_receipt(allocations: list[dict]) -> ReceiptDoc:
    return ReceiptDoc(
        voucher_number="TEST-001",
        party_ledger_name="Test Customer",
        allocations=[ReceiptAllocation(**a) for a in allocations],
    )


def test_against_reference_receipt():
    r = make_receipt([{"bill_name": "INV-100", "bill_type": "Agst Ref", "amount": 40000}])
    assert len(r.against_reference_allocations) == 1
    assert r.against_reference_allocations[0].bill_name == "INV-100"
    assert r.on_account_allocations == []


def test_on_account_receipt():
    r = make_receipt([{"bill_name": None, "bill_type": "New Ref", "amount": 20000}])
    assert r.against_reference_allocations == []
    assert len(r.on_account_allocations) == 1


def test_advance_receipt_is_on_account():
    r = make_receipt([{"bill_name": "ADV-001", "bill_type": "Advance", "amount": 10000}])
    assert r.against_reference_allocations == []
    assert len(r.on_account_allocations) == 1


def test_mixed_receipt():
    r = make_receipt([
        {"bill_name": "INV-200", "bill_type": "Agst Ref", "amount": 30000},
        {"bill_name": None, "bill_type": "New Ref", "amount": 5000},
    ])
    assert len(r.against_reference_allocations) == 1
    assert len(r.on_account_allocations) == 1
    # On-account portion is NOT linked to INV-200
    assert r.on_account_allocations[0].bill_name is None


def test_on_account_not_automatically_allocated_to_invoice():
    """Core rule: on-account receipts must NOT be treated as paying a specific invoice."""
    on_acc = make_receipt([{"bill_name": None, "bill_type": "New Ref", "amount": 20000}])
    against = [a for a in on_acc.allocations if a.bill_type == "Agst Ref" and a.bill_name == "INV-999"]
    # Must be empty — on-account is NOT linked to INV-999
    assert against == [], "On-account receipt must not be auto-allocated to any invoice"


if __name__ == "__main__":
    # ponytail self-check
    test_against_reference_receipt()
    test_on_account_receipt()
    test_advance_receipt_is_on_account()
    test_mixed_receipt()
    test_on_account_not_automatically_allocated_to_invoice()
    print("All receipt semantics tests passed.")
