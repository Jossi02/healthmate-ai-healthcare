#!/usr/bin/env bash
set -euo pipefail
umask 077

ARCHIVE_PATH="${2:-/tmp/healthmate-deploy.tar.gz}"
APP_ROOT="/opt/healthmate"
STAGE_DIR=""

is_private_ipv4() {
  local address="${1}" first second third fourth octet

  [[ "${address}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  IFS=. read -r first second third fourth <<< "${address}"
  for octet in "${first}" "${second}" "${third}" "${fourth}"; do
    (( 10#${octet} <= 255 )) || return 1
  done
  first=$((10#${first}))
  second=$((10#${second}))
  (( first == 10 || (first == 172 && second >= 16 && second <= 31) || (first == 192 && second == 168) ))
}

if [ "${1:-}" = "--self-test" ]; then
  is_private_ipv4 10.0.0.1
  is_private_ipv4 172.31.255.254
  is_private_ipv4 192.168.1.1
  ! is_private_ipv4 0.0.0.0
  ! is_private_ipv4 127.0.0.1
  ! is_private_ipv4 8.8.8.8
  ! is_private_ipv4 10.0.0.999
  echo "bootstrap self-test passed"
  exit 0
fi

if [ "${ARCHIVE_PATH}" != "/tmp/healthmate-deploy.tar.gz" ]; then
  echo "Unexpected archive path" >&2
  exit 2
fi
if [ ! -f "${ARCHIVE_PATH}" ]; then
  echo "Deployment archive is missing" >&2
  exit 2
fi
chmod 600 "${ARCHIVE_PATH}"

cleanup() {
  if [ -n "${STAGE_DIR}" ]; then
    rm -rf -- "${STAGE_DIR}"
  fi
  rm -f -- "${ARCHIVE_PATH}"
}
trap cleanup EXIT

SERVICE="${1:?service is required: backend or ai}"
case "${SERVICE}" in
  backend)
    COMPOSE_REL="develop/deploy/gcp-two-vm/backend"
    ENV_NAME=".env.backend"
    COMPOSE_SERVICE="backend"
    PERSIST_DIR="/var/lib/healthmate/backend/logs"
    PERSIST_UID=1000
    PERSIST_GID=1000
    ;;
  ai)
    COMPOSE_REL="develop/deploy/gcp-two-vm/ai"
    ENV_NAME=".env.ai"
    COMPOSE_SERVICE="ai-hub-v2"
    PERSIST_DIR="/var/lib/healthmate/ai/data"
    PERSIST_UID=10001
    PERSIST_GID=10001
    ;;
  *)
    echo "Unknown service: ${SERVICE}" >&2
    exit 2
    ;;
esac

TARGET_DIR="${APP_ROOT}/${SERVICE}"

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi

  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg lsb-release
  sudo install -m 0755 -d /etc/apt/keyrings

  if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  ARCH="$(dpkg --print-architecture)"
  CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"

  echo \
    "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null

  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable docker
  sudo systemctl start docker
}

migrate_existing_state() {
  local old_state old_compose old_env suffix

  sudo install -d -m 0750 -o "${PERSIST_UID}" -g "${PERSIST_GID}" "${PERSIST_DIR}"

  if [ "${SERVICE}" = "backend" ]; then
    old_state="${TARGET_DIR}/develop/backend-api/logs"
    if [ -d "${old_state}" ]; then
      old_compose="${TARGET_DIR}/${COMPOSE_REL}/docker-compose.yml"
      old_env="${TARGET_DIR}/${COMPOSE_REL}/${ENV_NAME}"
      if [ ! -f "${old_compose}" ] || [ ! -f "${old_env}" ]; then
        echo "Cannot safely stop the existing Backend before log migration" >&2
        exit 2
      fi
      (
        cd "$(dirname "${old_compose}")"
        sudo docker compose --env-file "${old_env}" stop backend
      )
      sudo cp -an "${old_state}"/. "${PERSIST_DIR}"/
    fi
  else
    old_state="${TARGET_DIR}/develop/ai-model/v2/data"
    old_compose="${TARGET_DIR}/${COMPOSE_REL}/docker-compose.yml"
    old_env="${TARGET_DIR}/${COMPOSE_REL}/${ENV_NAME}"
    if [ -d "${old_state}" ]; then
      if [ ! -f "${old_compose}" ] || [ ! -f "${old_env}" ]; then
        echo "Cannot safely stop the existing AI service before checkpoint migration" >&2
        exit 2
      fi
      (
        cd "$(dirname "${old_compose}")"
        sudo docker compose --env-file "${old_env}" stop ai-hub-v2
      )
      for suffix in "" "-wal" "-shm"; do
        if [ -f "${old_state}/checkpoints.sqlite${suffix}" ]; then
          sudo cp -an "${old_state}/checkpoints.sqlite${suffix}" "${PERSIST_DIR}/"
        fi
      done
    fi
  fi

  sudo chown -R "${PERSIST_UID}:${PERSIST_GID}" "${PERSIST_DIR}"
  sudo chmod 0750 "${PERSIST_DIR}"
}

show_failure_context() {
  local reason="${1}"
  echo "Deployment failed: ${reason}" >&2
  set +e
  "${COMPOSE[@]}" ps
  "${COMPOSE[@]}" logs --no-color --tail 80
  set -e
  return 1
}

validate_stage() {
  local compose_file="${1}" stage_env="${2}"
  local ai_bind_address backend_domain

  sudo docker compose --env-file "${stage_env}" -f "${compose_file}" config --quiet

  if [ "${SERVICE}" = "ai" ]; then
    if [ "$(grep -c '^AI_BIND_ADDRESS=' "${stage_env}" || true)" -ne 1 ]; then
      echo "AI_BIND_ADDRESS must appear exactly once in ${ENV_NAME}" >&2
      exit 2
    fi
    ai_bind_address="$(sed -n 's/^AI_BIND_ADDRESS=//p' "${stage_env}")"
    ai_bind_address="${ai_bind_address%$'\r'}"
    if ! is_private_ipv4 "${ai_bind_address}"; then
      echo "AI_BIND_ADDRESS must be an unquoted RFC1918 IPv4 address" >&2
      exit 2
    fi
    if ! command -v ip >/dev/null 2>&1 ||
      ! grep -Fqx -- "${ai_bind_address}" < <(ip -o -4 addr show scope global | awk '{sub(/\/.*/, "", $4); print $4}'); then
      echo "AI_BIND_ADDRESS is not assigned to a private interface on this VM" >&2
      exit 2
    fi
  else
    if [ "$(grep -c '^BACKEND_DOMAIN=' "${stage_env}" || true)" -ne 1 ]; then
      echo "BACKEND_DOMAIN must appear exactly once in ${ENV_NAME}" >&2
      exit 2
    fi
    backend_domain="$(sed -n 's/^BACKEND_DOMAIN=//p' "${stage_env}")"
    backend_domain="${backend_domain%$'\r'}"
    sudo docker run --rm \
      -e "BACKEND_DOMAIN=${backend_domain}" \
      -v "${STAGE_DIR}/${COMPOSE_REL}/Caddyfile:/etc/caddy/Caddyfile:ro" \
      caddy:2-alpine \
      caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  fi
}

deploy_service() {
  local compose_file stage_env owner

  STAGE_DIR="$(mktemp -d /tmp/healthmate-stage.XXXXXX)"
  tar -xzf "${ARCHIVE_PATH}" -C "${STAGE_DIR}"

  compose_file="${STAGE_DIR}/${COMPOSE_REL}/docker-compose.yml"
  stage_env="${STAGE_DIR}/${COMPOSE_REL}/${ENV_NAME}"
  if [ ! -f "${compose_file}" ] || [ ! -f "${stage_env}" ]; then
    echo "Archive is missing the expected Compose or environment file" >&2
    exit 2
  fi
  chmod 600 "${stage_env}"

  validate_stage "${compose_file}" "${stage_env}"
  migrate_existing_state

  sudo mkdir -p "${APP_ROOT}"
  sudo rm -rf -- "${TARGET_DIR}"
  sudo mkdir -p "${TARGET_DIR}"
  sudo cp -a "${STAGE_DIR}"/. "${TARGET_DIR}"/
  owner="$(id -u):$(id -g)"
  sudo chown -R "${owner}" "${TARGET_DIR}"

  COMPOSE_DIR="${TARGET_DIR}/${COMPOSE_REL}"
  ENV_FILE="${COMPOSE_DIR}/${ENV_NAME}"
  chmod 600 "${ENV_FILE}"
  cd "${COMPOSE_DIR}"
  COMPOSE=(sudo docker compose --env-file "${ENV_FILE}")

  if ! "${COMPOSE[@]}" up -d --build --remove-orphans --wait --wait-timeout 180; then
    show_failure_context "${COMPOSE_SERVICE} did not become healthy within 180 seconds"
  fi

  if [ "${SERVICE}" = "backend" ] && ! "${COMPOSE[@]}" exec -T backend \
    wget -T 20 -t 1 -qO- http://127.0.0.1:8080/api/readiness >/dev/null; then
    show_failure_context "backend readiness check failed"
  fi

  "${COMPOSE[@]}" ps
}

install_docker
deploy_service
