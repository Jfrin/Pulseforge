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
