"""Self-check for agent_scenarios_100.json.

Validates the 100-case dataset against the REAL vocabulary imported from the
code (intent catalog, agent names) so the eval file can't drift from what the
system actually supports. Run: python evals/validate_agent_scenarios.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ca.contracts import AGENT_NAMES  # noqa: E402
from ca.orchestrator import INTENT_AGENT  # noqa: E402

# Intents the model can name, plus the two the orchestrator injects itself and
# the empty list used for ops-driven (non-inbound) turns.
VALID_INTENTS = set(INTENT_AGENT) | {"ambiguous_reference", "unknown"}
VALID_AGENTS = set(AGENT_NAMES)
VALID_RESPONSE_TYPES = {
    "answer", "ask_followup", "execute_task", "refuse", "clarify", "escalate", "acknowledge",
}
VALID_STATUS = {  # contracts.ResultStatus
    "completed", "needs_information", "needs_agent", "needs_approval", "needs_human", "failed",
}
HUMAN_APPROVAL_INTENTS = {"settlement_request", "credit_note_request"}


def main() -> int:
    path = Path(__file__).resolve().parent / "datasets" / "agent_scenarios_100.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data["cases"]
    problems: list[str] = []
    ids: Counter[str] = Counter()
    intent_cov: Counter[str] = Counter()
    agent_cov: Counter[str] = Counter()

    for case in cases:
        cid = case.get("case_id", "<no id>")
        ids[cid] += 1
        turns = case.get("turns") or []
        if not turns:
            problems.append(f"{cid}: no turns")
        for t in turns:
            exp = t.get("expected") or {}
            where = f"{cid} turn {t.get('turn')}"
            for name in exp.get("intents", []):
                if name not in VALID_INTENTS:
                    problems.append(f"{where}: unknown intent {name!r}")
                intent_cov[name] += 1
            for name in exp.get("agents", []):
                if name not in VALID_AGENTS:
                    problems.append(f"{where}: unknown agent {name!r}")
                agent_cov[name] += 1
            rt = exp.get("response_type")
            if rt not in VALID_RESPONSE_TYPES:
                problems.append(f"{where}: bad response_type {rt!r}")
            st = exp.get("status")
            if st and st not in VALID_STATUS:
                problems.append(f"{where}: bad status {st!r}")
            # requires_human must be true iff a human-approval intent is present.
            named = set(exp.get("intents", []))
            expect_human = bool(named & HUMAN_APPROVAL_INTENTS)
            if named and exp.get("requires_human", False) != expect_human:
                problems.append(
                    f"{where}: requires_human={exp.get('requires_human')} but intents={sorted(named)}"
                )

    dupes = [i for i, n in ids.items() if n > 1]
    if dupes:
        problems.append(f"duplicate case_ids: {dupes}")
    if len(cases) != 100:
        problems.append(f"expected 100 cases, found {len(cases)}")

    # Every routable intent and every sub-agent must appear somewhere.
    missing_intents = set(INTENT_AGENT) - set(intent_cov)
    if missing_intents:
        problems.append(f"intents never exercised: {sorted(missing_intents)}")
    missing_agents = (VALID_AGENTS - {"customer_assist"}) - set(agent_cov)
    if missing_agents:
        problems.append(f"agents never exercised: {sorted(missing_agents)}")

    print(f"cases: {len(cases)}  turns: {sum(len(c['turns']) for c in cases)}")
    print(f"intents covered: {len(intent_cov)}/{len(INTENT_AGENT)}")
    print(f"agents covered: {sorted(agent_cov)}")
    if problems:
        print(f"\nFAIL ({len(problems)} problems):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK — all cases valid, full intent+agent coverage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
