# NOTES

Design decisions, trade-offs, limitations and security considerations for **Contact QR**.
The README covers how to run and use it. The most important decisions are also kept as
ADRs in [`docs/adr/`](docs/adr/).

---

## 1. Important design decisions

### One service layer, two thin interfaces
The web UI (`app/web.py`) and the REST API (`app/api/`) are thin adapters. Both validate
through the **same Pydantic model** (`ContactIn`) and call the **same service functions**
(`app/services/`). That's how "everything in the UI is also in the API" is guaranteed,
and why validation messages are identical in both. The UI posts to its own routes and
doesn't call the API over HTTP, which keeps UI and API traffic separate in the request
log (see below).

### Mandatory vs optional fields
| Field | Rule | Why |
|---|---|---|
| First name, last name | **required**, 1–50 characters | vCard requires `N`/`FN`, and a saved contact with no name is useless on a phone |
| Email **or** phone | **at least one required** | A contact you can't reach defeats the purpose of sharing one. Many people share only one of the two, so neither is required on its own |
| Birthday | optional; real date, not in the future, year ≥ 1900 | catches typos (`2099`, `1890`) without rejecting real people |
| Street, city, postal code, country | optional, each one separate | a partial address (just a city) is common and valid |
| Organization, job title, website, note | optional | nice to have on business cards |

**Validation details worth defending:**
* **Phone:** parsed with Google's libphonenumber (`phonenumbers`). People can type it as
  they normally write it (`8 612 34567`, `+370 612 34567`). Numbers without a country
  prefix are read in the configurable default region (`LT`). Numbers are **checked for
  validity**, not just against a regex, and **stored in E.164** (`+37061234567`) so every
  phone dials them correctly from a vCard.
* **Email:** syntax-checked with `email-validator`, with **no DNS lookups**. That keeps
  validation fast, deterministic and offline (and testable), at the cost of accepting
  syntactically valid addresses at domains that don't exist.
* **Length limits** were chosen so that **a contact with every field at its maximum
  still fits in a QR code**, even with multi-byte UTF-8 characters and characters that
  need escaping. A test checks exactly that worst case.
* **Control characters** (including zero-width characters) are rejected in single-line
  fields; the note may contain line breaks. Whitespace is trimmed and blank optional
  fields become `null`.
* All problems are reported **at once**, per field, not one at a time.

### vCard 3.0, built by hand
**vCard 3.0** is the version the iOS and Android camera apps most reliably recognise and
offer to save. 4.0 support is less consistent. The serializer (`app/vcard.py`) is about
50 lines: it escapes `\ , ;` and newlines per RFC 2426, so a crafted note like
`x\nEMAIL:attacker@evil.com` can't inject an extra property (a test covers this). Lines
are not folded at 75 octets, because phone scanners don't need it and folding only
adds bytes to the QR payload.

### QR images are generated once and stored
The PNG is rendered at save time (create or update) and stored together with the exact
vCard text. The brief asks for persisted images, and it also guarantees that what was
issued is what gets served, even if the rendering library changes later.

### Initials badge in the QR code (bonus task)
Each code has the contact's initials (e.g. **ŠŽ**) in a blue badge in the centre:
* The code is generated at error correction **H**, which recovers up to about 30% of
  damaged symbol. The badge's white clear zone covers about 6% of the area (24% of the
  width), aligned to whole modules, so there's a wide safety margin. Finder patterns at
  the corners are never covered.
* H costs capacity, so the symbol is denser. A typical contact goes from version ~6 to
  ~10. If a contact is too long for H, the badge is dropped and the plain code is used:
  level **M** (about 15%), raised by `segno` when capacity allows, falling back to L.
  Every valid contact therefore still gets a code.
* Initials are the first letter or digit of each name, upper-cased. Pillow's built-in
  font has no Lithuanian letters, so **Inter Bold** (SIL Open Font License, licence
  text next to the file in `app/assets/fonts/`) is bundled. Characters the font lacks
  (e.g. CJK) are skipped; with none left, the code has no badge.
* Tests decode badged codes of every size (small to the largest that fits at H) with
  OpenCV, check the badge is actually there, and check the fallback.
* Existing contacts get the badge through data migration `0002`, which re-renders their
  stored PNGs from the stored vCard text. Its downgrade renders them without the badge.
* Badge PNGs are saved as 16-colour palette images: 4–15 KB instead of 1–5 KB.

### Request logging
* A **pure ASGI middleware** wraps the whole app, so it sees the final status of
  *every* response, including 404s, validation errors, static files and unhandled 500s.
* **Source classification is by URL space:** `/api/*` (including the API docs) is
  `API`, `/health` is `SYSTEM`, and everything else is `UI`. The brief asks for UI vs
  API; `SYSTEM` was added so the health probe (every 30 s from Docker and every deploy)
  doesn't pollute either category but is still logged, because "every request" means
  every request.
* The row is written **after the response is sent**, in a worker thread, so logging adds
  no latency. **A logging failure never fails the request**: the entry goes to stdout
  instead (`docker logs`), and a test covers this. The trade-off is that if the database
  is down, log rows for that period exist only in the container logs.
* The client IP is the real visitor's IP. nginx overwrites `X-Forwarded-For` (so clients
  can't spoof a chain), and Uvicorn trusts that header **only** from the Docker bridge
  gateway (`FORWARDED_ALLOW_IPS`).
* Logs are queryable through `GET /api/v1/logs` (filters: source, method, status, path,
  IP, time range; pagination) and through the `/logs` page.

### Health check includes the database
`/health` runs `SELECT 1` with a 3-second timeout and returns `503` with per-check
detail if the database can't be reached. The app is useless without its database, so
"process is up" alone would be a false positive. The endpoint also reports the
**deployed version**, which the deploy gate uses to prove the *new* container is the
one serving. A separate liveness/readiness split (Kubernetes style) wasn't needed for
a Compose deployment.

---

## 2. Technology choices and rationale

| Choice | Why | Alternatives considered |
|---|---|---|
| **Python + FastAPI** | Generates OpenAPI/Swagger docs directly from type hints (the API documentation can't drift from the code); Pydantic validation; little boilerplate | Node/TypeScript (more setup for docs and validation), .NET (more ceremony) |
| **Server-rendered Jinja2 UI** | One container, no build step, no CORS, no client state; the forms work without JavaScript; strict CSP is easy | React/Vite SPA: a second container and build pipeline for a 5-page app, and UI traffic would hit `/api` and blur the UI/API logging distinction |
| **PostgreSQL** | Real concurrent database with transactions; one place for all state; its health is meaningfully checkable | SQLite on a volume: simpler, but weaker for concurrent writes and undersells the multi-service setup |
| **SQLAlchemy 2 + Alembic** | Versioned, reviewable schema changes, which is central to safe redeploys | `create_all()` on startup (no migration history, risky changes) |
| **segno** (+ Pillow for the badge) | segno: pure Python, standards-compliant, picks the smallest symbol automatically. Pillow draws the initials badge on top | `qrcode` (its logo support is the same Pillow overlay) |
| **uv + lock file** | Fast, fully reproducible dependency resolution in CI and Docker | pip + requirements.txt |
| **Host nginx (not a proxy container)** | The VPS already runs nginx on 80/443 for other sites, so a second proxy couldn't bind those ports. Adding a virtual host is the least invasive option and reuses the existing certbot setup | Caddy container (the original plan; it would conflict with the existing nginx) |
| **GitHub Actions + GHCR** | Built into the repo hosting; images are built **once** in CI and the server only pulls them (it never compiles code) | Building on the server (slow, needs source code there, not reproducible) |
| **sslip.io hostname** | No domain was provided; `contact-qr.46-202-189-105.sslip.io` resolves to the server and gets a real Let's Encrypt certificate, so HTTPS works with zero DNS setup | Plain IP over HTTP (no TLS) |

---

## 3. Safe redeployment

Requirement: a redeploy must never delete contacts, QR codes or request logs. Here is
how that's guaranteed, one layer at a time:

1. **All state is in one place.** Contacts, QR PNGs (`bytea`) and request logs are all
   PostgreSQL tables. The app container is stateless, so replacing it can't lose anything.
2. **The data volume is external.** `docker-compose.prod.yml` declares
   `contact-qr_pgdata` as `external: true`. It was created once by the bootstrap script,
   and **Compose never creates or removes it**, not even with `docker compose down -v`.
   Deleting it would take a deliberate `docker volume rm`.
3. **Deploys replace only the app container.** `deploy.sh` runs `up -d --no-deps app`.
   The database container keeps running through a normal deploy.
4. **The schema changes only through migrations.** Alembic migrations run in a separate
   one-shot step. The app never creates or drops tables itself. A test
   (`test_migrations_match_models`) fails if a model change has no migration.
5. **A backup runs before every migration.** `pg_dump` writes a custom-format dump to
   `/opt/contact-qr/backups/` on the host, outside Docker, keeping the last 10.
6. **Health gate and automatic rollback.** If the new version doesn't report healthy
   *with its own version number* within 60 s, the previous image is started again and
   the pipeline fails. A rollback swaps only the app image, so data is unaffected.
7. **Verified in CI.** The smoke-test job recreates the containers and checks the data is
   still there, and `test_data_survives_application_restart` covers app restarts.

**Caveat (documented, not automated):** rolling back the *image* doesn't roll back the
*schema*. Future migrations therefore have to be **backward compatible**
(expand → migrate → contract: add columns as nullable, drop them a release later), so
the previous version keeps working on the new schema. If a migration ever corrupts data,
the pre-deploy dump is the recovery path (the `pg_restore` command is in the README).

---

## 4. Optional enhancements: what was built and why

| Enhancement | Built? | Reasoning |
|---|---|---|
| **API versioning** (`/api/v1`) | ✅ | Nearly free now, and it makes breaking changes possible later without breaking clients |
| **Database migrations** (Alembic) | ✅ | Essential for safe redeploys (see §3) |
| **Search / filtering for contacts** | ✅ | `q` searches name, email, phone, organization and city (case-insensitive; `%`/`_` are treated literally). Pagination everywhere |
| **Search / filtering for request logs** | ✅ | source, method, status, path substring, IP, time range, pagination, in both the API and the UI |
| **Rate limiting per IP** | ✅ | Sliding window on **write** operations only (30/min by default) across UI and API combined; reads are never limited. `429` + `Retry-After` |
| **HTTPS** | ✅ | Let's Encrypt via certbot on the host nginx; HTTP redirects to HTTPS |
| **Automated rollback** | ✅ | Version-aware health gate in `deploy.sh` |
| **Data retention** | ✅ | Request logs older than `LOG_RETENTION_DAYS` (90) are deleted by a daily in-app job. Contacts are never auto-deleted, since they're user data |
| **Additional monitoring** | ◑ partial | Health endpoint with DB check and version, Docker `HEALTHCHECK`, the request log itself (latency per request), structured stdout logs with rotation. No metrics stack (see §7) |
| **Initials badge in the QR code** (bonus task) | ✅ | Error correction H plus a centre badge; falls back to a plain code when a contact is too long for H (see §1) |
| **API authentication** | ❌ deliberately | See below |

**Why no API authentication:** the brief requires the UI and the API to offer the same
functionality, and the UI is public. Putting a key on the API alone would add no real
protection, because anyone could do the same thing through the UI. Real access control
means authenticating **both**, which means user accounts and sessions: more than a week
allows to do properly. Rate limiting reduces abuse in the meantime. This is the first
item under "with more time".

---

## 5. Relevant trade-offs

* **QR in the database vs files on a volume:** `bytea` keeps a single source of truth,
  one volume and one backup, and the contact and its image are written atomically, at
  the cost of slightly larger DB rows. The images are 4–15 KB, and the column is loaded
  lazily (`deferred`), so listing contacts never reads them.
  [ADR 0002](docs/adr/0002-store-qr-images-in-postgres.md)
* **Stored QR vs generating it on each request:** storing costs a few KB per contact but
  gives a stable, auditable artifact and no CPU per view. The brief also asks for it.
* **Log after the response:** zero added latency and a request never fails because of
  logging, at the cost of possibly missing DB rows during a DB outage (they're still in
  stdout). [ADR 0003](docs/adr/0003-request-logging-after-response.md)
* **Logging in the main database:** simple and queryable with SQL, but log volume competes
  with business data. Retention keeps it bounded; at real scale logs would go to a
  dedicated store.
* **In-memory rate limiter:** correct for a single app container and free of extra
  infrastructure. It resets on restart and wouldn't be shared between replicas (that
  would need Redis).
* **Single Uvicorn worker:** enough for this workload, and it keeps the rate-limit state
  consistent. Vertical scaling would mean adding workers *and* moving the limiter to Redis.
* **Full replace (`PUT`) instead of `PATCH`:** simpler semantics. Each update rebuilds the
  whole vCard and QR anyway, so a partial update gains little.
* **UUID ids:** contacts can't be enumerated by guessing `/contacts/1, /2, ...`. The
  trade-off is longer URLs.

---

## 6. Security considerations

**Done:**
* **No secrets in the repo or images.** Local dev and CI use password-less `trust` auth on
  networks that aren't exposed. The production DB password is generated on the server
  (`openssl rand`), kept in a `600` file, and never leaves it. CI secrets are in GitHub
  Actions secrets. The registry login uses the per-job `GITHUB_TOKEN`, which expires when
  the job ends, and logs out afterwards.
* **Least privilege.** CI deploys over SSH as the dedicated `contactqr` user with its own
  key, not as root. The container runs as an unprivileged user (uid 10001). The host key
  is pinned (`known_hosts` secret), with no `StrictHostKeyChecking=no`.
* **Network exposure.** PostgreSQL isn't published at all. The app listens only on
  `127.0.0.1:8100`, so the only public entry point is nginx with TLS.
* **Injection.** SQL goes through SQLAlchemy with bound parameters, and LIKE wildcards in
  search input are escaped. vCard values are escaped, so no property injection is
  possible. Jinja2 autoescaping protects against XSS (tested).
* **Security headers:** a strict `Content-Security-Policy` (no inline scripts, no
  framing), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and a
  `Referrer-Policy`.
* **Input hardening:** length limits on every field, control characters rejected,
  `extra="forbid"` (unknown JSON fields are errors, not silently ignored), request body
  size capped by nginx (64 KB), and rate limiting on writes.
* **IP spoofing:** `X-Forwarded-For` is overwritten by nginx and trusted only from the
  bridge gateway.
* **Download filenames** are reduced to ASCII slugs, so header injection through
  `Content-Disposition` isn't possible.

**Known gaps / accepted risks:**
* **No authentication.** Anyone with the URL can read, create, edit and delete contacts
  and read the request logs, which include IPs and user agents (personal data under
  GDPR). Fine for a demo, unacceptable for real data (see §7).
* **No CSRF tokens on the UI forms.** With no authentication there's no session to
  hijack, so CSRF gives an attacker nothing they couldn't do directly. Once login exists,
  CSRF protection (SameSite cookies + tokens) becomes required.
* The Swagger/ReDoc pages load their assets from a CDN, so they're served without the CSP
  rather than weakening it for the whole site.
* Backups are on the same machine as the database. That protects against bad migrations
  and mistakes, but not against losing the server.

---

## 7. Known limitations

* No users or authentication (see above).
* The rate limiter is per process and in memory: it resets on restart and isn't shared
  across replicas.
* A rollback doesn't undo a schema migration. Migrations must be backward compatible,
  and restoring a backup is a manual step.
* Backups are local to the VPS (no off-site copy), and restoring isn't automated or tested
  on a schedule.
* A single app instance means a few seconds of downtime while the container is replaced
  during a deploy. That's not a true zero-downtime blue/green deploy.
* Times are shown in UTC in the UI. There is no per-user timezone.
* Duplicate contacts are allowed (no uniqueness rule on email or phone), deliberately,
  since two contacts can legitimately share a company phone.
* The sslip.io hostname depends on a third-party DNS service. A real deployment would
  use its own domain.
* No metrics or alerting: health is checked at deploy time and by Docker, but nobody is
  paged if the app goes down later.

---

## 8. What I would improve with more time

1. **Authentication & authorization:** user accounts (OIDC, or sessions with argon2
   password hashing) for the UI, API keys or OAuth tokens per client for the API,
   per-user contact ownership, CSRF protection, and an admin-only logs page.
2. **Zero-downtime deploys:** run two app containers behind nginx (blue/green), switch
   the upstream only after the new one passes its health check, and drain the old one.
3. **Off-site, tested backups:** ship dumps to object storage (S3 or Backblaze) with
   encryption and lifecycle rules, and add a scheduled job that restores the latest dump
   into a scratch database to prove it works.
4. **Observability:** Prometheus metrics (request rate, latency histograms, errors by
   route), structured JSON logs, an uptime monitor, and alerting on `/health`, error rate
   and disk usage.
5. **Shared rate limiting** in Redis so the app can scale horizontally, plus a separate
   limit for reads.
6. **Staging environment** deployed automatically from pull requests, with production
   promotion behind manual approval (GitHub environment protection rules).
7. **Supply-chain hardening:** image vulnerability scanning (Trivy) in CI, Dependabot,
   GitHub Actions pinned to commit SHAs, image signing (cosign) and SBOM generation.
8. **Product:** importing a `.vcf` file, SVG and PDF QR downloads, a printable business-card
   layout, a "scan me" full-screen mode, and duplicate detection.
9. **GDPR:** a retention policy and export/erasure for contacts as well as logs, and IP
   anonymisation in logs after N days.
