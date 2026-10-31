# PulseForge implementation plan

## 1. Core platform

### Source connectors
- [x] `SourceConnector` interface (`fetch(cursor) -> Page`)
- [x] Cursor-paginated polling with durable transactional Postgres checkpoints
- [x] CSV directory import connector
- [x] Real Steam connector: reviews, metadata, and official player-count API
- [x] Signed webhook receiver, resumable SSE and websocket wrappers
- [x] Idempotent ingestion keyed on `(source, external_id, payload_hash)`
- [x] Durable micro-batching (500 records or 5 minutes), emits `BatchCreated`
- [x] Raw-response bronze landing in MinIO-compatible object storage
- [x] Faker mock source with pagination, duplicate/error injection and review spikes

### Processing
- [x] Validation and normalization with retained, replayable rejects
- [x] Persistent cross-source entity resolution for editions, DLC, bundles and studio-name noise
- [x] Source-scoped HMAC reviewer pseudonymization before silver
- [x] Explainable controversy scoring: review spikes, negative swings, player drops, price and patch events
- [x] Live transactional consumer emits `ProductsScored`

### Enrichment orchestrator
- [x] Cheapest-first waterfall routing; only missing fields cascade
- [x] Three adapters behind one interface (A, B, and the planned keyed C fixture stub)
- [x] Per-field confidence based on agreement and recency, with source lineage
- [x] API calls saved, actual calls, cache hits and hit-rate metrics
- [x] Redis-backed circuit breakers with cooldown and recovery
- [x] Bounded retries with exponential backoff and jitter
- [x] Per-tier token buckets and persisted `Retry-After` pauses
- [x] Transactional event idempotency and request-result caching
- [x] Durable dead-letter queue with targeted replay tooling
- [x] Backpressure pauses page fetches while downstream queues are congested
- [x] All-source outages pause batches until enrichment recovers

### Governance
- [x] Review-content retention, bronze lifecycle expiration and delivered-event retention
- [x] Adult-content flags
- [x] Versioned per-source terms policies
- [x] Product/source/reviewer suppression lists
- [x] Flag-and-tombstone decisions, including erasure and resolution redirects
- [x] Timestamped append-only audit log enforced against SQL mutation
- [x] Scheduled 30-day suppression re-checks and expiry/policy-change sweeps

### Export
- [x] Standardized object names and explicit, shared CSV/Parquet column mapping
- [x] Aggregate-only gold files with governance decisions and bronze/batch/field lineage
- [x] Exported JSON alert feed

### Analytics and alerts
- [x] Durable pipeline, source, enrichment and per-product metrics
- [x] Configurable rolling-window review-bomb detector with minimum counts, baseline and cooldown
- [x] Source-failure, duplicate-spike, consumer-lag, hit-rate, circuit and pipeline-failure alerts
- [x] Slack and SMTP notification delivery with persistent delivery history

### Core service integration
- [x] RabbitMQ topology, publisher-confirmed outbox relays and idempotent consumers
- [x] Separate service-owned Postgres databases, Redis and object storage wired in Compose
- [x] Runtime images and dependency/readiness health checks
- [x] Core unit/integration tests and isolated end-to-end CI workflow

## Verification

Verified on 2026-10-31:

| Check | Result |
|---|---|
| Source connectors (including Postgres integration) | 173 passed; 74.44% service coverage |
| Processing | 177 passed; 87.91% service coverage |
| Mock source | 24 passed |
| Shared core (including real Postgres, Redis and RabbitMQ) | 27 passed |
| Ruff lint and formatting | Passed across 111 Python files |
| Mypy | Passed for connectors, processing, mock source and shared core |
| Docker Compose build/readiness | All core services healthy |
| End-to-end acceptance | Bronze lineage → audited Parquet → review-bomb alert → alert export and notification history |

The end-to-end run used the isolated eight-game mock stack. The external Steam APIs and real
Slack/SMTP accounts are covered by request-contract tests rather than live account calls.

Run `make test`, `make lint`, and `make e2e`. Configuration, API endpoints, adapter behavior,
retention, replay instructions and runtime limits are documented in [core-platform.md](docs/core-platform.md).
