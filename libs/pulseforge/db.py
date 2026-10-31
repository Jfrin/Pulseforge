from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    namespace text NOT NULL, key text NOT NULL, value jsonb NOT NULL,
    PRIMARY KEY (namespace, key)
);
CREATE TABLE IF NOT EXISTS consumed (id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS outbox (
    id bigserial PRIMARY KEY, event_type text NOT NULL, source text NOT NULL,
    payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unpublished ON outbox(id) WHERE published_at IS NULL;
CREATE TABLE IF NOT EXISTS failures (id text PRIMARY KEY, attempts int NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS dead_letters (
    id text PRIMARY KEY, event jsonb NOT NULL, reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(), replayed_at timestamptz
);
CREATE TABLE IF NOT EXISTS audit (
    id bigserial PRIMARY KEY, record_id text NOT NULL, decision jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit is append-only'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS audit_immutable ON audit;
CREATE TRIGGER audit_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON audit
FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation();
"""


class Database:
    def __init__(self, url: str):
        self.pool = AsyncConnectionPool(url, min_size=1, max_size=6, open=False)

    async def open(self):
        await self.pool.open()
        await self.pool.wait()
        async with self.pool.connection() as conn:
            await conn.execute(SCHEMA)


class Transaction:
    def __init__(self, conn, service: str):
        self.conn = conn
        self.service = service

    async def get(self, namespace: str, key: str, default=None):
        cur = await self.conn.execute("SELECT value FROM state WHERE namespace=%s AND key=%s", (namespace, key))
        row = await cur.fetchone()
        return row[0] if row else default

    async def put(self, namespace: str, key: str, value: Any):
        await self.conn.execute(
            "INSERT INTO state VALUES (%s,%s,%s) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value",
            (namespace, key, Jsonb(value)),
        )

    async def all(self, namespace: str) -> dict:
        cur = await self.conn.execute("SELECT key,value FROM state WHERE namespace=%s ORDER BY key", (namespace,))
        return dict(await cur.fetchall())

    async def emit(self, kind: str, payload: dict):
        await self.conn.execute(
            "INSERT INTO outbox(event_type,source,payload) VALUES (%s,%s,%s)",
            (kind, self.service, Jsonb(payload)),
        )

    async def audit(self, record_id: str, decision: dict) -> int:
        cur = await self.conn.execute(
            "INSERT INTO audit(record_id,decision) VALUES (%s,%s) RETURNING id", (record_id, Jsonb(decision))
        )
        return (await cur.fetchone())[0]
