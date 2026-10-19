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

lint:      ## ruff, format check and mypy for every service (what CI runs)
	$(TEST) run --rm source-connectors-test sh -c "ruff check . && ruff format --check . && mypy"
	$(TEST) run --rm mock-source-test sh -c "ruff check . && ruff format --check . && mypy"
	$(TEST) run --rm processing-test sh -c "ruff check . && ruff format --check . && mypy"

evaluate:  ## entity-resolution precision/recall against the mock catalog
	$(COMPOSE) --profile tools run --rm --build processing python -m app.evaluate
