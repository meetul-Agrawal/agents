"""Phase 6 — SA-9, the approval verifier.

Runs right after SA-4 raises a draft approval request, and checks that draft
(type, amount, summary) against what the customer actually asked for, before
a human reviewer ever sees it. A miss feeds back into one redraft, up to
`sa4_approval.MAX_VERIFY_ATTEMPTS` — either way, the approval was already
`pending` the moment SA-4 raised it (see `sa4_approval.py`). This agent only
ever improves what the reviewer sees; it never gates whether they see it, and
it never decides the approval itself.

Wired as SA-4's dependent, not by its own intent: no intent maps to
`sa9_verifier` in `INTENT_AGENT`, so `orchestrator.create_plan` appends this
task manually, right after SA-4's, with `depends_on=[sa4_task_id]`,  and
`orchestrator.execute` injects SA-4's own `AgentResult` into
`task.inputs["dependency_result"]` before running it — the one thing this
agent needs that its own tool reads can't hand back on their own (the
approval id SA-4 just raised).
"""

from __future__ import annotations

from typing import Any

from . import customer360 as c3
from . import sa4_approval
from . import services
from .contracts import AgentResult, AgentTask, CustomerAssistState, ProposedAction, ToolCall
from .sa1_general import _read


def _find_approval(cid: str, calls: list[ToolCall], approval_id: str) -> dict[str, Any] | None:
    rows = _read(calls, "get_approvals", lambda: c3.get_approvals(cid), customer_id=cid) or []
    for row in rows:
        if row.get("approval_id") == approval_id:
            return row
    return None


def run(task: AgentTask, state: CustomerAssistState) -> AgentResult:
    calls: list[ToolCall] = []

    def result(status: str, summary: str, actions: list[ProposedAction] | None = None) -> AgentResult:
        return AgentResult(agent="sa9_verifier", agent_task_id=task.agent_task_id, status=status,
                           summary=summary, tool_calls=calls, actions=actions or [])

    approval_result: AgentResult | None = task.inputs.get("dependency_result")
    if approval_result is None or approval_result.status != "needs_approval" or not state.customer_id:
        return result("completed", "nothing to verify")

    approval_id = next(
        (a.payload.get("approval_id") for a in approval_result.actions if a.type == "create_approval"), None,
    )
    if not approval_id:
        return result("completed", "no approval id on the dependency result")

    cid = state.customer_id
    row = _find_approval(cid, calls, approval_id)
    if row is None:
        return result("completed", f"approval {approval_id} not found on account")

    approval_type = row.get("type", "")
    amount = row.get("amount")
    summary = row.get("summary") or ""

    verified, feedback, attempts = False, "", 0
    for attempts in range(1, sa4_approval.MAX_VERIFY_ATTEMPTS + 1):
        verified, feedback = sa4_approval._verify(state.message, approval_type, amount, summary)
        if verified:
            break
        summary = sa4_approval._summarize(approval_type, amount, feedback=feedback)

    context = {**(row.get("context") or {}), "verified": verified, "verify_attempts": attempts}
    if not verified:
        context["verify_feedback"] = feedback
    services.update_approval_draft(approval_id, summary=summary, context=context)
    calls.append(ToolCall(tool="update_approval_draft", arguments={"approval_id": approval_id, "verified": verified}))

    return result(
        "completed",
        (f"approval {approval_id} verified after {attempts} attempt(s)" if verified
         else f"approval {approval_id} unverified after {attempts} attempts"),
        actions=[ProposedAction(type="update_approval_draft", mode="auto", executed=True,
                                payload={"approval_id": approval_id, "verified": verified})],
    )
