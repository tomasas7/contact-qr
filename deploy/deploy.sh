#!/usr/bin/env bash
# Zero-data-loss deploy of one image tag, with automatic rollback.
# Runs on the server as the `contactqr` user, invoked by CI:  ./deploy.sh sha-abc1234
#
#   1. pull the new image
#   2. back up the database (pg_dump, keep the last $KEEP_BACKUPS)
#   3. run migrations (one-shot container)
#   4. replace the app container
#   5. health gate: /health must be OK *and* report the new version
#   6. on failure: put the previous image back and exit non-zero (CI goes red)
#
# Data lives in the external Docker volume `contact-qr_pgdata`, which no step here
# touches, so contacts, QR codes and request logs survive every deploy and rollback.
set -Eeuo pipefail

NEW_TAG="${1:?usage: deploy.sh <image-tag>}"
APP_DIR="${APP_DIR:-/opt/contact-qr}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8100/health}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
KEEP_IMAGES="${KEEP_IMAGES:-3}"
IMAGE_REPO="ghcr.io/tomasas7/contact-qr"

cd "$APP_DIR"
mkdir -p backups .deploy
PREV_TAG="$(cat .deploy/current_tag 2>/dev/null || true)"

log() { printf '\n==> %s\n' "$*"; }
compose() { IMAGE_TAG="$1" docker compose -f docker-compose.prod.yml "${@:2}"; }

wait_healthy() {  # wait_healthy <expected-version-substring>
  local tag="$1" body
  for _ in $(seq 1 30); do
    if body="$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null)" \
      && grep -q '"status":"ok"' <<<"$body" && grep -q "${tag#sha-}" <<<"$body"; then
      echo "healthy: $body"
      return 0
    fi
    sleep 2
  done
  echo "NOT healthy after 60s. Last response: ${body:-<none>}"
  return 1
}

log "Deploying $NEW_TAG (previous: ${PREV_TAG:-none})"
compose "$NEW_TAG" pull app

log "Starting database"
compose "$NEW_TAG" up -d --wait db

log "Backing up database"
# shellcheck disable=SC1091
source .env
backup="backups/contactqr-$(date -u +%Y%m%dT%H%M%SZ)-before-${NEW_TAG}.dump"
compose "$NEW_TAG" exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom >"$backup"
echo "wrote $backup ($(du -h "$backup" | cut -f1))"
# shellcheck disable=SC2012  # our own timestamped filenames; ls -t gives newest first
ls -1t backups/*.dump | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm --

log "Running migrations"
compose "$NEW_TAG" run --rm migrate

log "Starting app $NEW_TAG"
compose "$NEW_TAG" up -d --no-deps app

log "Health gate"
if ! wait_healthy "$NEW_TAG"; then
  compose "$NEW_TAG" logs --tail 50 app || true
  if [[ -n "$PREV_TAG" ]]; then
    log "ROLLING BACK to $PREV_TAG"
    compose "$PREV_TAG" up -d --no-deps app
    wait_healthy "$PREV_TAG" || echo "WARNING: previous version is not healthy either"
    echo "Note: if the failed release changed the schema, restore $backup (see README)."
  fi
  exit 1
fi

echo "$PREV_TAG" >.deploy/previous_tag
echo "$NEW_TAG" >.deploy/current_tag

log "Pruning old images (keeping current, previous and the newest $KEEP_IMAGES)"
docker images "$IMAGE_REPO" --format '{{.Tag}}' | grep -vxF -e "$NEW_TAG" -e "${PREV_TAG:-__none__}" \
  | tail -n +"$((KEEP_IMAGES + 1))" | xargs -r -I{} docker rmi "$IMAGE_REPO:{}" || true

log "Deployed $NEW_TAG"
