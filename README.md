# devops-learning-path

FastAPI + Postgres, containerised. API only, no frontend.

A task API with JWT authentication, used as the vehicle for building out a
full CI/CD pipeline: lint and tests gating every pull request, migrations and
an image build in the pipeline, and an automated deploy to staging.

---

## Staging

**https://devops-learning-path.vercel.app** · [interactive docs](https://devops-learning-path.vercel.app/docs)

```bash
curl https://devops-learning-path.vercel.app/health      # process is up
curl https://devops-learning-path.vercel.app/health/db   # database reachable
```

> **First request may take 10-20 seconds.** The container scales to zero after
> 5 minutes idle and Neon suspends its compute after 5 minutes. A cold request
> pays both. Subsequent requests are fast. This is the free tier, not a fault.

### Try it in 60 seconds

```bash
BASE=https://devops-learning-path.vercel.app
EMAIL="reviewer-$RANDOM@example.com"

# register + log in
curl -X POST $BASE/auth/register -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"reviewer-password-123\"}"

TOKEN=$(curl -s -X POST $BASE/auth/login \
  -d "username=$EMAIL&password=reviewer-password-123" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# create and list a task
curl -X POST $BASE/tasks -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"first task","due_date":"2027-01-15"}'

curl $BASE/tasks -H "Authorization: Bearer $TOKEN"
```

### Postman collection

One file — [`postman/devops-learning-path.postman_collection.json`](postman/devops-learning-path.postman_collection.json)
— covering every endpoint in **39 requests with 56 assertions**. It points at
staging by default; no environment file to import or select.

In the Postman app: **Import** → that file → **Run collection**.

Headless, no Postman account needed:

```bash
npx newman run postman/devops-learning-path.postman_collection.json \
  --timeout-request 60000
```

To run it against a local stack instead, change the collection variable
`baseUrl` to `http://localhost:8000`.

Last run against staging: **39 requests, 56 assertions, 0 failures, 37s.**

It is not just a list of endpoints — every request asserts its expected status
and the interesting ones assert behaviour:

| folder | what it proves |
|---|---|
| Health | the app is up and can reach Postgres |
| Auth | register, login, token shapes (JWT access, opaque refresh) |
| Tasks (CRUD) | create/read/update/delete, filters, pagination, validation, and that a partial `PATCH` doesn't wipe unmentioned fields |
| Ownership isolation | a second user gets **404** — not 403 — on another user's task, and their list is empty |
| Token lifecycle | refresh rotation, and that replaying a rotated token revokes the whole family |
| Negative cases | no token, garbage token, forged `alg:none`, duplicate registration, wrong password |

Tokens and ids chain automatically between requests, and identities are
generated per run, so it is safe to run repeatedly.

> One result worth reading: the forged `alg:none` request passes on either
> **401** or **403**. The app returns 401 because it pins RS256 when verifying.
> On staging it usually never gets that far — Vercel's WAF rejects the request
> at the edge with 403 and `x-vercel-mitigated: deny`.

There is also [`api.http`](api.http) for VS Code's REST Client extension, if you
prefer staying in the editor. Same coverage, no runner.

Staging holds throwaway data. Register your own account; there is no shared one.

---

## CI/CD pipeline

One workflow, [`.github/workflows/ci.yml`](.github/workflows/ci.yml).

```
                        pull_request → develop      push → develop
                        ──────────────────────      ──────────────
  Lint          ruff check + format               ✓                 ✓
  Test          pytest against real Postgres      ✓                 ✓
  Build image   docker build (no push)            ✓                 ✓
       │
       └─→ Publish image   push to GHCR            skipped          ✓
                │
                └─→ Deploy to staging              skipped          ✓
```

Jobs 1-3 run in parallel. `Publish` requires all three to pass; `Deploy`
requires `Publish`. Nothing reaches the registry or staging unless lint, tests
and the image build are all green.

### What each job does

**Lint** — `ruff check` and `ruff format --check`. Failures appear as inline
annotations on the PR diff.

**Test** — spins up `postgres:16-alpine` as a service container with a
`pg_isready` healthcheck, then:

| step | why it's there |
|---|---|
| `make keys` | generates a throwaway RS256 keypair per run, so CI never holds the real signing key — and so the job still works on pull requests from forks, which are denied repository secrets |
| `alembic upgrade head` | proves the migrations apply to a **genuinely empty** database, catching the migration that only works locally because your dev DB already had the table |
| `alembic check` | fails the build on model/migration drift — "changed the model, forgot to generate the revision" |
| `pytest` | 71 tests, run against the migrated schema |

**Build image** — builds the `Dockerfile` without pushing. Validates the image
on every PR.

**Publish image** — pushes to `ghcr.io/khatana-azm/devops-learning-path`, tagged
`sha-<commit>` and `develop`. Authenticates with the run-scoped `GITHUB_TOKEN`,
so there is no long-lived registry credential to store or rotate.

**Deploy to staging** — migrations first, then deploy, then verify:

1. `alembic upgrade head` against Neon — **before** the new code ships. The
   reverse order deploys an app that queries columns which do not exist yet.
2. `vercel deploy --prod` — Vercel builds the same `Dockerfile` and runs it as
   a container.
3. `curl /health` (with retries for cold start) and `/health/db`. **A non-200
   fails the job.** A deploy that returns a URL is not a deploy that works, and
   `/health/db` is what proves the migrations actually landed.

### Merge protection

`develop` is covered by an active ruleset with **no bypass actors** — it applies
to repository admins too:

- a pull request is required; direct pushes are rejected
- `Lint`, `Test` and `Build image` must pass before merge

This was verified by deliberately breaking it: a PR that removed the ownership
filter from the task lookup turned four tests red and GitHub reported
`mergeStateStatus: BLOCKED`. The gate is enforced, not just configured.

### How a change reaches staging

```
branch → PR into develop → 3 checks must pass → merge
                                                  │
                             image published to GHCR
                                                  │
                        migrations applied to Neon
                                                  │
                              deployed to Vercel
                                                  │
                            health-checked or fail
```

No manual step anywhere. Nobody SSHes in, nobody runs migrations by hand,
nobody clicks deploy.

---

## Configuration

Nothing secret lives in the repository. `.env`, `keys/`, `*.pem` and `.vercel`
are all gitignored, and the history has been checked for leaked material.

| where | holds | used by |
|---|---|---|
| GitHub environment `staging` | `NEON_DATABASE_URL_DIRECT`, `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | the deploy job only |
| Vercel project env | `DATABASE_URL` (pooled), `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY`, `PORT` | the running app |
| local `.env` | compose Postgres credentials | local dev |

Two deliberate choices there:

**The deploy job declares `environment: staging`.** Only jobs that declare it
can read those secrets — the lint and test jobs *cannot* reach the staging
database or the Vercel token, even if someone later adds a step that tries.

**Runtime secrets live in Vercel, not GitHub.** The signing key never passes
through CI, so there is one fewer system that could leak it.

### Two Neon connection strings

| | endpoint | used for |
|---|---|---|
| direct | `ep-...neon.tech` | migrations |
| pooled | `ep-...-pooler.neon.tech` | application runtime |

Neon's pooled endpoint is PgBouncer in transaction mode, where session-level
features do not work. Schema migrations need a real session; the application
needs the connection ceiling. Using the pooled endpoint for migrations appears
to work and then fails intermittently on anything session-scoped.

### Container notes

- Binds `$PORT` (default 8000) — hosted platforms inject it, and a hardcoded
  port means the health check silently times out
- `CMD` uses `exec` so uvicorn is PID 1 and receives `SIGTERM` directly.
  Vercel sends `SIGTERM` with a 30-second grace period on scale-down; without
  `exec`, `sh` holds PID 1 and the server is killed mid-request instead of
  draining
- Runs as non-root (`appuser`, uid 1000)
- `DATABASE_URL` accepts any Postgres scheme and is rewritten to
  `postgresql+psycopg://`; `sslmode` is appended only when not already present

---

## Known gaps

Stated plainly rather than discovered in review:

- **The published image is not the deployed image.** CI publishes to GHCR;
  Vercel rebuilds the same `Dockerfile` server-side. Vercel's container runtime
  builds from source rather than pulling an external registry. The same
  Dockerfile runs in all three places, but the exact bytes differ. Pushing to
  Vercel's own registry and deploying by digest would close this.
- **No Slack notification yet** — the pipeline reports through GitHub only.
- **No login rate limiting.** `/auth/login` accepts unlimited attempts.
- **Logout does not revoke an issued access token.** It stays valid until it
  expires (≤30 min). Inherent to stateless JWTs; a per-request revocation list
  would fix it at the cost of the statelessness.
- **Expired `refresh_tokens` rows are never pruned.**
- **Staging accumulates test accounts** — there is no delete-user endpoint.

---

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
tests/           pytest suite (71 tests, runs against real Postgres)
postman/         Postman collection (39 requests, 56 assertions)
.github/workflows/ci.yml
api.http
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

## Poking at it by hand

Two options, same coverage:

- **`postman/devops-learning-path.postman_collection.json`** -- one file, runs
  in the Postman app or headless with `npx newman run ...` (see
  [Staging](#staging)). Every request asserts its expected status, so a run
  gives you pass/fail rather than output to eyeball.
- **`api.http`** -- the same requests for VS Code's REST Client extension, if
  you would rather not leave the editor.

Both chain tokens and ids automatically and generate fresh identities per run.
To point either at a local stack, change the `baseUrl` collection variable in
Postman, or `@baseUrl` at the top of `api.http`.

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
- The Makefile defaults to `DOCKER_CONTEXT=default` (the system daemon).
  Override per invocation if you use a different context, e.g.
  `make run DOCKER_CONTEXT=desktop-linux`.
