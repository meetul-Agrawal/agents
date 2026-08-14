"""Configuration + Mongo access.

Hard rule for this project: the Tally tenant database is READ-ONLY and no new
collections may be created in it. All state the platform writes (conversations,
events, cases, approvals, promises, health scores, agent runs) goes to a
separate application database on the same mongod.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.database import Database

load_dotenv()

# Collections that exist in the Tally tenant DB. Anything else is a bug.
TENANT_COLLECTIONS = frozenset(
    {
        "companies",
        "costCenters",
        "currencies",
        "forecastEntries",
        "forecasts",
        "godowns",
        "groups",
        "ledgers",
        "stockCategories",
        "stockGroups",
        "stockItems",
        "units",
        "voucherTypes",
        "vouchers",
    }
)

# Customers live in `ledgers` under this group path; vouchers join to them by
# ledgerName (ledgerId is null in every voucher document).
CUSTOMER_GROUP_PATH_RE = r"Sundry Debtors"


class Settings(BaseModel):
    mongo_url: str = os.getenv(
        "MONGO_URL", "mongodb://admin:One4allAllFor1@127.0.0.1:27017/admin"
    )
    tenant_db: str = os.getenv("TENANT_DB", "sf_tenant_6a33b5b2091da2fb4a7c3de4")
    app_db: str = os.getenv("APP_DB", "customer_assist")


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def _client() -> MongoClient:
    return MongoClient(settings().mongo_url, serverSelectionTimeoutMS=5000)


class ReadOnlyDatabaseError(RuntimeError):
    """Raised on any attempt to write to (or invent a collection in) the tenant DB."""


class ReadOnlyDatabase:
    """Thin guard over the tenant DB: known collections only, reads only.

    ponytail: attribute allow-list, not a full pymongo proxy. If a read helper
    ever needs another method, add its name to _READS.
    """

    _READS = frozenset(
        {
            "find",
            "find_one",
            "aggregate",
            "count_documents",
            "estimated_document_count",
            "distinct",
            "list_indexes",
        }
    )

    def __init__(self, db: Database):
        self._db = db

    def __getitem__(self, name: str) -> "_ReadOnlyCollection":
        if name not in TENANT_COLLECTIONS:
            raise ReadOnlyDatabaseError(
                f"{name!r} is not an existing tenant collection; "
                "new collections must go to the app database"
            )
        return _ReadOnlyCollection(self._db[name])

    @property
    def name(self) -> str:
        return self._db.name


class _ReadOnlyCollection:
    def __init__(self, coll):
        self._coll = coll

    def __getattr__(self, item: str):
        if item not in ReadOnlyDatabase._READS:
            raise ReadOnlyDatabaseError(
                f"{item!r} is not permitted on the read-only tenant database"
            )
        return getattr(self._coll, item)


def tenant_db() -> ReadOnlyDatabase:
    """Tally data. Read-only, existing collections only."""
    return ReadOnlyDatabase(_client()[settings().tenant_db])


def app_db() -> Database:
    """Everything Customer Assist writes. Separate database, fully writable."""
    return _client()[settings().app_db]
