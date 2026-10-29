# Contact QR

A containerized web application and REST API that turns contact details into a
**vCard QR code**. Scan the code with a phone camera (iOS or Android) and the phone
offers to **save the person as a contact**.

**Live:** https://contact-qr.46-202-189-105.sslip.io · **API docs:**
https://contact-qr.46-202-189-105.sslip.io/api/docs

| | |
|---|---|
| Web UI | create, edit and delete contacts, view and download QR codes, browse history, search, view request logs |
| REST API | everything the UI does, under `/api/v1`, documented with OpenAPI/Swagger |
| Storage | PostgreSQL: contacts, the generated QR images, and a log of every HTTP request |
| Ops | one-command Docker Compose stack, health check, CI/CD with tests → build → deploy → automatic rollback |

Design decisions, trade-offs, limitations and security notes are in **[NOTES.md](NOTES.md)**.
The key decisions are also recorded as ADRs in [`docs/adr/`](docs/adr/).

---

## Contents

1. [Architecture](#architecture)
2. [Technology stack](#technology-stack)
3. [Running locally](#running-locally)
4. [Configuration](#configuration)
5. [Running the tests](#running-the-tests)
6. [CI/CD pipeline](#cicd-pipeline)
7. [Deployment](#deployment)
8. [Persistent storage](#persistent-storage)
9. [Using the API](#using-the-api)
10. [Project layout](#project-layout)

---

## Architecture

```
                     Production VPS (Debian 12)
 Browser / phone     ┌──────────────────────────────────────────────────────────────┐
 API client  ──HTTPS─┼─► nginx (host, :443)  ── Let's Encrypt TLS, one vhost per site │
                     │      │ proxy_pass 127.0.0.1:8100, sets X-Forwarded-For       │
                     │      ▼                                                       │
                     │  ┌─ docker compose project "contact-qr" ─────────────────┐   │
                     │  │  app  (FastAPI + Uvicorn, non-root)                   │   │
                     │  │   ├─ Web UI  (Jinja2, server-rendered)  /, /contacts… │   │
                     │  │   ├─ REST API                           /api/v1/…     │   │
                     │  │   ├─ request-logging middleware → request_logs table  │   │
                     │  │   └─ /health  (checks the database)                   │   │
                     │  │        │ private bridge network (172.30.50.0/24)      │   │
                     │  │  db   (PostgreSQL 17)  ── not published to the host   │   │
                     │  │        └─ volume contact-qr_pgdata  (external, persistent)│
                     │  │  migrate (one-shot: alembic upgrade head)             │   │
                     │  └────────────────────────────────────────────────────────┘   │
                     └──────────────────────────────────────────────────────────────┘
```

**Request flow for "create contact"** (UI form or `POST /api/v1/contacts`):

1. The input is validated by one Pydantic model, `ContactIn`, which both interfaces share.
2. The service layer builds a vCard 3.0 payload, renders it as a PNG QR code with `segno` (initials badge in the centre),
   and stores the contact, the vCard text and the PNG in one database transaction.
3. The UI redirects to the contact page, which shows the QR code. The API returns `201`
   with a `Location` header.
4. After the response has been sent, the logging middleware writes one `request_logs` row:
   timestamp, IP, method, path, status, user agent, source (`UI`/`API`/`SYSTEM`) and duration.

The UI and the API are thin adapters over the same **service layer**
(`app/services/`), so the two cannot behave differently.

## Technology stack

| Concern | Choice |
|---|---|
| Language / runtime | Python 3.12 |
| Web framework | FastAPI (generates the OpenAPI docs from the code) + Uvicorn |
| UI | Server-rendered Jinja2 templates, plain CSS, no JS framework (≈5 lines of JS) |
| Validation | Pydantic v2, `phonenumbers` (libphonenumber), `email-validator` |
| QR / vCard | `segno` (QR), Pillow (initials badge in the centre), hand-written vCard 3.0 serializer (RFC 2426) |
| Database | PostgreSQL 17, SQLAlchemy 2 ORM, Alembic migrations |
| Packaging | `uv` with a lock file (`uv.lock`) for reproducible builds |
| Containers | Multi-stage Dockerfile, Docker Compose (local and production files) |
| Reverse proxy / TLS | Host nginx + certbot (Let's Encrypt) |
| CI/CD | GitHub Actions → GitHub Container Registry (GHCR) → SSH deploy |
| Tests / lint | pytest (+ real PostgreSQL), OpenCV QR decoding, Ruff |

Why each was chosen: [NOTES.md → Technology choices](NOTES.md#technology-choices-and-rationale).

## Running locally

### Prerequisites

* Docker with the Compose plugin (Docker Desktop, or Docker Engine 24+ with Compose v2.24+).
* Nothing else: no Python or database installation is needed.

### Start

```bash
git clone https://github.com/tomasas7/contact-qr.git
cd contact-qr
docker compose up --build
```

That one command builds the image, starts PostgreSQL, runs the migrations (the `migrate`
service) and then starts the app. No `.env` file is needed for local use.

### Access

| | URL |
|---|---|
| Web UI | http://localhost:8000 |
| API docs (Swagger UI) | http://localhost:8000/api/docs (ReDoc: `/api/redoc`) |
| OpenAPI spec | http://localhost:8000/api/openapi.json |
| Health check | http://localhost:8000/health |
| Request logs | http://localhost:8000/logs or `GET /api/v1/logs` |

To try the QR code, open a contact in the UI and point your phone camera at the screen.

### Stop

```bash
docker compose down        # stop and remove the containers; data is KEPT
docker compose down -v     # ...and delete the local database volume (start from scratch)
```

### How local data is persisted

All state (contacts, QR images, request logs) lives in PostgreSQL. Its data directory is
the named Docker volume `contact-qr_pgdata`. The volume outlives the containers: `down`
followed by `up`, a rebuild, or a reboot all keep your data. Only `down -v` deletes it.

The local database is reachable only on the stack's private network. No port is published
to the host, so it uses `trust` auth and no password exists anywhere in the repo. To open
a SQL shell, run `docker compose exec db psql -U contactqr`.

### Running without Docker (optional, for development)

```bash
uv sync                                   # Python 3.12 + uv required
docker run -d --name cqr-db -p 127.0.0.1:5432:5432 \
  -e POSTGRES_USER=contactqr -e POSTGRES_DB=contactqr -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres:17-alpine
export POSTGRES_HOST=127.0.0.1
uv run alembic upgrade head
uv run uvicorn --factory app.main:create_app --reload
```

## Configuration

All configuration comes from **environment variables** (12-factor). Locally they are
optional. In production they're read from `/opt/contact-qr/.env` on the server, which is
generated there and never committed. See [`.env.example`](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_HOST` / `POSTGRES_PORT` | `localhost` / `5432` | Database address (Compose sets the host to `db`) |
| `POSTGRES_USER` / `POSTGRES_DB` | `contactqr` | Database user and name |
| `POSTGRES_PASSWORD` | *(empty)* | **Required in production.** Generated on the server with `openssl rand` |
| `DEFAULT_PHONE_REGION` | `LT` | How to read phone numbers that have no `+country` prefix |
| `LOG_RETENTION_DAYS` | `90` | Request logs older than this are deleted daily (`0` = keep forever) |
| `RATE_LIMIT_WRITES_PER_MINUTE` | `30` | Create/update/delete requests allowed per client IP per minute (`0` = off) |
| `LOG_LEVEL` | `INFO` | Application log level |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-For` is trusted (read by Uvicorn) |
| `APP_VERSION` | `dev` | Baked into the image at build time (`1.0.0+<git sha>`), reported by `/health` |

**Secrets policy:** no credentials exist in the repository or in any image. Production
secrets are the DB password (on the server only, file mode `600`) and the CI deploy key
and server details (GitHub Actions secrets). The registry login uses the per-job
`GITHUB_TOKEN`, which expires when the job ends.

## Running the tests

The suite has 122 tests and 96% line coverage. The integration tests run against a
**real PostgreSQL** and build the schema by running the Alembic migrations, so the
migrations are tested as well.

```bash
# 1. a throwaway test database (tests refuse to run unless the DB name ends in _test)
docker run -d --name cqr-test-db -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=contactqr -e POSTGRES_DB=contactqr_test -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres:17-alpine

# 2. run tests (and lint)
export POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432
uv run pytest                      # add --cov=app for coverage
uv run ruff check . && uv run ruff format --check .
```

| Test file | Covers |
|---|---|
| `test_validation.py` | every input rule: required fields, email/phone/date formats, lengths, control characters, URLs |
| `test_vcard_qr.py` | vCard structure and escaping, injection attempts, and a **round trip that decodes the PNG with OpenCV** and compares it with the stored payload, including Lithuanian characters and a worst-case maximum-size contact |
| `test_api_contacts.py` | CRUD, pagination, search, QR/vCard downloads, error envelope, status codes 400/404/405/422 |
| `test_ui.py` | form errors shown and input kept, create → view → edit → delete flow, HTML escaping, security headers |
| `test_request_logging.py` | every request logged with all fields and the right source, log API filters, logging failure doesn't break requests, retention |
| `test_platform.py` | health check (including DB outage → 503), rate limiting (429 + `Retry-After`), data survives an app restart, **models match migrations** |

## CI/CD pipeline

Defined in [`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml). It runs on every
push and pull request. Deployment happens only on pushes to `main`.

```
push / PR ──► [Lint & test] ──┬──► [Build & push image] ──┐
                              └──► [Compose smoke test] ──┴──► [Deploy] ──► verify public /health
                                                               (main only)
```

| Job | What it does | Fails when |
|---|---|---|
| **Lint & test** | Ruff lint and format check; pytest against a PostgreSQL service container; coverage table in the job summary; JUnit report artifact | any lint error or failing test |
| **Build & push** | builds the image (`APP_VERSION=1.0.0+<sha>`), with layer cache; pushes `ghcr.io/tomasas7/contact-qr:sha-<sha>` and `:latest` on `main` | the image doesn't build |
| **Compose smoke test** | `docker compose up --build --wait` (the exact local one-command setup), creates a contact, downloads its QR, queries logs, then **recreates the containers and checks the data is still there** | the stack doesn't come up or lose data |
| **Deploy** | uploads `docker-compose.prod.yml` + `deploy.sh`, runs the deploy over SSH (see below), then checks the **public HTTPS** `/health` reports the new version | deploy, health gate or public check fails |

**Tests gate deployment:** `build` and `smoke` need `test`, and `deploy` needs both, so a
failing test means nothing gets built or deployed. Deploys run one at a time
(`concurrency: production`) and are never cancelled halfway. Failures show up as GitHub
annotations (Ruff), pytest output, the JUnit artifact, and container logs from the
deploy health gate.

## Deployment

The app runs on the provided VPS next to other services. It uses its own Compose
project, network, volume and nginx virtual host, and touches nothing else.

### One-time server setup (done)

[`deploy/bootstrap-server.sh`](deploy/bootstrap-server.sh) runs once as root and is safe
to re-run. It:
* creates a `contactqr` user (in the docker group, SSH-key login only) that CI deploys as,
  so CI never gets root
* creates `/opt/contact-qr/` and a `.env` with a random DB password (mode `600`)
* creates the **external** Docker volume `contact-qr_pgdata`
* installs [`deploy/nginx-contact-qr.conf`](deploy/nginx-contact-qr.conf) as a new nginx
  site, runs `nginx -t` before reloading, and gets a Let's Encrypt certificate with certbot

### Every deploy

[`deploy/deploy.sh <tag>`](deploy/deploy.sh), run by CI on the server:

1. `docker compose pull` the new image (`sha-<commit>`)
2. make sure the DB is up, then **`pg_dump` a backup** to `/opt/contact-qr/backups/`
   (keeping the last 10)
3. **run migrations** in a one-shot container (`alembic upgrade head`)
4. recreate **only** the app container with the new image (`up -d --no-deps app`)
5. **health gate:** for up to 60 s, `/health` must return `ok` **and** report the new
   version. This proves the new container is the one serving and that it can reach the DB.
6. on failure: **automatic rollback** to the previous image tag, then exit non-zero, so
   the pipeline goes red
7. on success: record current and previous tags in `.deploy/`, and prune old images

### Manual operations (on the server, as `contactqr`, in `/opt/contact-qr`)

```bash
IMAGE_TAG=$(cat .deploy/current_tag) docker compose -f docker-compose.prod.yml ps
IMAGE_TAG=$(cat .deploy/current_tag) docker compose -f docker-compose.prod.yml logs -f app
./deploy.sh "$(cat .deploy/previous_tag)"           # manual rollback to the previous release

# restore a backup (e.g. after a bad migration):
source .env
IMAGE_TAG=$(cat .deploy/current_tag) docker compose -f docker-compose.prod.yml exec -T db \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backups/<file>.dump
```

## Persistent storage

| Data | Where | Survives restart, container recreation, redeploy? |
|---|---|---|
| Contacts | `contacts` table | yes |
| Generated QR images (PNG) | `contacts.qr_png` (`bytea`), with the exact vCard text in `contacts.vcard` | yes |
| Request logs | `request_logs` table (pruned after `LOG_RETENTION_DAYS`) | yes |
| DB backups | `/opt/contact-qr/backups/*.dump` on the host (last 10) | yes, outside Docker entirely |

Everything stateful is in PostgreSQL, whose data directory is the Docker volume
**`contact-qr_pgdata`**, mounted at `/var/lib/postgresql/data`. The app container is
stateless and disposable.

In production the volume is declared **`external: true`**. Compose never creates or
removes it, so no deploy, rollback or even `docker compose down -v` can delete the data.
Schema changes happen only through versioned Alembic migrations, and a `pg_dump` runs
before each one. The reasoning is in
[NOTES.md → Safe redeployment](NOTES.md#safe-redeployment) and
[ADR 0002](docs/adr/0002-store-qr-images-in-postgres.md).

## Using the API

The interactive documentation is at **`/api/docs`** (Swagger UI), with ReDoc at
`/api/redoc` and the OpenAPI 3.1 spec at `/api/openapi.json`. It covers every endpoint,
parameter, request and response schema, status code and example.

| Method & path | Description | Success | Errors |
|---|---|---|---|
| `POST /api/v1/contacts` | Create a contact and generate its QR code | `201` + `Location` | `400` bad JSON, `422` validation, `429` rate limit |
| `GET /api/v1/contacts?q=&limit=&offset=` | List contacts (newest first), optional search | `200` | `422` |
| `GET /api/v1/contacts/{id}` | Get one contact | `200` | `404`, `422` bad UUID |
| `PUT /api/v1/contacts/{id}` | Replace a contact (QR code is regenerated) | `200` | `400`, `404`, `422`, `429` |
| `DELETE /api/v1/contacts/{id}` | Delete a contact and its QR code | `204` | `404`, `429` |
| `GET /api/v1/contacts/{id}/qr[?download=true]` | QR code PNG (inline, or as a download) | `200 image/png` | `404` |
| `GET /api/v1/contacts/{id}/vcard` | The contact as a `.vcf` file | `200 text/vcard` | `404` |
| `GET /api/v1/logs?source=&method=&status_code=&path=&client_ip=&since=&until=&limit=&offset=` | Query request logs | `200` | `422` |
| `GET /health` | Liveness and database check | `200` | `503` |

**Contact fields:** `first_name` and `last_name` are required (≤50 characters), plus **at
least one of** `email` or `phone`. Optional: `birthday` (`YYYY-MM-DD`, not in the future,
≥1900), `street`, `city`, `postal_code`, `country`, `organization`, `job_title`,
`website` (http/https) and `note` (≤200, multi-line). Phone numbers are accepted in any
common format and returned in E.164 (`+37061234567`).

**Errors** always have the same shape. `code` is stable and meant for programs; `message`
is meant for people. `details` lists every field problem at once:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request contains invalid data.",
    "details": [
      {"field": "email", "message": "Enter a valid email address.", "code": "invalid_email"},
      {"field": null, "message": "Provide at least an email address or a phone number.",
       "code": "contact_method_required"}
    ]
  }
}
```

### Example requests

```bash
BASE=https://contact-qr.46-202-189-105.sslip.io     # or http://localhost:8000

# Create
curl -sS -X POST "$BASE/api/v1/contacts" -H 'Content-Type: application/json' -d '{
  "first_name": "Jonas", "last_name": "Jonaitis",
  "email": "jonas@example.com", "phone": "8 612 34567",
  "birthday": "1990-05-17", "city": "Vilnius", "country": "Lithuania"
}'
```
```http
HTTP/1.1 201 Created
Location: /api/v1/contacts/3f1c2a9e-7b4d-4c1e-9a8f-2d6b5e4c3a21
Content-Type: application/json

{"id": "3f1c2a9e-7b4d-4c1e-9a8f-2d6b5e4c3a21", "first_name": "Jonas", "last_name": "Jonaitis",
 "email": "jonas@example.com", "phone": "+37061234567", "birthday": "1990-05-17",
 "city": "Vilnius", "country": "Lithuania", "street": null, ...,
 "vcard": "BEGIN:VCARD\r\nVERSION:3.0\r\nN:Jonaitis;Jonas;;;\r\nFN:Jonas Jonaitis\r\n...",
 "qr_url": "/api/v1/contacts/3f1c2a9e-.../qr", "vcard_url": "/api/v1/contacts/3f1c2a9e-.../vcard",
 "created_at": "2026-10-25T12:00:00Z", "updated_at": "2026-10-25T12:00:00Z"}
```
```bash
# List / search / paginate
curl -sS "$BASE/api/v1/contacts?q=vilnius&limit=10&offset=0"
# -> {"items": [...], "total": 1, "limit": 10, "offset": 0}

# Get one
curl -sS "$BASE/api/v1/contacts/$ID"

# Download the QR code image
curl -sS -o jonas.png "$BASE/api/v1/contacts/$ID/qr?download=true"

# Update (full replace; the QR code is regenerated)
curl -sS -X PUT "$BASE/api/v1/contacts/$ID" -H 'Content-Type: application/json' \
  -d '{"first_name": "Jonas", "last_name": "Jonaitis", "email": "jonas@new.lt"}'

# Delete
curl -sS -X DELETE "$BASE/api/v1/contacts/$ID" -o /dev/null -w '%{http_code}\n'   # 204

# Validation error
curl -sS -X POST "$BASE/api/v1/contacts" -H 'Content-Type: application/json' \
  -d '{"first_name": "Jonas", "last_name": "", "email": "nope"}'
# -> 422 {"error": {"code": "validation_error", "details": [{"field": "last_name", ...}, ...]}}

# Request logs: failed API calls in the last day
curl -sS "$BASE/api/v1/logs?source=API&status_code=422&since=2026-10-24T00:00:00Z"

# Health
curl -sS "$BASE/health"
# -> {"status": "ok", "checks": {"database": "ok"}, "version": "1.0.0+3f1c2a9"}
```

## Project layout

```
app/
  main.py            app factory: wiring, middleware, retention job
  config.py          settings from environment variables
  schemas.py         ContactIn validation rules + API response models
  vcard.py, qr.py    vCard 3.0 serialization, QR PNG rendering
  services/          use cases shared by UI and API (contacts, request logs)
  api/               REST endpoints (/api/v1/contacts, /api/v1/logs, /health)
  web.py             server-rendered UI routes
  middleware.py      request logging, security headers
  errors.py          consistent JSON/HTML error responses
  ratelimit.py       per-IP sliding-window limiter
  templates/, static/
migrations/          Alembic (versioned schema changes)
tests/               pytest suite (unit + integration against PostgreSQL)
deploy/              deploy.sh, bootstrap-server.sh, nginx site config
docs/adr/            architecture decision records
Dockerfile, docker-compose.yml (local), docker-compose.prod.yml (server)
.github/workflows/ci-cd.yml
```
