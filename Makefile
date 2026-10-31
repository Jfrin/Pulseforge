COMPOSE=docker compose
TEST=docker compose -f docker-compose.yml -f docker-compose.test.yml

.PHONY: up down demo test logs
up:        ## start the stack in the background
	$(COMPOSE) up -d --build
down:
	$(COMPOSE) down
logs:
	$(COMPOSE) logs -f source-connectors
demo:      ## run the interface demo once
	$(COMPOSE) run --rm --no-deps source-connectors python -m app.demo
test:      ## all unit and integration tests, in Docker
	$(TEST) run --rm --build source-connectors-test
	$(TEST) run --rm --build mock-source-test
	$(TEST) run --rm --build processing-test
	$(TEST) run --rm --build core-test pytest -v tests

lint:      ## ruff, format check and mypy for every service (what CI runs)
	$(TEST) run --rm source-connectors-test sh -c "ruff check . && ruff format --check . && mypy"
	$(TEST) run --rm mock-source-test sh -c "ruff check . && ruff format --check . && mypy"
	$(TEST) run --rm processing-test sh -c "ruff check . && ruff format --check . && mypy"
	$(TEST) run --rm core-test sh -c "ruff check --config /app/pyproject.toml /libs /services tests && ruff format --check --config /app/pyproject.toml /libs /services tests && mypy /libs/pulseforge"

.PHONY: e2e
e2e:       ## isolated real-broker/object-store acceptance test
	docker compose -p pulseforge-e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --wait
	docker compose -p pulseforge-e2e -f docker-compose.yml -f docker-compose.e2e.yml run --rm --build e2e
	docker compose -p pulseforge-e2e -f docker-compose.yml -f docker-compose.e2e.yml down -v

evaluate:  ## entity-resolution precision/recall against the mock catalog
	$(COMPOSE) --profile tools run --rm --build processing python -m app.evaluate
evaluate-hard:   ## entity resolution on the hard mock catalog (resets the mock to hard)
	$(COMPOSE) --profile tools run --rm --build processing python -m app.evaluate --difficulty hard

evaluate-labels: ## entity resolution on the hand-labeled CSV
	$(COMPOSE) --profile tools run --rm --build processing python -m app.evaluate --labels /data/labels.example.csv --examples 20
