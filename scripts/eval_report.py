#!/usr/bin/env python
"""Compare every intent-classifier configuration on the full routing dataset
and write a report.

    uv run scripts/eval_report.py                    # all configs
    uv run scripts/eval_report.py llm-8b llm-70b     # a subset
    uv run scripts/eval_report.py --out Docs/x.md

The question this answers: how much of the LLM's routing accuracy is the model,
and how much is the post-hoc regex guards in `_apply_output_guards`? Each config
is graded on identical cases with identical graders, so the columns are
comparable.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ca import evals as E  # noqa: E402

DATASET = Path("evals/datasets/routing")
DEFAULT_OUT = Path("Docs/03phase3-evaluation.md")

GRADERS = [
    E.exact_match("intent", "requires_human", "order", "clarifies", "asks_identity",
                  "executed_without_approval"),
    E.agent_set(),
    E.numeric("amounts", "quantities"),
    E.exact_match("voucher_numbers"),
]

# Agent selection and the approval gate are what actually route money; grade
# them separately from intent naming, which is cosmetic by comparison.
SAFETY_GRADERS = [E.agent_set(), E.exact_match("requires_human", "executed_without_approval")]


def _summarize(case: E.EvalCase, classifier, run: int = 0) -> dict:
    from ca.orchestrator import handle, summarize

    state = handle(
        case.input,
        customer_id=case.customer_id,
        case_context=case.context,
        message_id=case.case_id,
        classifier=classifier,
        # A repeat must not resume the previous repeat's thread, or it inherits
        # its state and stops being an independent sample.
        thread_id=f"{case.case_id}-run{run}",
    )
    result = summarize(state)
    entities = result.pop("entities", {})
    response = result.get("final_response") or ""
    return {
        **result,
        "voucher_numbers": entities.get("voucher_numbers"),
        "amounts": entities.get("amounts"),
        "quantities": entities.get("quantities"),
        "clarifies": "which one do you mean" in response.lower(),
        "asks_identity": "could not match this message to an account" in response.lower(),
        "executed_without_approval": any(
            a.mode == "human_approval" and a.executed for a in state.completed_actions
        ),
    }


def _llm_classifier(model: str):
    def classify(text, context=None):
        from ca import llm
        from ca.orchestrator import classify_llm

        previous = llm.MODELS["classification"]
        llm.MODELS["classification"] = model
        try:
            return classify_llm(text, context)
        finally:
            llm.MODELS["classification"] = previous

    return classify


FAST = os.getenv("LLM_MODEL_FAST") or os.getenv("NIM_MODEL") or "meta/llama-3.1-8b-instruct"
BIG = os.getenv("LLM_MODEL_REASONING", "meta/llama-3.3-70b-instruct")

CONFIGS: dict[str, tuple[str, object]] = {
    "llm-8b": (f"{FAST}", _llm_classifier(FAST)),
    # Opt-in only: this endpoint serves the 70b at ~48s per call, so a single
    # 128-case config takes ~1.7h. Name it explicitly if you want to pay that.
    "llm-70b": (f"{BIG} (SLOW: ~48s/call on this endpoint)", _llm_classifier(BIG)),
}

DEFAULT_CONFIGS = ["llm-8b"]


def run_config(
    name: str, classifier, cases: list[E.EvalCase], run: int = 0
) -> tuple[E.Report, E.Report, float]:
    started = time.time()
    done = [0]

    def run_one(case: E.EvalCase) -> dict:
        result = _summarize(case, classifier, run)
        done[0] += 1
        if done[0] % 10 == 0:
            elapsed = time.time() - started
            print(f"    {done[0]}/{len(cases)}  {elapsed:.0f}s "
                  f"({elapsed / done[0]:.1f}s/case)", flush=True)
        return result

    full = E.run_suite(name, cases, run_one, GRADERS)
    # Re-grade the same outputs for the safety-only view: no second run, no
    # second bill.
    safety = E.Report(
        suite=f"{name}-safety",
        cases=[
            E.CaseReport(
                case_id=c.case_id,
                grades=[] if c.error else [g(by_id[c.case_id].expected, c.actual)
                                           for g in SAFETY_GRADERS],
                error=c.error,
                actual=c.actual,
            )
            for c in full.cases
            for by_id in [{case.case_id: case for case in cases}]
        ],
        started_at=full.started_at,
    )
    return full, safety, time.time() - started


def tag_breakdown(report: E.Report, cases: list[E.EvalCase]) -> dict[str, tuple[int, int]]:
    by_id = {c.case_id: c for c in cases}
    counts: dict[str, list[int]] = {}
    for case_report in report.cases:
        for tag in by_id[case_report.case_id].tags:
            row = counts.setdefault(tag, [0, 0])
            row[1] += 1
            row[0] += int(case_report.passed)
    return {tag: (passed, total) for tag, (passed, total) in sorted(counts.items())}


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else DEFAULT_OUT
    repeat = int(argv[argv.index("--repeat") + 1]) if "--repeat" in argv else 1
    skip = {str(out), str(repeat)}
    names = [a for a in argv[1:] if not a.startswith("--") and a not in skip]
    names = names or DEFAULT_CONFIGS
    unknown = set(names) - set(CONFIGS)
    if unknown:
        print(f"unknown config(s): {sorted(unknown)}; known: {list(CONFIGS)}")
        return 2

    cases = E.load_datasets(DATASET)
    print(f"{len(cases)} cases, {len(names)} configs\n")

    results: dict[str, tuple[E.Report, E.Report, float]] = {}
    spreads: dict[str, tuple[list[float], list[float]]] = {}
    for name in names:
        description, classifier = CONFIGS[name]
        from ca import llm

        if not llm.available():
            print(f"skipping {name}: no LLM provider configured")
            continue
        print(f"running {name} ({description}) x{repeat} ...", flush=True)
        runs = []
        for attempt in range(repeat):
            if attempt:
                _clear_model_cache()
            runs.append(run_config(name, classifier, cases, attempt))
            full, safety, seconds = runs[-1]
            print(f"  run {attempt + 1}: routing {full.pass_rate:.1%}  "
                  f"safety {safety.pass_rate:.1%}  {seconds:.0f}s", flush=True)
        results[name] = runs[0]
        spreads[name] = ([r[0].pass_rate for r in runs], [r[1].pass_rate for r in runs])
        if repeat > 1:
            routing, safety_rates = spreads[name]
            print(f"  routing mean {sum(routing) / len(routing):.1%} "
                  f"(min {min(routing):.1%}, max {max(routing):.1%}, "
                  f"spread {max(routing) - min(routing):.1%})")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(results, cases, spreads))
    print(f"\nwritten: {out}")
    return 0


def _clear_model_cache() -> None:
    """Each repeat must be an independent sample: the memo would otherwise
    replay the first run's answers and report zero variance."""
    from ca.orchestrator import _understand

    _understand.cache_clear()


def render(
    results: dict[str, tuple[E.Report, E.Report, float]],
    cases: list[E.EvalCase],
    spreads: dict[str, tuple[list[float], list[float]]] | None = None,
) -> str:
    from datetime import datetime, timezone

    by_id = {c.case_id: c for c in cases}
    lines = [
        "# Phase 3 — Routing Evaluation",
        "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by "
        "`uv run scripts/eval_report.py`.",
        "",
        f"**{len(cases)} routing cases** across "
        f"{len({t for c in cases for t in c.tags})} tags. Every configuration is graded on the "
        "same cases with the same graders.",
        "",
        "Two scores per configuration:",
        "",
        "- **Routing** — everything: intent name, agent set, plan order, extracted entities, "
        "clarification behaviour, approval flag.",
        "- **Safety** — agent set plus the approval gate only. This is the score that matters "
        "operationally: a wrong intent *label* is cosmetic, a missed approval is not.",
        "",
        "## Results",
        "",
        "| Configuration | What it is | Routing | Safety | Time | Runs |",
        "|---|---|---|---|---|---|",
    ]
    spreads = spreads or {}
    for name, (full, safety, seconds) in results.items():
        description = CONFIGS[name][0]
        routing_runs, safety_runs = spreads.get(name, ([full.pass_rate], [safety.pass_rate]))
        routing = f"**{sum(routing_runs) / len(routing_runs):.1%}**"
        safety_text = f"**{sum(safety_runs) / len(safety_runs):.1%}**"
        if len(routing_runs) > 1:
            routing += f" ±{(max(routing_runs) - min(routing_runs)) / 2:.1%}"
            safety_text += f" ±{(max(safety_runs) - min(safety_runs)) / 2:.1%}"
        lines.append(
            f"| `{name}` | {description} | {routing} | {safety_text} | "
            f"{seconds:.0f}s | {len(routing_runs)} |"
        )

    lines += ["", "## By category", "", "Pass rate on the full routing score, per tag.", ""]
    tags = sorted({t for c in cases for t in c.tags})
    header = "| Tag | n | " + " | ".join(f"`{n}`" for n in results) + " |"
    lines += [header, "|" + "---|" * (len(results) + 2)]
    breakdowns = {name: tag_breakdown(full, cases) for name, (full, _, _) in results.items()}
    for tag in tags:
        total = sum(1 for c in cases if tag in c.tags)
        cells = []
        for name in results:
            passed, tag_total = breakdowns[name].get(tag, (0, 0))
            cells.append(f"{passed}/{tag_total}" if tag_total else "—")
        lines.append(f"| {tag} | {total} | " + " | ".join(cells) + " |")

    lines += ["", "## Cases every configuration got wrong", ""]
    always_failed = [
        cid for cid in by_id
        if results and all(
            any(c.case_id == cid and not c.passed for c in full.cases)
            for full, _, _ in results.values()
        )
    ]
    if always_failed:
        lines.append("These are dataset or design problems, not model problems.")
        lines.append("")
        for cid in sorted(always_failed):
            lines.append(f"- **{cid}** — {by_id[cid].input!r}")
    else:
        lines.append("None — every case is handled by at least one configuration.")

    for name, (full, _, _) in results.items():
        lines += ["", f"## Failures — `{name}`", ""]
        if not full.failures:
            lines.append("None.")
            continue
        for case_report in full.failures:
            reason = case_report.error or "; ".join(
                f"{g.name}: {g.detail or 'failed'}" for g in case_report.grades if not g.passed
            )
            lines.append(f"- **{case_report.case_id}** {by_id[case_report.case_id].input!r}")
            lines.append(f"  - {reason}")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
