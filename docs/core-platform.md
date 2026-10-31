# Core platform

## Run

```sh
docker compose up -d --build --wait
```

The default stack runs connectors, their outbox relay, processing, three source adapters,
the enrichment orchestrator, governance, export, analytics, and notification. Each stateful
service owns a separate Postgres instance; the connector relay is part of the connector service
and uses its database. RabbitMQ carries events, Redis stores enrichment rate limits/circuit
state/cache, and MinIO-compatible Silo stores bronze and gold objects.

| Endpoint | Purpose |
|---|---|
| `http://localhost:8081/ready` | Connector readiness |
| `http://localhost:8081/webhooks/{name}` | Signed webhook ingestion |
| `http://localhost:8082/results` | Export manifests (CSV and Parquet object keys) |
| `http://localhost:8083/results` | Alert feed |
| `http://localhost:8083/metrics` | Per-product rolling review metrics and pipeline counters |
| `http://localhost:8084/results` | Notification delivery history |
| `http://localhost:9001` | Object-store console |
| `http://localhost:15672` | Broker console and queue depth |

Every worker also serves `/health`, `/ready`, `/metrics`, `/results`, and `/dead-letters` on
its internal port 8000. The APIs expose each service's own data only.

## Connectors

- Mock catalogs/reviews are enabled by default. CSV files under `imports/` use canonical game columns.
- Set `STEAM_APP_IDS=570,730` to poll Steam reviews, store metadata, and the official
  `ISteamUserStats/GetNumberOfCurrentPlayers/v1/` API. No API key is needed for player counts.
- `WEBHOOK_SOURCES=demo=<secret>` enables signed webhooks; signatures use the existing
  timestamped HMAC contract in `services/source-connectors/app/signing.py`.
- `SSE_SOURCES=demo=https://example/events` uses `Last-Event-ID` to resume.
- `WEBSOCKET_SOURCES=demo=wss://example/events` sends `{"cursor": null}` initially, then the
  last committed ID on reconnect. The server must return JSON `{"id":"1","data":{...}}`
  messages and support that cursor contract.
- Set `ENVELOPE_SOURCES=webhook-demo,sse-demo,websocket-demo` to use the canonical mapper
  for those push sources. Payloads need canonical game fields (`external_id`, `title`, ...)
  or review fields (`review_id`, `game_id`, `reviewer_id`, `voted_up`, `created_at`).
  Add each source to `infra/config/governance.json` as well.

Checkpoints, dedup keys, and staging commit atomically. Batches default to 500 records or
five minutes. Every page fetch waits when broker + unpublished-outbox backlog exceeds
`QUEUE_LAG_THRESHOLD`; polling resumes once the backlog drains.

## Processing and enrichment

Processing persists its first-seen resolver inputs in insertion order so assignments survive
restarts, including edition/DLC/bundle normalization. Late catalog matches retire provisional
review products with redirect tombstones. Reviews use source-scoped HMAC pseudonyms and
drop text before entering silver. Controversy scores include separate, inspectable review-volume,
negative-share, player-drop, price-change, and patch-event contributions; absent baselines
contribute no evidence.

Adapters share `POST /enrich/{a|b|c}` with `{source, external_id, fields}` requests and
`{fields, observed_at, source, cost}` responses:

1. **A:** cheap mock catalog identity/studio fields.
2. **B:** additional mock catalog fields, or Steam metadata/official player counts.
3. **C:** keyed fixture stub, configured in `infra/config/keyed-stub.json` as
   `{"source:external_id": {"publisher": "Example"}}`. It returns no invented fields when empty.

Only products missing required fields advance to another tier. Outputs include missing fields,
per-field source observations, confidence from agreement/recency, actual API calls, cache hits,
and calls saved against the three-calls-per-product baseline. Redis coordinates token buckets,
response caching, circuit cooldowns and `Retry-After`. Transient requests use exponential
backoff and jitter; all-tier outages pause the batch until a source recovers.

## Governance, retention, and exports

Edit `infra/config/governance.json` to configure source terms, retention days, suppression
(product IDs, `source:external_id`, or reviewer pseudonyms), and product erasure. Bump `version`
to re-evaluate stored records on the next scheduler tick. Otherwise active records are checked
against suppression every 30 days, and expired reviews are checked every minute. Blocked
records emit tombstones; every decision gets a timestamped audit row protected against SQL
UPDATE, DELETE and TRUNCATE. Tombstones remain irreversible for erased content.
The governance database retains active reviewer pseudonyms privately for later policy rechecks;
they are removed when reviews expire or are suppressed and never included in outgoing events.
Reviews stored before this behavior was added require re-ingestion to enable pseudonym-based rechecks.

Bronze objects receive a prefix-scoped lifecycle expiration (`BRONZE_RETENTION_DAYS`, default
30); existing unrelated lifecycle rules are preserved. Delivered outbox bodies and drained
webhook inbox bodies expire after `EVENT_RETENTION_DAYS` with audit tombstones. Pending
deliveries are preserved for recovery. Silver review state is bounded to 30 days on batch
processing; analytics retains two detector windows. Validation dead letters retain their original
envelopes for explicit operator replay.

Gold uses an explicit column whitelist: product metadata, review aggregates, scores, confidence,
governance flags/audit ID, and lineage. It contains neither review text nor reviewer IDs.
CSV and Parquet share one schema. Objects use
`gold/products/dt=YYYY-MM-DD/batch-<event-hash>.{csv,parquet}`; alert JSON uses
`gold/alerts/dt=YYYY-MM-DD/<event-hash>.json`. Each event creates a snapshot, including
governance tombstone updates; consumers should use the latest snapshot per product.

## Alerts, notifications, and recovery

The detector defaults to 24 hours, 70% negative reviews, 100 minimum reviews, and a 12-hour
per-product cooldown. It includes the preceding window's baseline. Duplicate deliveries and
review edits update by source/review ID rather than increasing counts. Operational alerts cover
source failures, duplicate spikes, consumer lag, enrichment hit-rate drops, circuit outages, and DLQ failures.

Set `SLACK_WEBHOOK_URL` and/or SMTP variables from `.env.example` to send alerts. Without
channels, notification stores the alert with status `stored`. Network delivery is at least once:
a crash after the remote service accepts a message can produce a repeat. Email uses a stable
Message-ID; delivery history is idempotent in the service database.

Consumer writes, dedup receipts and outgoing events share a transaction. Relays use publisher
confirms and stable event IDs, and resend when confirmation/commit is uncertain. Permanent
failures enter the service's Postgres dead-letter queue after five attempts. Validation rejects
enter processing's DLQ individually without blocking valid records. Inspect `/dead-letters`,
fix the cause, then replay inside the owning service:

```sh
docker compose exec processing python -m pulseforge.replay '<dead-letter-id>'
```

Replay targets only that consumer's queue and resets its retry counter.

## Verify

```sh
make test
make lint
make e2e
```

`make e2e` uses a separate `pulseforge-e2e` Compose project with eight mock base games and
short batching intervals. It checks real bronze lineage, Parquet output, audit IDs, a simulated
review-bomb, the exported alert feed and notification persistence. The core tests additionally
exercise PostgreSQL rollback/idempotency, broker confirmation loss and DLQ replay, Redis
rate-limiting/cache/circuits, policy sweeps, and Slack/SMTP delivery contracts.

The current runtime serializes state transitions per service and rebuilds resolver state from
persisted first-seen inputs. This is intended for the single-host core demo; throughput and
large-catalog scaling are not benchmarked.
