# ---------------------------------------------------------------------------
# devops-learning-path -- docker/compose shortcuts
#
#   make            show this help
#   make run        build + start everything + apply migrations
#
# DOCKER_CONTEXT defaults to `default` (the system dockerd) because Docker
# Desktop is currently broken on this machine. Override per-invocation:
#   make run DOCKER_CONTEXT=desktop-linux
# ---------------------------------------------------------------------------

DOCKER_CONTEXT ?= default
export DOCKER_CONTEXT

COMPOSE := docker compose
# Alembic runs in a one-off container, so it works even when `api` is down.
# compose honours depends_on, so `db` is started and healthy first.
ALEMBIC := $(COMPOSE) run --rm api alembic

.DEFAULT_GOAL := help
.PHONY: help build run up down clean restart ps logs config \
        db db-logs db-shell db-reset \
        api api-logs api-shell api-restart api-build test \
        migrate migration migrate-down migrate-history migrate-current migrate-sql

# ---------------------------------------------------------------- meta ------

help: ## Show this help
	@echo "Targets (context: $(DOCKER_CONTEXT))"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

config: ## Render the fully-resolved compose file (debug env substitution)
	$(COMPOSE) config

# --------------------------------------------------------------- stack ------

run: up migrate ## Build, start the whole stack, then apply migrations
	@echo
	@echo "  API   -> http://localhost:8000"
	@echo "  Docs  -> http://localhost:8000/docs"

up: ## Build and start all services (detached)
	$(COMPOSE) up -d --build

build: ## Build the api image without starting anything
	$(COMPOSE) build

down: ## Stop and remove containers (Postgres data survives)
	$(COMPOSE) down

clean: ## Stop everything AND delete the pgdata volume (destroys the DB)
	$(COMPOSE) down -v

restart: ## Restart every service
	$(COMPOSE) restart

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Tail logs from all services
	$(COMPOSE) logs -f

# ------------------------------------------------------------ services ------

db: ## Start only Postgres
	$(COMPOSE) up -d db

db-logs: ## Tail Postgres logs
	$(COMPOSE) logs -f db

db-shell: ## Open a psql prompt inside the db container
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-app} -d $${POSTGRES_DB:-appdb}

db-reset: clean db ## Wipe the database volume and start Postgres fresh
	@echo "Database volume destroyed and recreated. Run 'make migrate' next."

# -------------------------------------------------------------- server ------

api: ## Start the API (compose pulls up db first via depends_on)
	$(COMPOSE) up -d api

api-build: ## Rebuild just the api image and recreate the container
	$(COMPOSE) up -d --build api

api-logs: ## Tail API logs
	$(COMPOSE) logs -f api

api-shell: ## Open a shell inside the api container
	$(COMPOSE) exec api bash

api-restart: ## Restart the API container
	$(COMPOSE) restart api

test: ## Hit the health endpoints from the host
	@curl -fsS http://localhost:8000/health     && echo
	@curl -fsS http://localhost:8000/health/db  && echo

# ---------------------------------------------------------- migrations ------

migrate: ## Apply all pending migrations (alembic upgrade head)
	$(ALEMBIC) upgrade head

migration: ## Autogenerate a revision from model changes -- make migration m="add users"
ifndef m
	$(error missing message. usage: make migration m="add users table")
endif
	$(ALEMBIC) revision --autogenerate -m "$(m)"

migrate-down: ## Roll back exactly one migration
	$(ALEMBIC) downgrade -1

migrate-current: ## Show the revision the database is currently on
	$(ALEMBIC) current

migrate-history: ## Show the full revision history
	$(ALEMBIC) history --indicate-current

migrate-sql: ## Print the SQL for pending migrations without running it
	$(ALEMBIC) upgrade head --sql
