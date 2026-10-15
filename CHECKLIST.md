# PulseForge Build Checklist

Tick items by changing `[ ]` to `[x]`. Everything runs on Docker.

---

## 1. Core pipeline

### Source connectors
- [x] `SourceConnector` interface (`fetch(cursor) -> Page`)
- [ ] Cursor-paginated pollers with durable checkpoints (Postgres, same transaction as outbox write)
- [ ] CSV import connector
- [ ] First real connector (official store API)
- [ ] Signed webhook receiver and SSE/websocket wrapper
- [ ] Idempotent upserts keyed on `(source, external_id, payload_hash)`
- [ ] Micro-batching (500 records or 5 min), emits `BatchCreated`
- [ ] Bronze landing of raw responses in MinIO
- [ ] Mock source API (Faker: pagination, errors, duplicates, review spikes, chaos mode)

### Processing
- [ ] Validation and normalization
- [ ] Entity resolution with rapidfuzz (editions, DLC, bundles, dev/publisher names) into a canonical product
- [ ] Reviewer ID pseudonymization (salted hash) on ingest
- [ ] Controversy scoring from stacked signals (review spikes, negative-ratio swings, player drops, price/patch events)
- [ ] Emits `ProductsScored`

### Enrichment orchestrator
- [ ] Waterfall routing (A -> B -> C, cheapest first, cascade only on missing fields)
- [ ] 3 source adapters behind a common interface
- [ ] Per-field confidence scoring (source agreement and recency)
- [ ] "API calls saved" tracking
- [ ] Circuit breakers
- [ ] Retries with exponential backoff and jitter
- [ ] Per-source token bucket (honors `Retry-After`)
- [ ] Idempotency keys
- [ ] Dead-letter queue with replay tool
- [ ] Backpressure (connectors pause when queue lag grows)

### Governance
- [ ] Content-retention limits
- [ ] Adult-content flags
- [ ] Per-source terms policy
- [ ] Suppression list
- [ ] Flag-and-tombstone (never silent deletion)
- [ ] Append-only audit log
- [ ] 30-day suppression re-check job

### Export
- [ ] Naming convention and column mapping
- [ ] Parquet/CSV exports (aggregates only, no review text)
- [ ] Alert feed export
- [ ] Lineage on every gold record

### Analytics and alerts
- [ ] Metrics aggregation
- [ ] Review-bomb detector (`window`, `negative_threshold`, `min_reviews`, `cooldown`, baseline share in alert)
- [ ] Anomaly alerts (source failures, duplicate spikes, hit-rate drops, circuit trips)
- [ ] Notification service (Slack and email)

---

## 2. Platform

- [ ] API gateway (JWT auth, rate limiting, routing)
- [ ] Streamlit dashboard (sources, runs, results, metrics)
- [ ] Outbox pattern
- [ ] Idempotent consumers
- [ ] Saga with compensating states
- [ ] Shared schemas and event contracts in `/libs`
- [ ] One database per service (no cross-service table reads)
- [ ] Bronze / silver / gold data layers
- [ ] OpenTelemetry tracing with correlation IDs
- [ ] Prometheus metrics and Grafana dashboards
- [ ] Structured JSON logs
- [ ] `/health` and `/ready` on every service
- [ ] ADRs 001-008
- [ ] Kubernetes manifests (reference only)
- [ ] Additional sources later (Wikidata, IGDB)

---

## 3. Docker

- [ ] One Dockerfile per service (multi-stage, non-root, slim base)
- [ ] `docker-compose.yml` for the full stack (Postgres per service, Redis, RabbitMQ, MinIO, Prometheus, Grafana)
- [ ] Healthchecks and `depends_on: condition: service_healthy`
- [ ] `.env.example`
- [ ] Separate compose override for tests

---

## 4. Testing

- [ ] Unit tests per service (pytest)
- [ ] Contract tests for event schemas in `/libs`
- [ ] Integration tests with Testcontainers (Postgres, RabbitMQ)
- [ ] E2E tests with the mock source, including a scripted review-bomb
- [ ] Chaos scenarios (source errors, 429s, crash mid-batch, event redelivery)
- [ ] Detector tests (threshold, `min_reviews`, cooldown edge cases)
- [ ] Entity-resolution precision/recall on a hand-labeled sample
- [ ] Locust load test
- [ ] Performance table in README filled in
- [ ] Coverage reporting

---

## 5. CI (GitHub Actions)

- [ ] Per-service workflows with path filters
- [ ] Lint and format (ruff)
- [ ] Type check (mypy)
- [ ] Unit tests on every push/PR
- [ ] Integration and E2E job (compose up in CI, run tests, tear down)
- [ ] Docker image build (optionally push to GHCR)
- [ ] Dependency and image vulnerability scan
- [ ] Coverage gate
- [ ] Status badge in README