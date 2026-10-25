#!/usr/bin/env bash
# One-time server preparation (run as root on the VPS). Idempotent: safe to re-run.
#
#   scp deploy/bootstrap-server.sh deploy/nginx-contact-qr.conf root@<server>:/tmp/
#   ssh root@<server> 'bash /tmp/bootstrap-server.sh "<ci-deploy-public-key>"'
#
# Creates:
#   - user `contactqr` (docker group, no password, SSH key login only) used by CI
#   - /opt/contact-qr with a generated .env (random DB password, mode 600)
#   - the external Docker volume `contact-qr_pgdata` holding all persistent data
#   - an nginx virtual host + Let's Encrypt certificate
set -Eeuo pipefail

DEPLOY_PUBKEY="${1:?usage: bootstrap-server.sh '<ssh public key for CI>'}"
APP_USER=contactqr
APP_DIR=/opt/contact-qr
DOMAIN=contact-qr.46-202-189-105.sslip.io

echo "==> user $APP_USER"
id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"
usermod -aG docker "$APP_USER"
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
AUTH="/home/$APP_USER/.ssh/authorized_keys"
touch "$AUTH"
grep -qxF "$DEPLOY_PUBKEY" "$AUTH" || echo "$DEPLOY_PUBKEY" >>"$AUTH"
chown "$APP_USER:$APP_USER" "$AUTH"
chmod 600 "$AUTH"

echo "==> $APP_DIR"
install -d -m 750 -o "$APP_USER" -g "$APP_USER" "$APP_DIR" "$APP_DIR/backups" "$APP_DIR/.deploy"
if [[ ! -f "$APP_DIR/.env" ]]; then
  umask 077
  cat >"$APP_DIR/.env" <<EOF
POSTGRES_USER=contactqr
POSTGRES_DB=contactqr
POSTGRES_PASSWORD=$(openssl rand -hex 32)
LOG_LEVEL=INFO
DEFAULT_PHONE_REGION=LT
LOG_RETENTION_DAYS=90
RATE_LIMIT_WRITES_PER_MINUTE=30
EOF
  chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
  echo "generated $APP_DIR/.env"
fi

echo "==> docker volume"
docker volume inspect contact-qr_pgdata >/dev/null 2>&1 || docker volume create contact-qr_pgdata

echo "==> nginx"
install -m 644 /tmp/nginx-contact-qr.conf /etc/nginx/sites-available/contact-qr
ln -sf /etc/nginx/sites-available/contact-qr /etc/nginx/sites-enabled/contact-qr
nginx -t
systemctl reload nginx

echo "==> TLS certificate"
if [[ ! -d "/etc/letsencrypt/live/$DOMAIN" ]]; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect \
    --register-unsafely-without-email --keep-until-expiring
else
  certbot install --nginx --cert-name "$DOMAIN" --redirect --non-interactive || true
fi
nginx -t
systemctl reload nginx

echo "==> done. https://$DOMAIN will serve the app after the first CI deploy."
