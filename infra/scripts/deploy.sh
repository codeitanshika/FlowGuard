#!/usr/bin/env bash
# Deploys one already-published image tag to the single-VM target
# (ADR-0020) over SSH. Used identically by .github/workflows/promote.yml
# (a new release) and rollback.yml (reverting to an older one) — the only
# difference between a deploy and a rollback is which tag this is given;
# neither workflow duplicates the SSH/compose logic itself.
#
# Never builds anything: pulls IMAGE_TAG from IMAGE_REGISTRY and runs
# `docker compose up` on the VM against the images+prod overlays — see
# docker-compose.images.yml / docker-compose.prod.yml.
#
# Required env vars (set from GitHub Secrets by the calling workflow):
#   DEPLOY_HOST        VM's IP or hostname (Terraform output: public_ip)
#   DEPLOY_SSH_KEY      private key matching the VM's authorized key
#   DEPLOY_HOST_KEY       `ssh-keyscan` output for DEPLOY_HOST (Terraform
#                         output: ssh_keyscan_command) — pins the real host
#                         key instead of trust-on-first-connect; this script
#                         refuses to fall back to blind trust if it's unset.
#   IMAGE_REGISTRY      e.g. ghcr.io/<owner>
#   PRODUCTION_ENV_FILE    full contents of the production secrets file
#                          (POSTGRES_PASSWORD, GATEWAY_JWT_SECRET,
#                          GATEWAY_CLIENTS, OPS_HEALER_TOKEN, DOMAIN_NAME,
#                          ...) — see docs/deployment/setup-guide.md for
#                          the format (shell-sourced: single-quote any
#                          value containing a literal `$`, e.g. bcrypt
#                          hashes in GATEWAY_CLIENTS).
# Optional:
#   DEPLOY_USER (default "ubuntu")
#
# Usage: infra/scripts/deploy.sh <image-tag> <domain-for-health-check>

set -euo pipefail

IMAGE_TAG="${1:?usage: deploy.sh <image-tag> <domain-for-health-check>}"
HEALTH_DOMAIN="${2:?usage: deploy.sh <image-tag> <domain-for-health-check>}"
DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
REMOTE_DIR="/opt/flowguard"

: "${DEPLOY_HOST:?set DEPLOY_HOST}"
: "${DEPLOY_SSH_KEY:?set DEPLOY_SSH_KEY}"
: "${DEPLOY_HOST_KEY:?set DEPLOY_HOST_KEY - run ssh-keyscan against the VM once (see Terraforms ssh_keyscan_command output) and store the result as this secret; this script will not fall back to trust-on-first-connect}"
: "${IMAGE_REGISTRY:?set IMAGE_REGISTRY}"
: "${PRODUCTION_ENV_FILE:?set PRODUCTION_ENV_FILE}"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

ssh_key_file="$workdir/deploy_key"
printf '%s\n' "$DEPLOY_SSH_KEY" >"$ssh_key_file"
chmod 600 "$ssh_key_file"

known_hosts_file="$workdir/known_hosts"
printf '%s\n' "$DEPLOY_HOST_KEY" >"$known_hosts_file"

ssh_opts=(-i "$ssh_key_file" -o UserKnownHostsFile="$known_hosts_file" -o StrictHostKeyChecking=yes)
remote="${DEPLOY_USER}@${DEPLOY_HOST}"

echo "==> Copying compose files to ${remote}:${REMOTE_DIR}"
# shellcheck disable=SC2029 # REMOTE_DIR is a fixed local constant, not a remote-side var; client-side expansion is intended
ssh "${ssh_opts[@]}" "$remote" "mkdir -p ${REMOTE_DIR}/infra/docker"
scp "${ssh_opts[@]}" docker-compose.yml "$remote:${REMOTE_DIR}/docker-compose.yml"
scp "${ssh_opts[@]}" Caddyfile "$remote:${REMOTE_DIR}/Caddyfile"
scp "${ssh_opts[@]}" infra/docker/docker-compose.images.yml "$remote:${REMOTE_DIR}/infra/docker/docker-compose.images.yml"
scp "${ssh_opts[@]}" infra/docker/docker-compose.prod.yml "$remote:${REMOTE_DIR}/infra/docker/docker-compose.prod.yml"

echo "==> Writing the production secrets file (not logged, not stored in this repo)"
env_file="$workdir/.env.production"
printf '%s\n' "$PRODUCTION_ENV_FILE" >"$env_file"
# Catches a quoting mistake (e.g. an un-quoted `$` in GATEWAY_CLIENTS) here,
# with a clear error, rather than as a cryptic failure after `source` on
# the VM has already partially run.
bash -n "$env_file" || {
  echo "::error::PRODUCTION_ENV_FILE is not valid shell — check quoting (any value containing \$ needs single quotes)" >&2
  exit 1
}
scp "${ssh_opts[@]}" "$env_file" "$remote:${REMOTE_DIR}/.env.production"
# shellcheck disable=SC2029 # REMOTE_DIR is a fixed local constant, not a remote-side var; client-side expansion is intended
ssh "${ssh_opts[@]}" "$remote" "chmod 600 ${REMOTE_DIR}/.env.production"

echo "==> Deploying image tag ${IMAGE_TAG} from ${IMAGE_REGISTRY}"
# shellcheck disable=SC2087 # IMAGE_REGISTRY/IMAGE_TAG are meant to expand
# locally (they select *this* deploy's target); only the vars inside
# .env.production are meant to expand on the remote side, via `source`.
ssh "${ssh_opts[@]}" "$remote" IMAGE_REGISTRY="$IMAGE_REGISTRY" IMAGE_TAG="$IMAGE_TAG" bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
cd /opt/flowguard
set -a
# shellcheck disable=SC1091
source .env.production
set +a
compose=(docker compose -f docker-compose.yml -f infra/docker/docker-compose.images.yml -f infra/docker/docker-compose.prod.yml)
"${compose[@]}" pull
"${compose[@]}" up -d --no-build --wait --wait-timeout 300
"${compose[@]}" image prune -f
REMOTE_SCRIPT

echo "==> Verifying https://${HEALTH_DOMAIN}/health through Caddy"
for _ in $(seq 1 10); do
  if curl -fsS "https://${HEALTH_DOMAIN}/health" >/dev/null; then
    echo "Deploy of ${IMAGE_TAG} succeeded."
    exit 0
  fi
  sleep 5
done

echo "::error::https://${HEALTH_DOMAIN}/health did not become healthy after deploying ${IMAGE_TAG}" >&2
# shellcheck disable=SC2029 # REMOTE_DIR is a fixed local constant, not a remote-side var; client-side expansion is intended
ssh "${ssh_opts[@]}" "$remote" "cd ${REMOTE_DIR} && docker compose -f docker-compose.yml -f infra/docker/docker-compose.images.yml -f infra/docker/docker-compose.prod.yml ps" || true
exit 1
