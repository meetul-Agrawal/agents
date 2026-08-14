#!/usr/bin/env python
"""Run an eval suite and compare it against the stored baseline.

    uv run scripts/run_evals.py routing            # run + report
    uv run scripts/run_evals.py routing --accept   # store as the new baseline

Phase 0 has no orchestrator yet, so the system under test is the identity
oracle: this proves the harness, dataset format and regression gate work.
Phase 3 swaps `_oracle` for the real Customer Assist graph.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ca import evals as E  # noqa: E402

REPORTS = Path("evals/reports")
BASELINES = Path("evals/regression")


def _oracle(case: E.EvalCase) -> dict:
    return {"intent": case.expected.get("intent"), "agents": case.expected.get("agents", [])}


def main(argv: list[str]) -> int:
    suite = argv[1] if len(argv) > 1 else "routing"
    accept = "--accept" in argv

    cases = E.load_datasets(Path("evals/datasets") / suite)
    report = E.run_suite(suite, cases, _oracle, [E.exact_match("intent"), E.agent_set()])
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


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
