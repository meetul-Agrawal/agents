"""Phase 0 gate: contracts validate, registry holds, eval machinery works."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from ca import contracts as C
from ca import evals as E
from ca import registry as R
from ca.config import ReadOnlyDatabaseError, TENANT_COLLECTIONS

DATASETS = "evals/datasets/routing"


# --------------------------------------------------------------------------
# Unit — ids, schemas, serialization
# --------------------------------------------------------------------------


def test_ids_are_prefixed_and_unique():
    ids = {C.new_id("dispute") for _ in range(500)}
    assert len(ids) == 500
    assert all(C._ID_RE.match(i) and i.startswith("DSP-") for i in ids)


def test_unknown_id_kind_rejected():
    with pytest.raises(ValueError):
        C.new_id("nonsense")


def test_event_roundtrip_serialization():
    ev = C.Event(customer_id="cust-1", type="PAYMENT_PROMISE_CREATED",
                 source="sa2_recovery", payload={"amount": 200000})
    again = C.Event.model_validate(json.loads(ev.model_dump_json()))
    assert again == ev


def test_health_score_change():
    hs = C.HealthScore(customer_id="cust-1", score=72, previous_score=74)
    assert hs.change == -2
    assert C.HealthScore(customer_id="cust-1", score=72).change is None


def test_promise_requires_positive_amount():
    with pytest.raises(ValidationError):
        C.PaymentPromise(customer_id="cust-1", amount=0, due_date=date(2026, 8, 20))


def test_failed_result_requires_error():
    with pytest.raises(ValidationError):
        C.AgentResult(agent="sa1_general", status="failed")
    ok = C.AgentResult(agent="sa1_general", status="failed", error="tool timeout")
    assert ok.error == "tool timeout"


def test_plan_rejects_dangling_dependency():
    t = C.AgentTask(agent="sa6_return", action="validate_return", depends_on=["TSK-2026-deadbeef1234"])
    with pytest.raises(ValidationError):
        C.ExecutionPlan(tasks=[t])


def test_plan_agents_set():
    a = C.AgentTask(agent="sa1_general", action="get_outstanding")
    b = C.AgentTask(agent="sa6_return", action="validate_return", depends_on=[a.agent_task_id])
    assert C.ExecutionPlan(tasks=[a, b]).agents == {"sa1_general", "sa6_return"}


# --------------------------------------------------------------------------
# Negative — unknown agent/tool/status/extra fields
# --------------------------------------------------------------------------


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        C.Customer(customer_id="1", ledger_name="X", company_id="c",
                   display_name="X", surprise=True)


def test_invalid_status_rejected():
    with pytest.raises(ValidationError):
        C.AgentResult(agent="sa1_general", status="kind_of_done")


def test_invalid_event_type_rejected():
    with pytest.raises(ValidationError):
        C.Event(customer_id="c", type="PAYMENT_VIBES", source="sa2_recovery")


def test_unknown_agent_and_tool():
    with pytest.raises(R.UnknownAgentError):
        R.get_agent("sa9_wishful")
    with pytest.raises(R.UnknownToolError):
        R.get_tool("drop_database")


def test_permission_enforced():
    assert R.check_permission("sa2_recovery", "create_payment_promise").access == "write"
    with pytest.raises(R.PermissionDeniedError):
        R.check_permission("sa1_general", "create_credit_note")


def test_high_risk_tools_need_human():
    assert R.action_mode("create_credit_note") == "human_approval"
    assert R.action_mode("get_customer_ledger") == "auto"


def test_tenant_db_guard_blocks_writes_and_new_collections():
    from ca.config import ReadOnlyDatabase

    class _FakeDb:
        name = "sf_tenant_test"

        def __getitem__(self, k):
            return object()

    db = ReadOnlyDatabase(_FakeDb())
    with pytest.raises(ReadOnlyDatabaseError):
        db["conversations"]  # not a Tally collection — must go to the app DB
    with pytest.raises(ReadOnlyDatabaseError):
        db["vouchers"].insert_one
    assert "vouchers" in TENANT_COLLECTIONS


# --------------------------------------------------------------------------
# Integration — dataset loading, graders, runner, report, regression
# --------------------------------------------------------------------------


def test_datasets_load_and_reference_known_agents():
    cases = E.load_datasets(DATASETS)
    assert len(cases) >= 16
    for case in cases:
        for agent in case.expected.get("agents", []):
            assert agent in C.AGENT_NAMES, f"{case.case_id}: unknown agent {agent}"


def test_malformed_case_reports_line(tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"case_id": "A", "input": "hi"}\n{"input": "no id"}\n')
    with pytest.raises(E.MalformedCaseError) as exc:
        E.load_dataset(bad)
    assert "bad.jsonl:2" in str(exc.value)


def test_duplicate_case_id_rejected(tmp_path):
    dup = tmp_path / "dup.jsonl"
    dup.write_text('{"case_id": "A", "input": "x"}\n{"case_id": "A", "input": "y"}\n')
    with pytest.raises(E.MalformedCaseError):
        E.load_dataset(dup)


def test_agent_set_grader_scores_partial_and_unknown():
    g = E.agent_set()
    perfect = g({"agents": ["sa1_general"]}, {"agents": ["sa1_general"]})
    assert perfect.passed and perfect.score == 1.0
    partial = g({"agents": ["sa1_general", "sa6_return"]}, {"agents": ["sa1_general"]})
    assert not partial.passed and 0 < partial.score < 1
    bogus = g({"agents": ["sa1_general"]}, {"agents": ["sa9_wishful"]})
    assert not bogus.passed and bogus.score == 0.0


def test_exact_match_grader_ignores_unasserted_fields():
    g = E.exact_match("intent", "amount")
    assert g({"intent": "payment_promise"}, {"intent": "payment_promise", "amount": 5}).passed
    assert not g({"intent": "dispute"}, {"intent": "payment_promise"}).passed


def test_structured_grader_uses_contracts():
    g = E.structured(C.AgentResult)
    good = {"result": {"agent": "sa1_general", "status": "completed"}}
    assert g({}, good).passed
    assert not g({}, {"result": {"agent": "sa1_general", "status": "vibes"}}).passed
    assert not g({}, {}).passed


def test_safety_grader_catches_unapproved_execution():
    g = E.no_unauthorized_actions()
    unsafe = {"actions": [{"type": "create_credit_note", "mode": "human_approval", "executed": True}]}
    assert not g({}, unsafe).passed
    safe = {"actions": [{"type": "create_credit_note", "mode": "human_approval", "executed": False}]}
    assert g({}, safe).passed


def _oracle(case: E.EvalCase) -> dict:
    """A perfect system under test: echoes what the case expects."""
    return {"intent": case.expected.get("intent"), "agents": case.expected.get("agents", [])}


def test_runner_and_report(tmp_path):
    cases = E.load_dataset(f"{DATASETS}/single_intent.jsonl")
    report = E.run_suite("routing_single", cases, _oracle,
                         [E.exact_match("intent"), E.agent_set()])
    assert report.pass_rate == 1.0
    out = report.save(tmp_path)
    assert json.loads(out.read_text())["passed"] == report.total
    assert "Eval report" in (tmp_path / "routing_single.md").read_text()


def test_runner_survives_a_crashing_agent():
    def boom(case):
        raise RuntimeError("agent exploded")

    report = E.run_suite("crash", E.load_dataset(f"{DATASETS}/multi_intent.jsonl"),
                         boom, [E.exact_match("intent")])
    assert report.pass_rate == 0.0
    assert "agent exploded" in report.failures[0].error
    assert "agent exploded" in report.to_markdown()


def test_regression_detects_only_new_failures():
    cases = E.load_dataset(f"{DATASETS}/single_intent.jsonl")
    graders = [E.exact_match("intent"), E.agent_set()]
    baseline = E.run_suite("routing_single", cases, _oracle, graders).to_dict()

    def broken(case):
        out = _oracle(case)
        if case.case_id == "RT-S-003":
            out["agents"] = ["sa1_general"]
        return out

    regressions = E.compare(baseline, E.run_suite("routing_single", cases, broken, graders))
    assert [r.case_id for r in regressions] == ["RT-S-003"]
    # Fixing it again reports nothing.
    assert E.compare(baseline, E.run_suite("routing_single", cases, _oracle, graders)) == []
