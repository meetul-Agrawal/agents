"""AgentGateway — stub for future multi-agent orchestration.

ponytail: no external agents registered. Returns NOT_AVAILABLE for all.
Add implementations when specialized agents are built.
"""

from __future__ import annotations
import uuid
from app.models.schemas import AgentTask, AgentResult


class AgentGateway:
    def __init__(self):
        # ponytail: registry empty until specialized agents exist
        self._registry: dict[str, dict] = {}

    def register(self, agent_id: str, capabilities: list[str], handler) -> None:
        self._registry[agent_id] = {"capabilities": capabilities, "handler": handler}

    async def discover_agents(self, capability: str) -> list[dict]:
        return [
            {"agent_id": aid, "capabilities": info["capabilities"]}
            for aid, info in self._registry.items()
            if capability in info["capabilities"]
        ]

    async def delegate_task(self, agent_id: str, task: AgentTask) -> AgentResult:
        if agent_id not in self._registry:
            return AgentResult(
                task_id=task.task_id,
                agent_id=agent_id,
                status="FAILED",
                findings={"error": f"Agent '{agent_id}' not registered."},
            )
        handler = self._registry[agent_id]["handler"]
        return await handler(task)

    async def get_task_result(self, task_id: str) -> AgentResult | None:
        # ponytail: no async task tracking yet; add when agents run async
        return None


# Singleton
_gateway = AgentGateway()


def get_gateway() -> AgentGateway:
    return _gateway
