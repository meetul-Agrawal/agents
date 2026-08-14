#!/usr/bin/env python
"""Run an eval suite and compare it against the stored baseline.

    uv run scripts/run_evals.py routing            # run + report
    uv run scripts/run_evals.py customer360        # needs MongoDB
    uv run scripts/run_evals.py all --accept       # store new baselines

Suites:
  routing      — Phase 3 will replace the oracle with the real orchestrator.
                 For now it proves the harness, dataset format and gate work.
  customer360  — real: runs the Phase 1 read services against MongoDB and
                 grades them against golden values produced by an independent
                 implementation (scripts/gen_golden.js).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ca import evals as E  # noqa: E402

REPORTS = Path("evals/reports")
BASELINES = Path("evals/regression")
DATASETS = Path("evals/datasets")


def _routing_oracle(case: E.EvalCase) -> dict:
    return {"intent": case.expected.get("intent"), "agents": case.expected.get("agents", [])}


def _customer360_run(case: E.EvalCase) -> dict:
    from ca import customer360 as c3

    as_of = date.fromisoformat(case.context["as_of"])
    result = c3.get_outstanding(case.customer_id, as_of=as_of)
    return result.model_dump(mode="json")


def _resolution_run(case: E.EvalCase) -> dict:
    from ca import customer360 as c3

    try:
        return {"customer_id": c3.resolve_customer(case.input).customer_id}
    except c3.AmbiguousCustomerError as exc:
        return {"error": "ambiguous", "matches": [m.customer_id for m in exc.matches]}
    except c3.CustomerNotFoundError:
        return {"error": "not_found"}


def _conversation_run(case: E.EvalCase) -> dict:
    """Replay a scenario's deliveries into a scratch database and describe the
    state they produced."""
    from ca import inbox
    from ca.config import _client

    db = _client()["customer_assist_evals"]
    _client().drop_database("customer_assist_evals")
    inbox.ensure_indexes(db)

    conversations, messages, created, threaded, resolved_by = [], [], [], [], []
    for step in case.context["steps"]:
        conversation, message, was_created = inbox.ingest(step["channel"], step["payload"], db=db)
        conversations.append(conversation)
        messages.append(message)
        created.append(was_created)
        threaded.append(message.metadata.get("thread_resolved_by"))
        resolved_by.append(message.metadata.get("resolved_by"))

    ordered = inbox.conversation_messages(conversations[-1].conversation_id, db=db)
    stored = db["conversations"].find_one(
        {"conversation_id": conversations[-1].conversation_id}
    )
    result = {
        "conversations": db["conversations"].count_documents({}),
        "messages": db["messages"].count_documents({}),
        "created": created,
        "thread_resolved_by": threaded,
        "resolved_by": resolved_by,
        "customer_resolved": [m.customer_id is not None for m in messages],
        "customer_ids": [m.customer_id for m in messages],
        "channels": [m.channel for m in messages],
        "conversation_customer_id": stored.get("customer_id"),
        "chronological": [m.timestamp for m in ordered] == sorted(m.timestamp for m in ordered),
        "first_message_text": ordered[0].text if ordered else None,
    }
    _client().drop_database("customer_assist_evals")  # leave no scratch state behind
    return result


SUITES = {
    "routing": (
        _routing_oracle,
        [E.exact_match("intent"), E.agent_set()],
    ),
    "resolution": (
        _resolution_run,
        [E.exact_match("customer_id", "error")],
    ),
    "conversation": (
        _conversation_run,
        [
            E.exact_match(
                "conversations",
                "messages",
                "created",
                "thread_resolved_by",
                "resolved_by",
                "customer_resolved",
                "customer_ids",
                "channels",
                "conversation_customer_id",
                "chronological",
                "first_message_text",
            )
        ],
    ),
    "customer360": (
        _customer360_run,
        [
            E.exact_match("open_bill_count"),
            E.numeric(
                "outstanding",
                "invoiced_total",
                "receipted_total",
                "allocated_total",
                "pre_book_settlements",
                "on_account",
                "advance",
                "ageing",
            ),
        ],
    ),
}


def run(suite: str, accept: bool) -> int:
    run_fn, graders = SUITES[suite]
    report = E.run_suite(suite, E.load_datasets(DATASETS / suite), run_fn, graders)
    report.save(REPORTS)
    print(report.to_markdown())

    baseline_path = BASELINES / f"{suite}.json"
    regressions = E.compare(E.load_baseline(baseline_path), report)
    for r in regressions:
        print(f"REGRESSION: {r.case_id} passed before, fails now")

    if accept:
        BASELINES.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(report.to_dict(), indent=2))
        print(f"baseline updated: {baseline_path}")

    return 1 if regressions or report.failures else 0


def main(argv: list[str]) -> int:
    accept = "--accept" in argv
    names = [a for a in argv[1:] if not a.startswith("-")] or ["all"]
    suites = list(SUITES) if names == ["all"] else names
    unknown = set(suites) - set(SUITES)
    if unknown:
        print(f"unknown suite(s): {sorted(unknown)}; known: {sorted(SUITES)}")
        return 2
    return max(run(s, accept) for s in suites)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
