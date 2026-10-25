# 3. Persist request logs from ASGI middleware, after the response is sent

Date: 2026-10-25 · Status: Accepted

## Context
Every HTTP request (UI and API) must be logged persistently with timestamp, IP, method,
path, status, user agent, source and duration, and the logs must be queryable.

## Decision
* A pure ASGI middleware wraps the whole app and records the final status of every
  response, including errors and static files.
* The row is inserted into `request_logs` after the response has been sent, in a worker
  thread, and is shielded from client-disconnect cancellation.
* A failed insert is logged to stdout and never fails the request.
* Source is classified by path: `/api/*` is `API`, `/health` is `SYSTEM`, everything else
  is `UI`.
* Old rows are deleted by a daily retention job (`LOG_RETENTION_DAYS`, default 90).

## Consequences
* Logging adds no response latency, and an outage of logging can't take the app down.
* During a database outage, log rows exist only in the container's stdout.
* Logs share the main database. That's simple and SQL-queryable, with retention keeping
  the size bounded. A dedicated log store would be better at high volume.
