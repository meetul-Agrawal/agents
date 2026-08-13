"""All business tools — definitions + executor.

Each tool:
  1. Has an OpenAI-compatible spec (TOOL_SPECS list)
  2. Has a Python executor function (TOOL_EXECUTORS dict)

The agent only sees the spec. Executors enforce customer scope.
"""

from __future__ import annotations
import json
import logging
from app.repositories.ledger import CustomerRepository
from app.repositories.voucher import SalesRepository, ReceiptRepository
from app.repositories.cases import CaseRepository
from app.repositories.approvals import ApprovalRepository
from app.services.case import CaseService
from app.services.approval import ApprovalService
from app.services.payment_behavior import PaymentBehaviorService

logger = logging.getLogger(__name__)

# ── Tool specs (OpenAI function-calling format) ───────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_profile",
            "description": "Get the authenticated customer's profile and current outstanding balance.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_invoices",
            "description": "Get recent sales invoices for the customer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string", "description": "Specific invoice number to fetch."},
                    "limit": {"type": "integer", "description": "Max results (default 10)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_receipts",
            "description": "Get payment receipts for the customer. Shows both against-reference and on-account receipts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "invoice_number": {"type": "string", "description": "Filter receipts linked to this invoice."},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_outstanding",
            "description": "Get the customer's current outstanding balance from the ledger.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_cases",
            "description": "Search the customer's support/dispute cases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "status": {"type": "string", "enum": ["OPEN", "IN_PROGRESS", "PENDING_APPROVAL", "RESOLVED", "CLOSED"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_case",
            "description": "Get a specific case by ID.",
            "parameters": {
                "type": "object",
                "properties": {"case_id": {"type": "string"}},
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_case",
            "description": "Create a new support/dispute case. Checks for duplicates first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_type": {
                        "type": "string",
                        "enum": ["COMPLAINT", "DISPUTE", "PAYMENT_ISSUE", "INVOICE_ISSUE",
                                 "RECEIPT_ISSUE", "APPROVAL_REQUEST", "GENERAL_SUPPORT"],
                    },
                    "subject": {"type": "string"},
                    "description": {"type": "string"},
                    "related_invoice": {"type": "string"},
                    "priority": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"]},
                },
                "required": ["case_type", "subject", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_case",
            "description": "Update an existing case (status, add note).",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "status": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_approval_request",
            "description": "Submit a request for management approval (e.g. fee waiver, credit note).",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_type": {"type": "string"},
                    "requested_action": {"type": "string"},
                    "reason": {"type": "string"},
                    "case_id": {"type": "string"},
                },
                "required": ["request_type", "requested_action", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_management_decision",
            "description": "Check the management decision on an approval request for a case.",
            "parameters": {
                "type": "object",
                "properties": {"case_id": {"type": "string"}},
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_payment_behavior",
            "description": "Analyze historical payment patterns for payment reminder context.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Escalate the conversation to a human agent.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


# ── Executor ──────────────────────────────────────────────────────────────────

class ToolExecutor:
    """Executes tool calls with customer_id scope enforced."""

    def __init__(self, customer_id: str, customer_name: str):
        self.customer_id = customer_id
        self.customer_name = customer_name
        self._cust_repo = CustomerRepository()
        self._sales_repo = SalesRepository()
        self._receipt_repo = ReceiptRepository()
        self._case_svc = CaseService()
        self._approval_svc = ApprovalService()
        self._payment_svc = PaymentBehaviorService()

    async def execute(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments) if arguments else {}
            result = await self._dispatch(name, args)
            return json.dumps(result, default=str)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return json.dumps({"error": str(exc)})

    async def _dispatch(self, name: str, args: dict) -> dict:
        if name == "get_customer_profile":
            return await self._get_customer_profile()
        if name == "get_invoices":
            return await self._get_invoices(args)
        if name == "get_receipts":
            return await self._get_receipts(args)
        if name == "get_outstanding":
            return await self._get_outstanding()
        if name == "search_cases":
            return await self._search_cases(args)
        if name == "get_case":
            return await self._get_case(args)
        if name == "create_case":
            return await self._create_case(args)
        if name == "update_case":
            return await self._update_case(args)
        if name == "create_approval_request":
            return await self._create_approval(args)
        if name == "get_management_decision":
            return await self._get_decision(args)
        if name == "get_payment_behavior":
            return await self._get_payment_behavior()
        if name == "escalate_to_human":
            return {"escalated": True, "reason": args.get("reason", "")}
        return {"error": f"Unknown tool: {name}"}

    async def _get_customer_profile(self) -> dict:
        raw = await self._cust_repo.find_by_guid(self.customer_id)
        if not raw:
            return {"error": "Customer not found."}
        doc = await self._cust_repo.to_customer_doc(raw)
        return doc.model_dump()

    async def _get_invoices(self, args: dict) -> dict:
        inv_num = args.get("invoice_number")
        limit = int(args.get("limit", 10))
        if inv_num:
            doc = await self._sales_repo.get_invoice_by_number(self.customer_name, inv_num)
            if not doc:
                return {"found": False, "invoice_number": inv_num}
            return {"found": True, "invoice": doc.model_dump()}
        docs = await self._sales_repo.get_invoices(self.customer_name, limit=limit)
        return {"invoices": [d.model_dump() for d in docs], "count": len(docs)}

    async def _get_receipts(self, args: dict) -> dict:
        inv_num = args.get("invoice_number")
        limit = int(args.get("limit", 20))
        if inv_num:
            against = await self._receipt_repo.get_receipts_for_invoice(self.customer_name, inv_num)
            on_acc = await self._receipt_repo.get_on_account_receipts(self.customer_name, limit=limit)
            return {
                "against_reference": [d.model_dump() for d in against],
                "on_account": [d.model_dump() for d in on_acc],
                "note": "on_account receipts are NOT automatically allocated to this invoice.",
            }
        all_r = await self._receipt_repo.get_receipts(self.customer_name, limit=limit)
        return {
            "receipts": [d.model_dump() for d in all_r],
            "count": len(all_r),
            "note": "Check bill_type in allocations: 'Agst Ref'=against invoice, 'New Ref'/'Advance'=on account.",
        }

    async def _get_outstanding(self) -> dict:
        raw = await self._cust_repo.find_by_guid(self.customer_id)
        if not raw:
            return {"error": "Customer not found."}
        ob = raw.get("balances", {}).get("openingBalance", {})
        raw_amt = ob.get("amount", 0.0) if ob else 0.0
        amt = abs(raw_amt)
        btype = ob.get("type", "DEBIT") if ob else "DEBIT"
        return {
            "outstanding_balance": amt,
            "balance_type": btype,
            "formatted_outstanding": f"₹{amt:,.2f} ({btype})",
            "as_of_date": str(ob.get("asOfDate")) if ob and ob.get("asOfDate") else None,
            "note": "Authoritative ledger outstanding balance.",
        }

    async def _search_cases(self, args: dict) -> dict:
        cases = await self._case_svc.search(
            self.customer_id, args.get("query"), args.get("status")
        )
        return {"cases": [c.model_dump() for c in cases], "count": len(cases)}

    async def _get_case(self, args: dict) -> dict:
        case = await self._case_svc.get(self.customer_id, args["case_id"])
        if not case:
            return {"found": False, "case_id": args["case_id"]}
        return {"found": True, "case": case.model_dump()}

    async def _create_case(self, args: dict) -> dict:
        related: dict = {}
        if args.get("related_invoice"):
            related["invoice_id"] = args["related_invoice"]
        result = await self._case_svc.create(
            customer_id=self.customer_id,
            case_type=args["case_type"],
            subject=args["subject"],
            description=args["description"],
            related_entities=related,
            priority=args.get("priority", "NORMAL"),
        )
        return result.model_dump()

    async def _update_case(self, args: dict) -> dict:
        case_id = args["case_id"]
        updates: dict = {}
        if args.get("status"):
            updates["status"] = args["status"]
        ok = await self._case_svc.update(self.customer_id, case_id, updates) if updates else True
        if args.get("note"):
            ok = await self._case_svc.add_note(self.customer_id, case_id, args["note"])
        return {"success": ok, "case_id": case_id}

    async def _create_approval(self, args: dict) -> dict:
        approval = await self._approval_svc.create_request(
            customer_id=self.customer_id,
            case_id=args.get("case_id"),
            request_type=args["request_type"],
            requested_action=args["requested_action"],
            reason=args["reason"],
            supporting_context={},
        )
        return {
            "success": True,
            "approval_id": approval.approval_id,
            "decision": approval.decision,
            "message": "Your request has been submitted for management review.",
        }

    async def _get_decision(self, args: dict) -> dict:
        return await self._approval_svc.get_decision(self.customer_id, args["case_id"])

    async def _get_payment_behavior(self) -> dict:
        behavior = await self._payment_svc.analyze(self.customer_name)
        return behavior.model_dump()
