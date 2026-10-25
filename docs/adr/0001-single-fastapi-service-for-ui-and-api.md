# 1. One FastAPI service serves both the web UI and the REST API

Date: 2026-10-25 · Status: Accepted

## Context
The brief requires a web UI and a REST API with equal functionality, OpenAPI-style
documentation, and request logs that tell UI traffic apart from API traffic.

## Decision
A single FastAPI application serves server-rendered Jinja2 pages and the JSON API
(`/api/v1`). Both are thin adapters over one service layer and one validation model
(`ContactIn`). The UI doesn't call the API over HTTP.

## Consequences
* Equal functionality and identical validation come from the structure itself, not
  from discipline.
* OpenAPI docs are generated from the code, so they can't drift.
* One image and one container: no SPA build, no CORS.
* UI and API traffic live in separate URL spaces, so log source classification is a
  simple path rule (`/api/*` → API).
* Not suited to a rich client-side UI. That would need a SPA, which would then call the API.
