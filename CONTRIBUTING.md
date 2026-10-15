# Contributing to PulseForge

## Setup
Everything runs in Docker. You need Docker and Docker Compose, nothing else.

```bash
cp .env.example .env
docker compose up -d --build     # whole stack
docker compose logs -f source-connectors
```

## Tests
```bash
make test        # or the two `docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm --build <service>-test` commands
```

## Lint, format, types (run inside a service folder)
```bash
pip install -r requirements.txt -r requirements-test.txt
ruff check . --fix && ruff format . && mypy
```
CI runs the same checks and blocks the merge if any fail.

## Workflow
1. Pick an unticked item in `CHECKLIST.md` (say which in the issue or PR).
2. Branch from `main`: `feat/<short-name>`.
3. Keep PRs small: one checklist item, tests included.
4. Open a PR; CI must be green and one person must review.

## Rules of the house
- Each service owns its database. No service reads another service's tables.
- Services talk through events or APIs only.
- Governance decisions are flagged and logged, never silently dropped.
