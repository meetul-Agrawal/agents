"""ConversationRepository — persists session/conversation history."""

from __future__ import annotations
from datetime import datetime
from app.db.mongodb import get_db


class ConversationRepository:
    @property
    def col(self):
        return get_db()["conversations"]

    async def upsert_session(self, session_id: str, customer_id: str,
                             messages: list[dict], context: dict) -> None:
        await self.col.update_one(
            {"session_id": session_id},
            {"$set": {
                "customer_id": customer_id,
                "messages": messages[-50:],  # ponytail: keep last 50 only
                "context": context,
                "updated_at": datetime.utcnow(),
            }, "$setOnInsert": {"created_at": datetime.utcnow()}},
            upsert=True,
        )

    async def load_session(self, session_id: str) -> dict | None:
        return await self.col.find_one({"session_id": session_id}, {"_id": 0})

    async def get_customer_sessions(self, customer_id: str, limit: int = 10) -> list[dict]:
        cursor = self.col.find(
            {"customer_id": customer_id}, {"_id": 0, "session_id": 1, "updated_at": 1}
        ).sort("updated_at", -1).limit(limit)
        return await cursor.to_list(limit)
