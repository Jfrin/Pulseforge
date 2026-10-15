COMPOSE=docker compose
TEST=docker compose -f docker-compose.yml -f docker-compose.test.yml

.PHONY: up down demo test
up:        ## start the stack in the background
	$(COMPOSE) up -d --build
down:
	$(COMPOSE) down
demo:      ## run the connector demo once
	$(COMPOSE) run --rm --build source-connectors
test:      ## run all unit tests inside Docker
	$(TEST) run --rm --build source-connectors-test
	$(TEST) run --rm --build mock-source-test
