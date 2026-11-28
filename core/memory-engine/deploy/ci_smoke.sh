#!/usr/bin/env bash
# Execute the deployment artefacts instead of only reading them (#253). CI's `deploy-smoke` job runs exactly this.
#
#   CME_TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1:5433/db deploy/ci_smoke.sh   # from core/memory-engine/  # pragma: allowlist secret
#
# Needs: docker with the compose plugin, systemd-analyze, uv with the engine synced, and a PostgreSQL + pgvector server
# at CME_TEST_DATABASE_URL reachable from this host (the containers use the host network, so 127.0.0.1 means the same
# server inside and outside). The URL must be owner-level: it creates a throwaway schema and a runtime role, and both are
# dropped on exit. Nothing reads a dotenv file.
#
# Steps, one per acceptance criterion of #253:
#   1. systemd   `systemd-analyze verify` on every unit and timer in deploy/ (staged --root), and
#                every CME_* variable a unit, the compose file or the Dockerfile names exists in .env.example and in
#                deploy/README.md (scripts/verify_deploy_artifacts.py --require-systemd)
#   2. build     `docker build -f deploy/Dockerfile.worker`, the command the Dockerfile documents
#   3. inspect   the image runs as a non-root user, carries no secret in its environment or history, and ships no
#                .env, .model-cache, .git, tests, PLAN or host bytecode; a derived image that breaks each rule must fail
#                the same inspection (the control)
#   4. database  migrate a throwaway schema with the production runner and provision its runtime role
#   5. image     the worker starts from the image and keeps serving, the health probe exits 0 on the empty queue (and 2
#                on an unreachable database: the control), `docker stop` (SIGTERM) exits 0, and a missing required
#                variable exits 2 naming the variable and never a value
#   6. compose   `docker compose -f deploy/compose.worker.yaml` builds through the file's own `build:` and `up -d` reaches
#                `healthy` through the file's own healthcheck
#
# No model provider is contacted: the queue stays empty, so CME_LLM_BASE_URL points at the discard port and is never
# dialled. The worker's model path is tested with a stub provider in tests/integration/test_worker_process_db.py.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${CME_TEST_DATABASE_URL:?CME_TEST_DATABASE_URL must name an owner-level PostgreSQL URL}"

SUFFIX="$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
SCHEMA="cme_deploysmoke_${SUFFIX}"
ROLE="cme_deploysmoke_rt_${SUFFIX}"
IMAGE="chronamind/worker:smoke-${SUFFIX}"
CONTROL_IMAGE="chronamind/worker:smoke-control-${SUFFIX}"
CONTAINER="cme-deploysmoke-${SUFFIX}"
PROJECT="cme-deploysmoke-${SUFFIX}"
PRINCIPAL="deploysmoke"
# Not a credential: the provider is never dialled. Present so the image's missing-variable check can prove it is never
# echoed back.
SMOKE_API_KEY="smoke-not-a-credential-${SUFFIX}"  # pragma: allowlist secret
WORK="$(mktemp -d)"
COMPOSE=(docker compose -p "$PROJECT" -f deploy/compose.worker.yaml -f "$WORK/compose.smoke.yaml" --env-file "$WORK/worker.env")
REQUIRED_VARS=(CME_DATABASE_URL CME_RUNTIME_ROLE CME_WORKER_USERS CME_LLM_BASE_URL CME_LLM_API_KEY CME_EXTRACTOR_MODEL)

step() { printf '\n== %s\n' "$*"; }
ok() { printf 'ok   %s\n' "$*"; }
fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

drop_database_objects() {
  RL_DATABASE_URL="$CME_TEST_DATABASE_URL" uv run --no-sync python - "$SCHEMA" "$ROLE" <<'PY'
import asyncio
import os
import sys

from psycopg import AsyncConnection, sql


async def main(schema: str, role: str) -> None:
    async with await AsyncConnection.connect(os.environ["RL_DATABASE_URL"], autocommit=True) as connection:
        await connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        cursor = await connection.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (role,))
        if await cursor.fetchone() is not None:
            await connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        cursor = await connection.execute(
            "SELECT (SELECT count(*) FROM pg_catalog.pg_namespace WHERE nspname = %s),"
            " (SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname = %s)",
            (schema, role),
        )
        print("cleanup: schema_left=%s role_left=%s" % await cursor.fetchone())


asyncio.run(main(*sys.argv[1:]))
PY
}

cleanup() {
  local status=$?
  set +e
  step "cleanup"
  if [ -f "$WORK/compose.smoke.yaml" ]; then
    "${COMPOSE[@]}" down --volumes --timeout 30 >/dev/null 2>&1
  fi
  docker rm -f "$CONTAINER" >/dev/null 2>&1
  docker image rm -f "$IMAGE" "$CONTROL_IMAGE" >/dev/null 2>&1
  drop_database_objects
  rm -rf "$WORK"
  printf 'removed %s, %s, %s, compose project %s\n' "$IMAGE" "$CONTROL_IMAGE" "$CONTAINER" "$PROJECT"
  exit "$status"
}
trap cleanup EXIT

# One line per violation of #253's image criterion; no output means the image is clean.
image_findings() {
  local image=$1 user uid
  user="$(docker image inspect -f '{{.Config.User}}' "$image")"
  case "$user" in
    "" | root | 0 | root:* | 0:*) echo "image_runs_as_root:config_user=${user:-unset}" ;;
  esac
  uid="$(docker run --rm --network none --entrypoint id "$image" -u)"
  [ "$uid" != 0 ] || echo "image_runs_as_root:uid=0"
  docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$image" | cut -d= -f1 |
    grep -E '(API_KEY|PASSWORD|SECRET|TOKEN|PROVENANCE_KEY|RECEIPT_KEY|DATABASE_URL)' |
    sed 's/^/image_env_names_secret:/' || true
  docker history --no-trunc --format '{{.CreatedBy}}' "$image" |
    grep -Eo '[A-Z0-9_]*(API_KEY|PASSWORD|SECRET|TOKENS?|PROVENANCE_KEY|RECEIPT_KEY|DATABASE_URL)=[^ "]+' |
    sed 's/=.*//; s/^/image_history_sets_secret:/' || true
  # As root, so an unreadable directory cannot hide a file from the scan.
  docker run --rm --network none --user 0 --entrypoint sh "$image" -c '
    find / -xdev \( -name .env -o -name ".env.*" -o -name .model-cache -o -name .git -o -name PLAN \) -print |
      sed "s/^/image_ships_forbidden_path:/"
    [ "$(ls -A /app | tr "\n" " ")" = ".venv scripts src " ] || echo "image_ships_repository:/app=$(ls -A /app | tr "\n" " ")"
    [ "$(ls -A /app/src | tr "\n" " ")" = "custom_memory_engine " ] || echo "image_ships_repository:/app/src"
    [ -z "$(ls -A /models)" ] || echo "image_ships_model_weights:/models"
    find /app/src /app/scripts \( -name __pycache__ -o -name "*.py[cod]" \) -print | head -n 1 |
      sed "s/^/image_ships_host_bytecode:/"
  '
  docker run --rm --network none --entrypoint sh "$image" -c \
    '[ ! -w /app/src ] && [ ! -w /app/scripts ] && [ ! -w /app/.venv ] || echo image_code_writable_by_runtime_user'
}

run_worker_image() {
  # --read-only and a /tmp tmpfs: the same filesystem the compose file gives the container.
  docker run "$@" --network host --read-only --tmpfs /tmp "$IMAGE"
}

# ---------------------------------------------------------------------------------------------------------------------
step "1/6 systemd units and the environment inventory"
uv run --no-sync python scripts/verify_deploy_artifacts.py --require-systemd
ok "systemd-analyze verify: cme-worker, cme-maintenance(.timer), cme-erasure-journal(.timer)"

# ---------------------------------------------------------------------------------------------------------------------
step "2/6 docker build -f deploy/Dockerfile.worker"
docker build --quiet -f deploy/Dockerfile.worker -t "$IMAGE" . >/dev/null
ok "built $IMAGE ($(docker image inspect -f '{{.Size}}' "$IMAGE") bytes)"

# ---------------------------------------------------------------------------------------------------------------------
step "3/6 image inspection"
findings="$(image_findings "$IMAGE")"
[ -z "$findings" ] || fail "image inspection: $findings"
ok "Config.User=$(docker image inspect -f '{{.Config.User}}' "$IMAGE"); id: $(docker run --rm --network none --entrypoint id "$IMAGE")"
ok "/app holds only .venv, scripts, src; no .env, .model-cache, .git, tests, PLAN, host bytecode or model weights"
ok "no secret-named variable in Config.Env or in any layer's history; code and venv not writable at runtime"

# The control: one derived image breaking every rule above must be caught on each of them.
docker build --quiet -t "$CONTROL_IMAGE" - >/dev/null <<EOF
FROM $IMAGE
USER root
ENV CME_LLM_API_KEY=leaked-into-the-image
RUN mkdir -p /app/.git /app/tests /models/bge-m3 /app/src/custom_memory_engine/__pycache__ \
 && touch /app/.env /app/scripts/.env.production /models/bge-m3/model.onnx \
 && chmod a+w /app/src
EOF
control="$(image_findings "$CONTROL_IMAGE")"
for expected in image_runs_as_root:config_user image_runs_as_root:uid image_env_names_secret:CME_LLM_API_KEY \
  image_history_sets_secret:CME_LLM_API_KEY image_ships_forbidden_path:/app/.env \
  image_ships_forbidden_path:/app/scripts/.env.production image_ships_forbidden_path:/app/.git \
  image_ships_repository:/app= image_ships_model_weights image_ships_host_bytecode image_code_writable_by_runtime_user; do
  grep -q "$expected" <<<"$control" || fail "control: the inspection did not catch $expected; it reported: $control"
done
ok "control: a root image carrying a key, .env, .git, tests, weights and bytecode fails all 11 inspection rules"
docker image rm -f "$CONTROL_IMAGE" >/dev/null

# ---------------------------------------------------------------------------------------------------------------------
step "4/6 throwaway schema $SCHEMA and runtime role $ROLE"
RL_DATABASE_URL="$CME_TEST_DATABASE_URL" uv run --no-sync python -m custom_memory_engine.schema.migrate apply \
  --schema "$SCHEMA" >"$WORK/migrate.json"
jq -e '.status.state == "applied"' "$WORK/migrate.json" >/dev/null || fail "migrate apply: $(cat "$WORK/migrate.json")"
ok "migrate apply: version $(jq -r '.status.version' "$WORK/migrate.json"), state applied"
RL_DATABASE_URL="$CME_TEST_DATABASE_URL" uv run --no-sync python scripts/provision_role.py \
  --schema "$SCHEMA" --role "$ROLE"
ok "provision_role.py: $ROLE"

umask 077
cat >"$WORK/worker.env" <<EOF
CME_DATABASE_URL=$CME_TEST_DATABASE_URL
CME_RUNTIME_ROLE=$ROLE
CME_SCHEMA=$SCHEMA
CME_WORKER_USERS=$PRINCIPAL
CME_LLM_BASE_URL=http://127.0.0.1:9/v1
CME_LLM_API_KEY=$SMOKE_API_KEY
CME_EXTRACTOR_MODEL=stub-extractor
CME_WORKER_IDLE_POLL=1
EOF

# ---------------------------------------------------------------------------------------------------------------------
step "5/6 the worker image against the database"
run_worker_image -d --name "$CONTAINER" --env-file "$WORK/worker.env" >/dev/null
# Eight idle polls. A worker that cannot connect exits; one that connects but cannot act as its role keeps running and
# logs a failed tick or recovery on every poll (worker/loop.py catches both), so the log must have neither.
sleep 8
[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" = true ] ||
  fail "worker exited: code $(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER"); $(docker logs "$CONTAINER" 2>&1)"
if docker logs "$CONTAINER" 2>&1 | grep -E '"slow_lane_(tick|recovery)_failed"'; then
  fail "the worker runs but cannot serve its queue"
fi
ok "process started and served 8 idle polls without a failed tick (pid 1: $(docker exec "$CONTAINER" cat /proc/1/cmdline | tr '\0' ' '))"

set +e
probe="$(docker exec "$CONTAINER" /app/.venv/bin/python scripts/worker_healthcheck.py 2>&1)"
probe_status=$?
unreachable="$(docker exec -e CME_DATABASE_URL=postgresql://nobody@127.0.0.1:1/none "$CONTAINER" \
  /app/.venv/bin/python scripts/worker_healthcheck.py 2>&1)"
unreachable_status=$?
set -e
[ "$probe_status" = 0 ] || fail "health probe on the empty queue exited $probe_status: $probe"
ok "health probe on the empty queue: exit 0 ($probe)"
[ "$unreachable_status" = 2 ] || fail "control: probe with an unreachable database exited $unreachable_status"
ok "control: health probe with an unreachable database: exit 2 ($unreachable)"

docker stop --timeout 30 "$CONTAINER" >/dev/null
stop_code="$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")"
[ "$stop_code" = 0 ] || fail "SIGTERM exit code $stop_code"
docker logs "$CONTAINER" 2>&1 | grep -q '"slow_lane_stopped"' || fail "no slow_lane_stopped event after SIGTERM"
ok "docker stop (SIGTERM): exit 0 after the loop logged slow_lane_stopped"
docker rm "$CONTAINER" >/dev/null

for name in "${REQUIRED_VARS[@]}"; do
  grep -v "^${name}=" "$WORK/worker.env" >"$WORK/missing.env"
  set +e
  output="$(run_worker_image --rm --env-file "$WORK/missing.env" 2>&1)"
  code=$?
  set -e
  expected="$(tr '[:upper:]' '[:lower:]' <<<"$name")_required"
  [ "$code" = 2 ] || fail "without $name the image exited $code: $output"
  grep -qx "$expected" <<<"$output" || fail "without $name the image did not name it: $output"
  if grep -qF -e "$SMOKE_API_KEY" -e "$CME_TEST_DATABASE_URL" <<<"$output"; then
    fail "without $name the image echoed a configured value"
  fi
  ok "without $name: exit 2, stderr '$expected', no configured value echoed"
done

# ---------------------------------------------------------------------------------------------------------------------
step "6/6 docker compose -f deploy/compose.worker.yaml up -d, until healthy"
# The override changes only where the container runs, never what it runs: the image this script built (from the same
# Dockerfile and context the compose file names) and the host network, so the test database on 127.0.0.1 is reachable.
# The healthcheck, read-only root, tmpfs, env_file, interpolated variables, model volume and grace period are the file's.
mkdir -m 0755 "$WORK/model-cache"
cat >"$WORK/compose.smoke.yaml" <<EOF
services:
  worker:
    image: $IMAGE
    pull_policy: never
    network_mode: host
EOF
export WORKER_ENV_FILE="$WORK/worker.env" CME_MODEL_CACHE="$WORK/model-cache"
"${COMPOSE[@]}" config --quiet
# Through the file's own `build:` (context `..`, `deploy/Dockerfile.worker`); every layer is cached from step 2.
"${COMPOSE[@]}" build --quiet
"${COMPOSE[@]}" up -d --no-build
worker_id="$("${COMPOSE[@]}" ps -q worker)"
health=starting
for _ in $(seq 1 60); do
  health="$(docker inspect -f '{{.State.Health.Status}}' "$worker_id")"
  [ "$health" = starting ] || break
  sleep 3
done
"${COMPOSE[@]}" ps
[ "$health" = healthy ] || fail "compose worker is '$health': $(docker inspect -f '{{json .State.Health}}' "$worker_id")"
ok "compose worker is healthy: $(docker inspect -f '{{(index .State.Health.Log 0).Output}}' "$worker_id" | tr -d '\n')"
"${COMPOSE[@]}" down --volumes --timeout 30

step "deploy smoke passed"
