"""FinancialContextService — assembles a coherent financial picture for a customer."""

from __future__ import annotations
from app.models.schemas import FinancialContext, IntentClassification, ExtractedEntities
from app.repositories.ledger import CustomerRepository
from app.repositories.voucher import SalesRepository, ReceiptRepository


class FinancialContextService:
    def __init__(self):
        self._customer = CustomerRepository()
        self._sales = SalesRepository()
        self._receipts = ReceiptRepository()

    async def get_context(
        self,
        customer_name: str,
        customer_id: str,
        intent: IntentClassification | None = None,
        entities: ExtractedEntities | None = None,
    ) -> FinancialContext:
        """Targeted retrieval — only fetch what the intent requires."""
        invoice_ids = entities.invoice_ids if entities else []
        receipt_ids = entities.receipt_ids if entities else []

        needs_sales = (
            not intent
            or intent.requires_financial_context
            or (intent.intent in (
                "INVOICE_QUERY", "OUTSTANDING_QUERY", "DISPUTE",
                "PAYMENT_QUERY", "RECEIPT_QUERY", "LEDGER_QUERY",
            ))
        )
        needs_receipts = (
            not intent
            or intent.requires_financial_context
            or intent.intent in (
                "PAYMENT_QUERY", "RECEIPT_QUERY", "OUTSTANDING_QUERY",
                "PAYMENT_HISTORY", "PAYMENT_REMINDER",
            )
        )

        invoices: list[dict] = []
        receipts: list[dict] = []
        on_account: list[dict] = []
        notes: list[str] = []

        if needs_sales:
            if invoice_ids:
                for inv_id in invoice_ids[:5]:
                    doc = await self._sales.get_invoice_by_number(customer_name, inv_id)
                    if doc:
                        invoices.append(doc.model_dump())
                    else:
                        notes.append(f"Invoice {inv_id} not found for this customer.")
            else:
                docs = await self._sales.get_invoices(customer_name, limit=20)
                invoices = [d.model_dump() for d in docs]

        if needs_receipts:
            if invoice_ids and invoices:
                for inv_id in invoice_ids[:5]:
                    against = await self._receipts.get_receipts_for_invoice(customer_name, inv_id)
                    receipts.extend(d.model_dump() for d in against)
                on_acc = await self._receipts.get_on_account_receipts(customer_name, limit=20)
                on_account = [d.model_dump() for d in on_acc]
            else:
                all_r = await self._receipts.get_receipts(customer_name, limit=30)
                for r in all_r:
                    if r.against_reference_allocations:
                        receipts.append(r.model_dump())
                    if r.on_account_allocations:
                        on_account.append(r.model_dump())

        # Opening balance from ledger as authoritative outstanding
        cust_raw = await self._customer.find_by_guid(customer_id)
        outstanding: float | None = None
        if cust_raw:
            ob = cust_raw.get("balances", {}).get("openingBalance", {})
            if ob:
                outstanding = ob.get("amount")

        return FinancialContext(
            customer_id=customer_id,
            customer_name=customer_name,
            invoices=invoices,
            receipts=receipts,
            on_account_receipts=on_account,
            reported_outstanding=outstanding,
            reconciliation_notes=notes,
        )
