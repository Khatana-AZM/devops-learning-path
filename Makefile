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

KEYS_DIR := keys
PRIVATE_KEY := $(KEYS_DIR)/jwt-private.pem
PUBLIC_KEY := $(KEYS_DIR)/jwt-public.pem

.DEFAULT_GOAL := help
.PHONY: help build run up down clean restart ps logs config \
        db db-logs db-shell db-reset \
        api api-logs api-shell api-restart api-build smoke \
        venv lint fmt test testdb \
        migrate migration migrate-down migrate-history migrate-current migrate-sql \
        keys keys-show

# ---------------------------------------------------------------- meta ------

help: ## Show this help
	@echo "Targets (context: $(DOCKER_CONTEXT))"
	@echo
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

config: ## Render the fully-resolved compose file (debug env substitution)
	$(COMPOSE) config

# --------------------------------------------------------------- stack ------

run: keys up migrate ## Generate keys if needed, build, start the stack, migrate
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

smoke: ## Curl the health endpoints of the running stack
	@curl -fsS http://localhost:8000/health     && echo
	@curl -fsS http://localhost:8000/health/db  && echo

# ------------------------------------------------------- lint and tests ----
# These run on the HOST, not in a container: fast feedback, and it's exactly
# what CI does (a runner with python + a Postgres service container).

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

$(VENV)/bin/activate: requirements-dev.txt requirements.txt
	python3 -m venv $(VENV)
	@$(PIP) install -q --upgrade pip
	@$(PIP) install -q -r requirements-dev.txt
	@touch $(VENV)/bin/activate

venv: $(VENV)/bin/activate ## Create the dev virtualenv and install dev deps

lint: venv ## Lint with ruff (same command CI runs)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .

fmt: venv ## Auto-fix formatting and import order
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/ruff format .

testdb: ## Create the test database if it isn't there yet
	@$(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-app} -d postgres \
		-tc "SELECT 1 FROM pg_database WHERE datname='appdb_test'" \
		| grep -q 1 || $(COMPOSE) exec -T db createdb -U $${POSTGRES_USER:-app} appdb_test
	@echo "appdb_test ready"

test: venv testdb ## Run the pytest suite against appdb_test
	POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_DB=appdb_test \
		$(VENV)/bin/pytest

# ---------------------------------------------------------------- keys ------

keys: $(PRIVATE_KEY) ## Generate the RS256 keypair if it doesn't exist (idempotent)

$(PRIVATE_KEY):
	@mkdir -p $(KEYS_DIR)
	@openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out $(PRIVATE_KEY)
	@openssl rsa -in $(PRIVATE_KEY) -pubout -out $(PUBLIC_KEY)
	@chmod 600 $(PRIVATE_KEY)
	@chmod 644 $(PUBLIC_KEY)
	@echo "Generated $(PRIVATE_KEY) (0600) and $(PUBLIC_KEY) (0644)"
	@echo "These are gitignored. Never commit the private key."

keys-show: ## Print the public key (safe to share; verifiers only need this)
	@cat $(PUBLIC_KEY)

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
