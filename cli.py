"""Customer Representative Agent — terminal interface.

Run: python cli.py
     python cli.py --customer-id <ledgerGuid>   (skip the picker)
     python cli.py --session-id <uuid>           (resume a session)
"""

import asyncio
import uuid
import argparse
from app.agents.customer_rep.graph import graph
from app.repositories.ledger import CustomerRepository


async def pick_customer() -> tuple[str, str]:
    """Interactive customer search → returns (ledger_guid, ledger_name)."""
    repo = CustomerRepository()
    while True:
        query = input("Search customer name: ").strip()
        if not query:
            continue
        results = await repo.search_by_name(query, limit=8)
        if not results:
            print("  No matches. Try again.\n")
            continue
        for i, r in enumerate(results):
            print(f"  [{i+1}] {r['ledgerName']}  ({r['ledgerGuid']})")
        choice = input("Pick number: ").strip()
        try:
            idx = int(choice) - 1
            r = results[idx]
            return r["ledgerGuid"], r["ledgerName"]
        except (ValueError, IndexError):
            print("  Invalid choice.\n")


async def run(customer_id: str, session_id: str) -> None:
    repo = CustomerRepository()
    raw = await repo.find_by_guid(customer_id)
    if not raw:
        print(f"Customer {customer_id} not found in ledger.")
        return
    name = raw["ledgerName"]

    print(f"\n─── Customer Representative ────────────────────")
    print(f"  Customer : {name}")
    print(f"  Session  : {session_id}")
    print(f"  'quit' to exit\n")

    config = {"configurable": {"thread_id": session_id}}

    while True:
        try:
            message = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if message.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break
        if not message:
            continue

        state = {
            "session_id": session_id,
            "customer_id": customer_id,
            "customer_name": None,
            "messages": [{"role": "user", "content": message}],
            "conversation_summary": None,
            "intent": None, "entities": None, "task_plan": None,
            "customer_context": {}, "financial_context": {}, "case_context": {},
            "tool_results": [], "agent_results": [],
            "pending_action": None, "approval_state": None,
            "response": None, "errors": [],
            "task_id": None, "parent_task_id": None,
            "requesting_agent": None, "delegated_agent": None,
        }

        print("Agent: ", end="", flush=True)
        try:
            result = await graph.ainvoke(state, config=config)
            resp = result.get("response") or {}
            print(resp.get("message", "(no response)"))
            if resp.get("case_id"):
                print(f"  → Case: {resp['case_id']}")
            if resp.get("approval_id"):
                print(f"  → Approval: {resp['approval_id']}")
            if resp.get("escalation_required"):
                print("  → [Human escalation requested]")
        except Exception as exc:
            print(f"(error: {exc})")
        print()


async def main_async(customer_id: str | None, session_id: str) -> None:
    if not customer_id:
        customer_id, _ = await pick_customer()
    await run(customer_id, session_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Customer Representative Agent")
    parser.add_argument("--customer-id", default=None, help="ledgerGuid (prompts if omitted)")
    parser.add_argument("--session-id", default=None, help="Resume a prior session")
    args = parser.parse_args()
    asyncio.run(main_async(args.customer_id, args.session_id or str(uuid.uuid4())))


if __name__ == "__main__":
    main()
