# 4. Deploy behind the host's nginx, with an external data volume and a health-gated rollback

Date: 2026-10-25 · Status: Accepted (supersedes the initial plan to run Caddy in Compose)

## Context
The VPS is shared: it already runs nginx on ports 80/443 (other sites, certbot
certificates), several other services, and a host PostgreSQL on 5432. Redeploys must
never delete data.

## Decision
* Add one nginx virtual host (`contact-qr.46-202-189-105.sslip.io`) with a Let's Encrypt
  certificate. It proxies to the app on `127.0.0.1:8100`. No second reverse proxy.
* Run the app and its own PostgreSQL as an isolated Compose project with a private,
  fixed-subnet network. The DB isn't published to the host.
* Declare the data volume `external: true` (created once by `bootstrap-server.sh`), so
  Compose can never delete it.
* CI builds images; the server only pulls them. `deploy.sh` runs, in order: backup
  (`pg_dump`), migrate, replace the app container, then a health gate that checks the new
  version is serving. If the gate fails, it rolls back to the previous tag automatically.
* CI connects as an unprivileged `contactqr` user with its own SSH key, never as root.

## Consequences
* Nothing else on the server was modified apart from one new nginx site file.
* Data survives deploys, rollbacks and `docker compose down -v`.
* A few seconds of downtime while the single app container is swapped (no blue/green).
* An image rollback doesn't undo schema changes, so migrations must be backward
  compatible, with the pre-deploy dump as the recovery path.
