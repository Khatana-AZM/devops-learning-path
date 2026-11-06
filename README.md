# devops-learning-path

FastAPI + Postgres, containerised. API only, no frontend.

## Layout

```
app/
  main.py        app factory, lifespan, router wiring
  config.py      env-driven settings (pydantic-settings)
  database.py    engine, SessionLocal, Base, get_db dependency
  models.py      ORM models            <- CRUD resource goes here
  schemas.py     pydantic in/out models <- and here
  routers/
    health.py    /health, /health/db
migrations/      alembic revisions
  env.py         reads DB url from app settings
  versions/      generated revision files
Makefile
Dockerfile
docker-compose.yml
alembic.ini
```

## Run

```bash
cp .env.example .env     # first time only
make run                 # build + start + migrate
```

`make` with no target lists everything. The common ones:

| | |
|---|---|
| `make run` | build, start the stack, apply migrations |
| `make down` / `make clean` | stop (keep data) / stop and wipe the DB volume |
| `make logs` / `make api-logs` | tail everything / just the API |
| `make test` | curl the health endpoints |
| `make db` / `make api` | start just Postgres / just the server |
| `make db-shell` / `make api-shell` | psql prompt / bash in the container |
| `make migrate` | apply pending migrations |
| `make migration m="add users"` | autogenerate a revision from model changes |
| `make migrate-down` | roll back one revision |
| `make migrate-current` / `make migrate-history` | where the DB is / full history |

Raw compose still works if you prefer it:

```bash
docker compose up -d --build
```

- API:  http://localhost:8000
- Docs: http://localhost:8000/docs
- DB:   localhost:5432 (user/pass/db from `.env`)

```bash
docker compose logs -f api
docker compose down        # stop
docker compose down -v     # stop and wipe the pgdata volume
```

## Notes

- `app/` is bind-mounted read-only into the container and uvicorn runs with
  `--reload`, so edits on the host take effect immediately. The image still
  contains a full copy of the code, so it runs standalone without compose.
- Schema is owned by Alembic, not `create_all()`. Add a model to `app/models.py`,
  then `make migration m="..."` and `make migrate`. Revision files are generated
  inside the container but land in `migrations/versions/` on the host, owned by
  you -- the container's `appuser` is uid 1000 to make that work.
- The container runs as non-root (`appuser`, uid 1000).
- `api` waits on the `db` healthcheck (`pg_isready`), not just on the container
  starting, so there's no connect-on-boot race.
- The Makefile sets `DOCKER_CONTEXT=default` (the system dockerd) because Docker
  Desktop is currently crashing on this machine. Override with
  `make run DOCKER_CONTEXT=desktop-linux` once that's fixed.
