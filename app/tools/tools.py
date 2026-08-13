"""All business tools — definitions + executor.

Each tool:
  1. Has an OpenAI-compatible spec (TOOL_SPECS list)
  2. Has a Python executor function (TOOL_EXECUTORS dict)

The agent only sees the spec. Executors enforce customer scope.
"""

from __future__ import annotations
import json
import logging
import datetime
from typing import Any
from bson import ObjectId
from app.db.mongodb import get_db
from app.repositories.ledger import CustomerRepository
from app.repositories.voucher import SalesRepository, ReceiptRepository, compute_outstanding
from app.repositories.cases import CaseRepository
from app.repositories.approvals import ApprovalRepository
from app.services.case import CaseService
from app.services.approval import ApprovalService
from app.services.payment_behavior import PaymentBehaviorService

logger = logging.getLogger(__name__)


def _sanitize_mongo_doc(obj: Any) -> Any:
    if isinstance(obj, list):
        return [_sanitize_mongo_doc(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_mongo_doc(v) for k, v in obj.items() if k != "_id"}
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, ObjectId):
        return str(obj)
    return obj


# ── Tool specs (OpenAI function-calling format) ───────────────────────────────

TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "query_customer_data",
            "description": (
                "Directly query customer business data from MongoDB (vouchers, ledgers, cases, approvals, stockItems). "
                "Automatically scoped to the authenticated customer. Use this to view any invoices (voucherCategory='Sales'), "
                "receipts (voucherCategory='Receipt'), payments, ledger entries, items, or customer details. "
                "Supports flexible MongoDB filters, projections, sorting (e.g. {'dates.date': -1} for latest), and limits."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {
                        "type": "string",
                        "enum": ["vouchers", "ledgers", "cases", "approvals", "stockItems", "companies"],
                        "description": "Collection to query. 'vouchers' has sales invoices and receipts with dates.date, voucherNumber, amount, ledgerEntries, inventoryAllocations.",
                    },
                    "filter": {
                        "type": "object",
                        "description": "MongoDB filter dictionary (e.g. {'voucherCategory': 'Sales'}, {'voucherNumber': '...'}, {'dates.date': {'$gte': '2026-01-01'}}).",
                    },
                    "projection": {
                        "type": "object",
                        "description": "Fields to include (e.g. {'voucherNumber': 1, 'voucherCategory': 1, 'dates.date': 1, 'amount': 1, 'ledgerEntries': 1, 'inventoryAllocations': 1, 'reference': 1}).",
                    },
                    "sort": {
                        "type": "object",
                        "description": "Sort object (e.g. {'dates.date': -1} to get newest invoices/receipts first).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max documents to return (default 10, max 50).",
                    },
                    "skip": {
                        "type": "integer",
                        "description": "Pagination offset.",
                    },
                },
                "required": ["collection"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "aggregate_customer_data",
            "description": (
                "Run a MongoDB aggregation pipeline on customer records (e.g. sum totals, monthly sales totals, "
                "group by voucher category). Automatically scoped to the authenticated customer."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "collection": {
                        "type": "string",
                        "enum": ["vouchers", "ledgers", "cases", "approvals", "stockItems"],
                        "description": "Collection name.",
                    },
                    "pipeline": {
                        "type": "array",
                        "description": "List of aggregation stages (e.g. [{'$match': {'voucherCategory': 'Sales'}}, {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}])",
                    },
                },
                "required": ["collection", "pipeline"],
            },
        },
    },
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
            "name": "get_outstanding",
            "description": "Get the customer's current calculated outstanding balance from the ledger.",
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
        if name == "query_customer_data":
            return await self._query_customer_data(args)
        if name == "aggregate_customer_data":
            return await self._aggregate_customer_data(args)
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

    async def _query_customer_data(self, args: dict) -> dict:
        col_name = args.get("collection", "vouchers")
        allowed_collections = ["vouchers", "ledgers", "cases", "approvals", "stockItems", "companies", "units", "groups"]
        if col_name not in allowed_collections:
            return {"error": f"Collection '{col_name}' is not accessible. Allowed: {allowed_collections}"}

        db = get_db()
        col = db[col_name]
        user_filter = args.get("filter") or {}
        if not isinstance(user_filter, dict):
            user_filter = {}

        # Enforce strict customer scoping
        if col_name == "vouchers":
            user_filter["partyLedgerName"] = self.customer_name
            if "flags.isDeleted" not in user_filter:
                user_filter["flags.isDeleted"] = {"$ne": True}
        elif col_name == "ledgers":
            user_filter["$or"] = [{"ledgerGuid": self.customer_id}, {"ledgerName": self.customer_name}]
        elif col_name in ("cases", "approvals"):
            user_filter["customer_id"] = self.customer_id

        # Projection
        projection = args.get("projection")
        if projection and isinstance(projection, dict):
            projection["_id"] = 0
        elif projection and isinstance(projection, list):
            projection = {k: 1 for k in projection}
            projection["_id"] = 0
        else:
            projection = {"_id": 0}

        # Sort
        sort_arg = args.get("sort")
        sort_list = []
        if isinstance(sort_arg, dict):
            sort_list = list(sort_arg.items())
        elif isinstance(sort_arg, list):
            sort_list = sort_arg

        limit = min(int(args.get("limit", 10)), 50)
        skip = int(args.get("skip", 0))

        cursor = col.find(user_filter, projection)
        if sort_list:
            cursor = cursor.sort(sort_list)
        cursor = cursor.skip(skip).limit(limit)

        raw_docs = await cursor.to_list(limit)
        sanitized = _sanitize_mongo_doc(raw_docs)
        return {
            "collection": col_name,
            "count": len(sanitized),
            "results": sanitized,
        }

    async def _aggregate_customer_data(self, args: dict) -> dict:
        col_name = args.get("collection", "vouchers")
        allowed_collections = ["vouchers", "ledgers", "cases", "approvals", "stockItems"]
        if col_name not in allowed_collections:
            return {"error": f"Collection '{col_name}' is not accessible. Allowed: {allowed_collections}"}

        db = get_db()
        col = db[col_name]
        raw_pipeline = args.get("pipeline") or []
        if not isinstance(raw_pipeline, list):
            return {"error": "Pipeline must be a list of aggregation stages."}
        pipeline = list(raw_pipeline)

        # Prepend scope matching
        if col_name == "vouchers":
            pipeline.insert(0, {"$match": {"partyLedgerName": self.customer_name, "flags.isDeleted": {"$ne": True}}})
        elif col_name == "ledgers":
            pipeline.insert(0, {"$match": {"$or": [{"ledgerGuid": self.customer_id}, {"ledgerName": self.customer_name}]}})
        elif col_name in ("cases", "approvals"):
            pipeline.insert(0, {"$match": {"customer_id": self.customer_id}})

        cursor = col.aggregate(pipeline)
        raw_docs = await cursor.to_list(50)
        sanitized = _sanitize_mongo_doc(raw_docs)
        return {
            "collection": col_name,
            "count": len(sanitized),
            "results": sanitized,
        }

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
        opening_amt = ob.get("amount", 0.0) if ob else 0.0
        # Compute current balance: year-opening + all voucher movements for this party
        # Tally: negative result = DR = customer owes us (outstanding)
        current = await compute_outstanding(self.customer_name, opening_amt)
        amt = abs(current)
        btype = "DEBIT" if current <= 0 else "CREDIT"
        return {
            "outstanding_balance": amt,
            "balance_type": btype,
            "formatted_outstanding": f"₹{amt:,.2f} ({btype})",
            "note": "Current outstanding computed from opening balance + all sales and receipts.",
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
