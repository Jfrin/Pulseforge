COMPOSE=docker compose -f infra/docker-compose.yml
TEST=$(COMPOSE) -f infra/docker-compose.test.yml

.PHONY: up demo test
up:        ## start the stack (currently: connector demo)
	$(COMPOSE) up --build
demo:      ## run the connector demo once
	$(COMPOSE) run --rm --build source-connectors
test:      ## run unit tests inside Docker
	$(TEST) run --rm --build source-connectors-test
