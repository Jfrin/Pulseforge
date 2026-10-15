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
test:      ## run all unit + integration tests inside Docker
	$(TEST) run --rm --build source-connectors-test
	$(TEST) run --rm --build mock-source-test
