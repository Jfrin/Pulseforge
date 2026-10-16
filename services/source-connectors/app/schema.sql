-- source-connectors owns this database; no other service reads these tables.

CREATE TABLE IF NOT EXISTS checkpoints (
    source          TEXT PRIMARY KEY,
    cursor          TEXT,                       -- NULL = start from the beginning
    last_success_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id           BIGSERIAL PRIMARY KEY,
    event_type   TEXT        NOT NULL,
    source       TEXT        NOT NULL,
    payload      JSONB       NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ                    -- set by the outbox relay (later checklist item)
);

CREATE INDEX IF NOT EXISTS outbox_unpublished ON outbox (id) WHERE published_at IS NULL;

-- Dedup ledger. A record is "new" the first time its (source, external_id, payload_hash)
-- is seen. Same payload re-polled = no-op; changed payload = new hash = new version.
-- Payload bodies live in the outbox events (and later, bronze); this table is keys only.
CREATE TABLE IF NOT EXISTS seen_records (
    source        TEXT        NOT NULL,
    external_id   TEXT        NOT NULL,
    payload_hash  TEXT        NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, external_id, payload_hash)
);
