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


SUITES = {
    "routing": (
        _routing_oracle,
        [E.exact_match("intent"), E.agent_set()],
    ),
    "resolution": (
        _resolution_run,
        [E.exact_match("customer_id", "error")],
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
