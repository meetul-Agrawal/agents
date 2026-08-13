import motor.motor_asyncio
from app.config import get_settings

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None


def get_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = motor.motor_asyncio.AsyncIOMotorClient(s.mongodb_uri)
    return _client


def get_db():
    return get_client()[get_settings().mongodb_database]
