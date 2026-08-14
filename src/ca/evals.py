"""Phase 0 — evaluation foundation.

Dataset loader, graders, runner, regression comparison, report generator.
Deliberately a plain library: no pytest coupling, no LLM dependency, so it runs
in CI and from a script the same way.

A case file is JSONL; one object per line:

    {"case_id": "REC-001", "customer_id": "...", "input": "...",
     "context": {...}, "expected": {"intent": "payment_promise",
     "agents": ["sa2_recovery"], "amount": 200000}}
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .contracts import AGENT_NAMES, Contract, NonEmpty, utcnow


class EvalCase(Contract):
    case_id: NonEmpty
    input: str
    customer_id: str | None = None
    context: dict[str, Any] = {}
    expected: dict[str, Any] = {}
    tags: list[str] = []


class MalformedCaseError(ValueError):
    pass


# --------------------------------------------------------------------------
# Dataset loading
# --------------------------------------------------------------------------


def load_dataset(path: str | Path) -> list[EvalCase]:
    """Parse a .jsonl dataset. Raises MalformedCaseError with the line number."""
    path = Path(path)
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        raw = raw.strip()
        if not raw or raw.startswith("//"):
            continue
        try:
            case = EvalCase(**json.loads(raw))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise MalformedCaseError(f"{path}:{lineno}: {exc}") from exc
        if case.case_id in seen:
            raise MalformedCaseError(f"{path}:{lineno}: duplicate case_id {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return cases


def load_datasets(root: str | Path) -> list[EvalCase]:
    return [c for p in sorted(Path(root).rglob("*.jsonl")) for c in load_dataset(p)]


# --------------------------------------------------------------------------
# Graders
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Grade:
    name: str
    score: float  # 0.0 - 1.0
    passed: bool
    detail: str = ""


# A grader sees (expected, actual) and returns a Grade.
Grader = Callable[[dict[str, Any], dict[str, Any]], Grade]


def exact_match(*fields: str) -> Grader:
    """Every named expected field must equal the actual one. Absent in expected
    means not under test."""

    def grade(expected: dict[str, Any], actual: dict[str, Any]) -> Grade:
        checked = [f for f in fields if f in expected]
        if not checked:
            return Grade("exact_match", 1.0, True, "nothing asserted")
        bad = [f"{f}: expected {expected[f]!r}, got {actual.get(f)!r}"
               for f in checked if expected[f] != actual.get(f)]
        score = 1 - len(bad) / len(checked)
        return Grade("exact_match", score, not bad, "; ".join(bad))

    return grade


def agent_set(field_name: str = "agents") -> Grader:
    """Precision/recall over the selected agent set; F1 is the score."""

    def grade(expected: dict[str, Any], actual: dict[str, Any]) -> Grade:
        if field_name not in expected:
            return Grade("agent_set", 1.0, True, "nothing asserted")
        want = set(expected[field_name])
        got = set(actual.get(field_name, []))
        unknown = (want | got) - AGENT_NAMES
        if unknown:
            return Grade("agent_set", 0.0, False, f"unknown agent(s): {sorted(unknown)}")
        hit = len(want & got)
        precision = hit / len(got) if got else 0.0
        recall = hit / len(want) if want else 1.0
        f1 = 0.0 if not (precision + recall) else 2 * precision * recall / (precision + recall)
        detail = "" if f1 == 1.0 else f"missing={sorted(want - got)} extra={sorted(got - want)}"
        return Grade("agent_set", f1, f1 == 1.0, detail)

    return grade


def structured(model: type[BaseModel], key: str = "result") -> Grader:
    """The actual output must validate against a contract."""

    def grade(expected: dict[str, Any], actual: dict[str, Any]) -> Grade:
        payload = actual.get(key)
        if payload is None:
            return Grade("structured", 0.0, False, f"missing {key!r}")
        try:
            model.model_validate(payload)
        except ValidationError as exc:
            return Grade("structured", 0.0, False, str(exc).replace("\n", " ")[:300])
        return Grade("structured", 1.0, True)

    return grade


def no_unauthorized_actions(field_name: str = "actions") -> Grader:
    """Safety gate: nothing needing human approval may come back executed."""

    def grade(expected: dict[str, Any], actual: dict[str, Any]) -> Grade:
        bad = [
            a for a in actual.get(field_name, [])
            if a.get("mode") == "human_approval" and a.get("executed")
        ]
        return Grade(
            "no_unauthorized_actions",
            0.0 if bad else 1.0,
            not bad,
            f"executed without approval: {[a.get('type') for a in bad]}" if bad else "",
        )

    return grade


def judge(fn: Callable[[dict[str, Any], dict[str, Any]], Grade]) -> Grader:
    """Adapter for an LLM-as-judge grader. Phase 0 ships the seam only — the
    first real judge arrives with the LLM gateway in Phase 3.

    ponytail: no provider wiring here; the NIM key currently 403s on inference,
    and deterministic graders cover the Phase 0 gate.
    """
    return fn


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


@dataclass
class CaseReport:
    case_id: str
    grades: list[Grade]
    error: str | None = None
    actual: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.error is None and all(g.passed for g in self.grades)

    @property
    def score(self) -> float:
        return 0.0 if self.error or not self.grades else sum(
            g.score for g in self.grades
        ) / len(self.grades)


@dataclass
class Report:
    suite: str
    cases: list[CaseReport]
    started_at: str

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(c.passed for c in self.cases)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def failures(self) -> list[CaseReport]:
        return [c for c in self.cases if not c.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "started_at": self.started_at,
            "total": self.total,
            "passed": self.passed,
            "pass_rate": round(self.pass_rate, 4),
            "cases": {
                c.case_id: {
                    "passed": c.passed,
                    "score": round(c.score, 4),
                    "error": c.error,
                    "grades": [
                        {"name": g.name, "score": round(g.score, 4),
                         "passed": g.passed, "detail": g.detail}
                        for g in c.grades
                    ],
                }
                for c in self.cases
            },
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Eval report — {self.suite}",
            "",
            f"- run: {self.started_at}",
            f"- cases: {self.total}",
            f"- passed: {self.passed} ({self.pass_rate:.1%})",
        ]
        if self.failures:
            lines += ["", "## Failures", ""]
            for c in self.failures:
                reason = c.error or "; ".join(
                    f"{g.name}: {g.detail or 'failed'}" for g in c.grades if not g.passed
                )
                lines.append(f"- **{c.case_id}** — {reason}")
        return "\n".join(lines) + "\n"

    def save(self, directory: str | Path = "evals/reports") -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / f"{self.suite}.json"
        out.write_text(json.dumps(self.to_dict(), indent=2))
        (directory / f"{self.suite}.md").write_text(self.to_markdown())
        return out


# What the system under test must look like: case -> actual output dict.
Runnable = Callable[[EvalCase], dict[str, Any]]


def run_suite(
    suite: str,
    cases: Iterable[EvalCase],
    run: Runnable,
    graders: Iterable[Grader],
) -> Report:
    graders = list(graders)
    reports: list[CaseReport] = []
    for case in cases:
        try:
            actual = run(case)
        except Exception as exc:  # a crashing agent is a failed case, not a failed run
            reports.append(CaseReport(case.case_id, [], error=f"{type(exc).__name__}: {exc}"))
            continue
        reports.append(
            CaseReport(case.case_id, [g(case.expected, actual) for g in graders], actual=actual)
        )
    return Report(suite=suite, cases=reports, started_at=utcnow().isoformat())


# --------------------------------------------------------------------------
# Regression
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Regression:
    case_id: str
    was: bool
    now: bool


def compare(baseline: dict[str, Any], current: Report) -> list[Regression]:
    """Cases that passed in the baseline report and fail now. New cases are not
    regressions; fixed cases are not either."""
    old = baseline.get("cases", {})
    new = current.to_dict()["cases"]
    return [
        Regression(cid, True, False)
        for cid, c in new.items()
        if old.get(cid, {}).get("passed") and not c["passed"]
    ]


def load_baseline(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {"cases": {}}


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    for line in Path(path).read_text().splitlines():
        if line.strip():
            yield json.loads(line)
