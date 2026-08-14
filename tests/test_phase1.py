"""Phase 1 gate: the money math, the resolver, and the book's data quality.

Unit tests run on synthetic voucher documents and need no database.
Tests marked `live` run against MongoDB and skip cleanly when it is not there.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ca import customer360 as c3
from ca import evals as E
from ca.contracts import Customer

GOLDEN = "evals/datasets/customer360/outstanding.jsonl"


# --------------------------------------------------------------------------
# Synthetic documents
# --------------------------------------------------------------------------

PARTY = "Test Traders"


def sale(number: str, amount: float, when: date = date(2026, 1, 1), party: str = PARTY) -> dict:
    """A sales voucher. The party line is stored NEGATIVE for an amount owed."""
    return {
        "voucherNumber": number,
        "voucherCategory": "Sales",
        "voucherTypeName": "Sales",
        "partyLedgerName": party,
        "dates": {"date": datetime(when.year, when.month, when.day, tzinfo=timezone.utc)},
        "ledgerEntries": [
            {"ledgerName": party, "amount": -amount, "billAllocations": []},
            {"ledgerName": "Sales Account", "amount": amount, "billAllocations": []},
        ],
        "inventoryEntries": [],
    }


def receipt(
    number: str,
    amount: float,
    bills: list[tuple[str, str, float]] | None = None,
    when: date = date(2026, 1, 15),
    party: str = PARTY,
) -> dict:
    """A receipt. The party line is stored POSITIVE. `bills` is
    (billType, name, amount)."""
    return {
        "voucherNumber": number,
        "voucherCategory": "Receipt",
        "voucherTypeName": "Receipt",
        "partyLedgerName": party,
        "dates": {"date": datetime(when.year, when.month, when.day, tzinfo=timezone.utc)},
        "ledgerEntries": [
            {
                "ledgerName": party,
                "amount": amount,
                "billAllocations": [
                    {"billType": t, "name": n, "amount": a} for t, n, a in (bills or [])
                ],
            },
            {"ledgerName": "Bank", "amount": -amount, "billAllocations": [
                {"billType": None, "name": None, "amount": 0}
            ]},
        ],
        "inventoryEntries": [],
    }


def vset(*vouchers: dict) -> c3.VoucherSet:
    return c3.VoucherSet(PARTY, list(vouchers))


CUSTOMER = Customer(
    customer_id="cust-1", ledger_name=PARTY, company_id="co-1", display_name=PARTY
)


# --------------------------------------------------------------------------
# Unit — signs and parsing
# --------------------------------------------------------------------------


def test_sale_increases_and_receipt_reduces_what_is_owed():
    assert c3.party_amount(sale("INV-1", 1000), PARTY) == 1000
    assert c3.party_amount(receipt("REC-1", 400), PARTY) == -400


def test_other_parties_lines_are_ignored():
    v = sale("INV-1", 1000)
    v["ledgerEntries"].append({"ledgerName": "Someone Else", "amount": -9999, "billAllocations": []})
    assert c3.party_amount(v, PARTY) == 1000


def test_voucher_date_parsing_handles_datetime_string_and_missing():
    assert c3._vdate({"dates": {"date": datetime(2026, 3, 1)}}) == date(2026, 3, 1)
    assert c3._vdate({"dates": {"date": "2026-03-01T00:00:00.000Z"}}) == date(2026, 3, 1)
    assert c3._vdate({"dates": {"date": None}}) is None
    assert c3._vdate({}) is None
    assert c3._vdate({"dates": {"date": "not a date"}}) is None


def test_repeated_voucher_number_is_summed_not_overwritten():
    totals = c3.invoice_totals(vset(sale("INV-1", 1000), sale("INV-1", 500)))
    assert totals["INV-1"][1] == 1500


def test_unnumbered_sales_voucher_is_skipped():
    v = sale("", 1000)
    assert c3.invoice_totals(vset(v)) == {}


# --------------------------------------------------------------------------
# Unit — allocations
# --------------------------------------------------------------------------


def test_allocations_split_by_bill_type():
    alloc = c3.allocations(
        vset(
            receipt("R1", 1000, [("Agst Ref", "INV-1", 600), ("On Account", None, 400)]),
            receipt("R2", 700, [("Advance", None, 300), ("New Ref", "INV-9", 400)]),
        )
    )
    assert alloc.against_bill == {"INV-1": 600}
    assert alloc.on_account == 400
    assert alloc.advance == 300
    assert alloc.new_ref == 400
    assert alloc.total_receipted == 1700


def test_null_placeholder_allocation_on_the_bank_line_is_ignored():
    alloc = c3.allocations(vset(receipt("R1", 500, [("Agst Ref", "INV-1", 500)])))
    assert alloc.against_bill == {"INV-1": 500}
    assert alloc.on_account == 0


# --------------------------------------------------------------------------
# Unit — outstanding
# --------------------------------------------------------------------------


def _outstanding(*vouchers, as_of=date(2026, 3, 1)):
    return c3.compute_outstanding(CUSTOMER, vset(*vouchers), as_of=as_of)


def test_fully_paid_bill_is_not_outstanding():
    o = _outstanding(sale("INV-1", 1000), receipt("R1", 1000, [("Agst Ref", "INV-1", 1000)]))
    assert o.outstanding == 0
    assert o.open_bill_count == 0


def test_partially_paid_bill_leaves_the_remainder():
    o = _outstanding(sale("INV-1", 1000), receipt("R1", 400, [("Agst Ref", "INV-1", 400)]))
    assert o.outstanding == 600
    assert o.open_bills[0].allocated == 400


def test_on_account_receipt_does_not_reduce_a_bill():
    o = _outstanding(sale("INV-1", 1000), receipt("R1", 400, [("On Account", None, 400)]))
    assert o.outstanding == 1000
    assert o.on_account == 400


def test_pre_book_settlement_is_reported_not_netted():
    """A receipt paying an invoice that predates the book must not create a
    phantom credit against in-book bills."""
    o = _outstanding(
        sale("INV-1", 1000),
        receipt("R1", 5000, [("Agst Ref", "OLD-999", 5000)]),
    )
    assert o.outstanding == 1000
    assert o.pre_book_settlements == 5000
    assert o.receipted_total == 5000


def test_over_allocated_bill_never_goes_negative():
    o = _outstanding(sale("INV-1", 1000), receipt("R1", 1200, [("Agst Ref", "INV-1", 1200)]))
    assert o.outstanding == 0
    assert o.open_bill_count == 0


def test_sub_paisa_remainder_is_not_an_open_bill():
    o = _outstanding(sale("INV-1", 1000), receipt("R1", 999.995, [("Agst Ref", "INV-1", 999.995)]))
    assert o.open_bill_count == 0


def test_ageing_buckets_at_their_boundaries():
    as_of = date(2026, 4, 1)
    o = _outstanding(
        sale("A", 10, date(2026, 3, 2)),   # 30 days
        sale("B", 20, date(2026, 3, 1)),   # 31 days
        sale("C", 40, date(2026, 1, 31)),  # 60 days
        sale("D", 80, date(2026, 1, 30)),  # 61 days
        sale("E", 160, date(2026, 1, 1)),  # 90 days
        sale("F", 320, date(2025, 12, 31)),  # 91 days
        as_of=as_of,
    )
    assert o.ageing == {"0-30": 10, "31-60": 60, "61-90": 240, "90+": 320}
    assert o.outstanding == 630


def test_open_bills_are_oldest_first():
    o = _outstanding(sale("NEW", 10, date(2026, 2, 1)), sale("OLD", 10, date(2026, 1, 1)))
    assert [b.voucher_number for b in o.open_bills] == ["OLD", "NEW"]


def test_customer_with_no_vouchers_is_zero_not_an_error():
    o = _outstanding()
    assert o.outstanding == 0 and o.open_bills == [] and o.invoiced_total == 0


def test_negative_allocation_is_carried_through():
    """Some receipts carry negative Agst Ref amounts (adjustments). They must
    increase the open balance, not be silently dropped."""
    o = _outstanding(sale("INV-1", 1000), receipt("R1", -200, [("Agst Ref", "INV-1", -200)]))
    assert o.outstanding == 1200


# --------------------------------------------------------------------------
# Unit — ledger, behaviour, timeline
# --------------------------------------------------------------------------


def test_ledger_running_balance_and_direction():
    lines = c3.build_ledger(
        vset(
            sale("INV-1", 1000, date(2026, 1, 1)),
            receipt("R1", 400, [("Agst Ref", "INV-1", 400)], date(2026, 1, 5)),
        ),
        opening_balance=100,
    )
    assert [(x.debit, x.credit, x.balance) for x in lines] == [(1000, 0, 1100), (0, 400, 700)]
    assert lines[1].against_bills == ["INV-1"]


def test_payment_behaviour_measures_settlement_lag():
    b = c3.payment_behaviour(
        vset(
            sale("INV-1", 1000, date(2026, 1, 1)),
            sale("INV-2", 1000, date(2026, 1, 1)),
            receipt("R1", 1000, [("Agst Ref", "INV-1", 1000)], date(2026, 1, 11)),
            receipt("R2", 1000, [("Agst Ref", "INV-2", 1000)], date(2026, 1, 21)),
        )
    )
    assert b.avg_days_to_settle == 15.0
    assert b.settled_bill_count == 2
    assert b.receipt_count == 2 and b.total_received == 2000
    assert b.first_receipt == date(2026, 1, 11) and b.last_receipt == date(2026, 1, 21)


def test_payment_behaviour_without_receipts_has_no_average():
    assert c3.payment_behaviour(vset(sale("INV-1", 10))).avg_days_to_settle is None


def test_timeline_is_newest_first_and_labels_kinds():
    events = c3.build_timeline(
        CUSTOMER,
        vset(sale("INV-1", 1000, date(2026, 1, 1)), receipt("R1", 400, [], date(2026, 2, 1))),
    )
    assert [e.kind for e in events] == ["receipt", "invoice"]
    assert events[0].ref == "R1"


def test_capabilities_say_what_the_book_cannot_answer():
    caps = c3.capabilities()
    assert not (caps.credit_notes or caps.orders or caps.due_dates or caps.credit_limits)
    assert c3.get_credit_notes("cust-1") == [] and c3.get_open_orders("cust-1") == []


# --------------------------------------------------------------------------
# Live — MongoDB required
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live():
    from pymongo.errors import PyMongoError

    from ca.config import tenant_db

    try:
        tenant_db()["ledgers"].count_documents({}, limit=1)
    except PyMongoError as exc:
        pytest.skip(f"MongoDB unavailable: {exc}")
    return tenant_db()


@pytest.fixture(scope="session")
def golden():
    return E.load_dataset(GOLDEN)


def test_resolve_by_exact_name(live):
    c = c3.resolve_customer("Aadinath Traders, Siyaganj")
    assert c.ledger_name == "Aadinath Traders, Siyaganj"
    assert c.customer_id and c.group_path and "Sundry Debtors" in c.group_path


def test_resolve_by_ledger_id(live):
    by_name = c3.resolve_customer("Aadinath Traders, Siyaganj")
    assert c3.resolve_customer(by_name.customer_id).customer_id == by_name.customer_id


def test_resolve_by_mobile(live):
    """84 mobiles in this book are shared between customers, so a phone number
    resolves to one customer or to an explicit ambiguity — never to a guess."""
    customer = c3.resolve_customer("Aadinath Traders, Siyaganj")
    try:
        assert c3.resolve_customer(customer.mobile).customer_id == customer.customer_id
    except c3.AmbiguousCustomerError as exc:
        assert customer.customer_id in {m.customer_id for m in exc.matches}
        assert all(m.mobile.endswith(customer.mobile[-10:]) for m in exc.matches)


def test_unknown_customer_raises(live):
    with pytest.raises(c3.CustomerNotFoundError):
        c3.resolve_customer("Nonexistent Trading Company Pvt Ltd")


def test_ambiguous_query_raises_instead_of_guessing(live):
    with pytest.raises(c3.AmbiguousCustomerError) as exc:
        c3.resolve_customer("Traders")
    assert len(exc.value.matches) > 1


def test_golden_outstanding_matches_independent_implementation(live, golden):
    """The expected values come from scripts/gen_golden.js, a separately written
    implementation of the same rules."""
    assert golden, "golden dataset is empty"
    for case in golden:
        as_of = date.fromisoformat(case.context["as_of"])
        actual = c3.get_outstanding(case.customer_id, as_of=as_of)
        assert actual.outstanding == pytest.approx(case.expected["outstanding"], abs=0.01), case.case_id
        assert actual.open_bill_count == case.expected["open_bill_count"], case.case_id
        assert actual.ageing == pytest.approx(case.expected["ageing"], abs=0.01), case.case_id
        assert actual.pre_book_settlements == pytest.approx(
            case.expected["pre_book_settlements"], abs=0.01
        ), case.case_id


def test_outstanding_equals_sum_of_open_bills(live, golden):
    for case in golden:
        o = c3.get_outstanding(case.customer_id, as_of=date.fromisoformat(case.context["as_of"]))
        assert o.outstanding == pytest.approx(sum(b.outstanding for b in o.open_bills), abs=0.01)
        assert o.outstanding == pytest.approx(sum(o.ageing.values()), abs=0.01)
        assert all(b.outstanding > 0 for b in o.open_bills)


def test_ledger_ends_at_invoiced_minus_receipted(live, golden):
    for case in golden[:3]:
        customer = c3.get_customer(case.customer_id)
        vs = c3.fetch_vouchers(customer.ledger_name)
        lines = c3.build_ledger(vs, opening_balance=customer.opening_balance)
        expected = customer.opening_balance + sum(
            c3.party_amount(v, vs.ledger_name) for v in vs.vouchers
        )
        assert lines[-1].balance == pytest.approx(expected, abs=0.01), case.case_id


def test_build_customer_360_answers_the_exit_criteria(live, golden):
    c360 = c3.build_customer_360(golden[0].customer_id, as_of=date(2026, 4, 23))
    assert c360.customer.ledger_name
    assert c360.financial["outstanding"] == golden[0].expected["outstanding"]
    assert c360.financial["ageing"] and c360.financial["payment_behaviour"]["receipt_count"] > 0
    assert c360.commercial["invoice_count"] > 0
    assert c360.commercial["capabilities"]["credit_notes"] is False
    # App-database sections exist and are empty until later phases write to them.
    for section in ("cases", "approvals", "promises", "events"):
        assert c360.operational[section] == []


def test_timeline_covers_every_voucher(live, golden):
    case = golden[1]
    customer = c3.get_customer(case.customer_id)
    vs = c3.fetch_vouchers(customer.ledger_name)
    events = c3.get_customer_timeline(case.customer_id, limit=10_000)
    assert len(events) == len([v for v in vs.vouchers if c3._vdate(v)])
    assert events == sorted(events, key=lambda e: e.at, reverse=True)


def test_sales_and_receipt_reads_are_newest_first(live, golden):
    cid = golden[0].customer_id
    sales = c3.get_sales_history(cid, limit=5)
    receipts = c3.get_receipts(cid, limit=5)
    assert len(sales) == 5 and len(receipts) == 5
    assert sales == sorted(sales, key=lambda r: r["date"], reverse=True)
    assert any(r["against_bills"] for r in receipts)


def test_cancelled_and_optional_vouchers_are_excluded(live):
    from ca.config import tenant_db

    parked = tenant_db()["vouchers"].find_one(
        {"$or": [{"flags.isCancelled": True}, {"flags.isOptional": True}],
         "partyLedgerName": {"$nin": [None, ""]}}
    )
    if not parked:
        pytest.skip("no cancelled or optional vouchers in this book")
    vs = c3.fetch_vouchers(parked["partyLedgerName"])
    assert parked["_id"] not in {v["_id"] for v in vs.vouchers}


# --------------------------------------------------------------------------
# Live — data quality invariants
# --------------------------------------------------------------------------


def test_every_voucher_is_double_entry_balanced(live):
    from ca.data_quality import check_unbalanced_vouchers

    finding = check_unbalanced_vouchers()
    assert finding.clean, finding


def test_no_bill_is_paid_beyond_its_value(live):
    from ca.data_quality import check_negative_open_bills

    finding = check_negative_open_bills()
    assert finding.clean, finding


def test_every_debtor_has_an_identity(live):
    from ca.data_quality import check_missing_customer_identity

    assert check_missing_customer_identity().clean


def test_data_quality_report_runs_and_is_typed(live):
    from ca.data_quality import run_all

    findings = run_all()
    assert len(findings) == 10
    assert all(f.severity in {"P0", "P1", "P2"} for f in findings)
    by_name = {f.check: f for f in findings}
    # Known and accepted: receipts settling bills that predate this book.
    assert by_name["receipt_against_unknown_invoice"].count > 0
