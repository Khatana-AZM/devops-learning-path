# devops-learning-path

FastAPI + Postgres, containerised. API only, no frontend.

## Layout

```
app/
  main.py        app factory, lifespan, router wiring
  config.py      env-driven settings (pydantic-settings)
  database.py    engine, SessionLocal, Base, get_db dependency
  models.py      ORM models: User, RefreshToken, Task
  schemas.py     pydantic in/out models
  security.py    bcrypt hashing + RS256 sign/verify + opaque token minting
  tokens.py      refresh-token lifecycle: issue / rotate / revoke
  dependencies.py  get_current_user / get_current_active_user
  routers/
    health.py    /health, /health/db
    auth.py      /auth/register, /auth/login, /auth/refresh,
                 /auth/logout, /auth/logout-all, /auth/me
    tasks.py     owner-scoped CRUD for /tasks
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
| `make keys` | generate the RS256 keypair (idempotent) |
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

## Tasks API

One table, full CRUD, every route authenticated, and you can only ever touch
your own rows.

| method | path | notes |
|---|---|---|
| POST | `/tasks` | 201; `user_id` comes from the token, never the body |
| GET | `/tasks` | `?is_done=`, `?due_before=`, `?limit=` (max 100), `?offset=` |
| GET | `/tasks/{id}` | 404 if not yours |
| PATCH | `/tasks/{id}` | partial; 400 on an empty body |
| DELETE | `/tasks/{id}` | 204 |

```bash
ACCESS=$(curl -s -X POST localhost:8000/auth/login \
  -d 'username=you@example.com&password=...' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -X POST localhost:8000/tasks \
  -H "Authorization: Bearer $ACCESS" -H 'Content-Type: application/json' \
  -d '{"title":"write the runbook","due_date":"2026-12-01"}'

curl "localhost:8000/tasks?is_done=false&limit=10" -H "Authorization: Bearer $ACCESS"
```

### How ownership is enforced

Two rules, both in `app/routers/tasks.py`:

**1. The owner check lives in the WHERE clause, not in an `if` after loading.**

```python
select(Task).where(Task.id == task_id, Task.user_id == user.id)
```

A filter that's part of the query can't be forgotten halfway down a function
or skipped by an early return. Fetch-then-check is how IDOR bugs happen.

**2. Someone else's row returns 404, not 403.**

403 confirms the row exists, so anyone could walk `/tasks/1`, `/tasks/2`, ...
and learn how many tasks the system holds and which ids are real. 404 leaks
nothing. Verified: a second user gets 404 on GET, PATCH and DELETE of another
user's task, and an empty list from `GET /tasks`.

### PATCH semantics

`TaskUpdate` has all-optional fields and the router uses
`model_dump(exclude_unset=True)`, which distinguishes *"field absent"* from
*"field explicitly null"*. So `{"is_done": true}` leaves `description` alone,
while `{"due_date": null}` actually clears it. Without `exclude_unset` every
PATCH would silently wipe the fields it didn't mention.

### Pagination ordering

`ORDER BY created_at DESC, id DESC` -- the `id` tiebreaker matters. Rows
sharing a `created_at` have no stable order without it, so a client paging
through the list can silently skip or repeat entries between pages.

## Auth

JWT bearer tokens signed with **RS256** (RSA + SHA-256, asymmetric).

```bash
make keys    # once -- writes keys/jwt-{private,public}.pem, gitignored

# register
curl -X POST localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"at least 8 chars"}'

# login -> access_token (JWT, 30 min) + refresh_token (opaque, 30 days)
curl -X POST localhost:8000/auth/login \
  -d 'username=you@example.com&password=at least 8 chars'

# use it
curl localhost:8000/auth/me -H "Authorization: Bearer $ACCESS"

# when the access token expires -> 401 with
#   WWW-Authenticate: Bearer error="invalid_token", error_description="..."
curl -X POST localhost:8000/auth/refresh \
  -H 'Content-Type: application/json' -d "{\"refresh_token\":\"$REFRESH\"}"

# sign out this device / every device
curl -X POST localhost:8000/auth/logout \
  -H 'Content-Type: application/json' -d "{\"refresh_token\":\"$REFRESH\"}"
curl -X POST localhost:8000/auth/logout-all -H "Authorization: Bearer $ACCESS"
```

### Two token types, on purpose

| | access | refresh |
|---|---|---|
| format | JWT, RS256 | opaque random string |
| lifetime | 30 min | 30 days |
| state | none — verified by signature | row in `refresh_tokens` |
| revocable | no | yes |
| sent on | every request | only to `/auth/refresh` |

The refresh token is opaque because it carries no claims — it's a lookup key.
Making it a JWT would add a forgeable surface and, being stateless, would make
it unrevocable, which is the entire reason it exists.

Only its SHA-256 is stored. Not bcrypt: bcrypt is slow by design to protect
low-entropy human passwords from offline brute force, but a refresh token is
256 random bits, and a salted hash can't be looked up by index.

### Rotation and reuse detection

Every `/auth/refresh` invalidates the token it was given and issues a new one.
All tokens descended from one login share a `family_id`.

If an already-rotated token is presented again, that means two parties hold the
same token — the legitimate client and a thief. The server can't tell which is
which, so it assumes theft and **revokes the entire family**, forcing a real
login. Verified end to end: replaying a retired token kills its live successor
(`revoked_reason = 'reuse_detected'`).

The tradeoff: a client that genuinely double-fires a refresh (retry, double
tap) gets logged out. Serialise refresh calls client-side.

Rotation claims the token with a conditional `UPDATE ... WHERE revoked_at IS
NULL RETURNING id`, so two concurrent refreshes can't both succeed.

### Known gap: logout doesn't kill the access token

`/auth/logout` revokes refresh tokens, but an access token already issued stays
valid until it expires (≤30 min). That's inherent to stateless JWTs. If you
need instant kill, you need a revocation list checked per request — at which
point ask whether stateless tokens are buying you anything. A shorter access
TTL narrows the window.

In Swagger (`/docs`), the **Authorize** button drives the same flow.

Protect any endpoint by adding the dependency:

```python
from app.dependencies import get_current_active_user

@router.get("/things")
def list_things(user: User = Depends(get_current_active_user)):
    ...
```

### Why RS256 and not HS256

HS256 uses one shared secret for both signing and verifying, so everything that
needs to *check* a token can also *mint* one. RS256 splits that: this service
holds the private key, and anything that only validates tokens (an API gateway,
another service) gets the public key, which is safe to publish. Rotating the
keypair doesn't require redistributing a secret.

### Security properties worth not breaking

- `algorithms=[...]` is pinned in `decode_access_token`. Without it, a token
  with `alg: none`, or one signed HS256 using the *public* key as the HMAC
  secret, verifies successfully and forges any identity.
- `iss`, `aud`, `exp`, `nbf`, `iat` are all required and verified, so a token
  minted for another service can't be replayed here.
- Login returns one identical error for unknown-email and wrong-password, and
  spends equal CPU in both cases, so the endpoint can't enumerate accounts.
- Passwords are capped at 72 bytes in the schema because bcrypt silently
  ignores anything beyond that.
- `keys/` is gitignored and `.dockerignore`d; the private key is `0600` and
  mounted read-only at runtime rather than baked into the image.

### Not done yet

No roles/scopes, no login rate limiting, no key rotation, no pruning of expired
`refresh_tokens` rows (they accumulate; a periodic `DELETE WHERE expires_at <
now() - interval '30 days'` handles it). Lifetimes are
`ACCESS_TOKEN_EXPIRE_MINUTES` and `REFRESH_TOKEN_EXPIRE_DAYS`.

## Notes

- `app/` is bind-mounted read-only into the container and uvicorn runs with
  `--reload`, so edits on the host take effect immediately. The image still
  contains a full copy of the code, so it runs standalone without compose.
- Schema is owned by Alembic, not `create_all()`. Add a model to `app/models.py`,
  then `make migration m="..."` and `make migrate`. Revision files are generated
  inside the container but land in `migrations/versions/` on the host, owned by
  you -- the container's `appuser` is uid 1000 to make that work.
- The container runs as non-root (`appuser`, uid 1000).
- `make run` generates the keypair if missing, so a fresh clone is one command.
- `api` waits on the `db` healthcheck (`pg_isready`), not just on the container
  starting, so there's no connect-on-boot race.
- The Makefile sets `DOCKER_CONTEXT=default` (the system dockerd) because Docker
  Desktop is currently crashing on this machine. Override with
  `make run DOCKER_CONTEXT=desktop-linux` once that's fixed.
