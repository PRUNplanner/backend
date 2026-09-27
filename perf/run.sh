#!/usr/bin/env bash
# Benchmark and load-test the backend against a throwaway, seeded Postgres.
#
#   perf/run.sh [--scale tiny|small|medium|large|prod] [--mode quick|full]
#               [--users 50] [--spawn-rate 10] [--duration 60s] [--workers 3]
#               [--only planning,data.planet] [--no-snapshot] [--keep]
#               [--prod-like]
#
# quick: seed + in-process endpoint benchmarks (query counts, latency)
# full:  quick + a Locust load test against gunicorn (production server config)
#
# Results land in .perf/<timestamp>-<scale>-<mode>/ and are compared with the
# previous run of the same scale, mode and game data source. Needs Docker running.
#
# Game data is real, from the FIO snapshot in perf/snapshot/ (downloaded on the
# first run by perf_snapshot); --no-snapshot seeds fake game data. See perf/README.md.
#
# --prod-like: db, redis and gunicorn in containers limited like production
# (2 shared cores, prod memory limits, default Postgres on disk); see
# perf/docker-compose.prod-like.yml. Compared only with other --prod-like runs.
set -euo pipefail

SCALE=small
MODE=full
USERS=50
SPAWN_RATE=10
DURATION=60s
WORKERS=3
ONLY=''
KEEP=0
NO_SNAPSHOT=0
PROD_LIKE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scale) SCALE="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --users) USERS="$2"; shift 2 ;;
    --spawn-rate) SPAWN_RATE="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --no-snapshot) NO_SNAPSHOT=1; shift ;;
    --prod-like) PROD_LIKE=1; shift ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in quick|full) ;; *) echo "--mode must be quick or full" >&2; exit 2 ;; esac

cd "$(dirname "$0")/.."  # backend repo root

ENV_FILE=perf/env.perf
COMPOSE="docker compose -f perf/docker-compose.perf.yml"
[[ "$PROD_LIKE" == 1 ]] && COMPOSE="$COMPOSE -f perf/docker-compose.prod-like.yml"
PORT=8765
RUN_DIR=".perf/$(date +%Y%m%d-%H%M%S)-${SCALE}-${MODE}"
SERVER_PID=''

step() { printf '\n==> %s\n' "$*"; }
manage() { uv run --env-file "$ENV_FILE" backend/manage.py "$@"; }

# preflight
command -v docker >/dev/null || { echo "docker is not installed." >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop (or OrbStack/Colima) and retry." >&2; exit 1; }
command -v uv >/dev/null || { echo "uv is not installed." >&2; exit 1; }
if [[ "$MODE" == full ]] && command -v lsof >/dev/null && lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $PORT is in use; stop whatever is listening there." >&2
  exit 1
fi

cleanup() {
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [[ "$PROD_LIKE" == 1 && "$MODE" == full ]]; then
    $COMPOSE logs --no-color app > "$RUN_DIR/server.log" 2>&1 || true
  fi
  if [[ "$KEEP" == 0 ]]; then
    $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
  else
    echo "Perf stack left running (--keep). Stop it with: $COMPOSE down -v"
  fi
}
trap cleanup EXIT

# game data: the FIO snapshot, downloaded once; fake with --no-snapshot or when FIO is unreachable
SNAPSHOT=perf/snapshot/gamedata.json.gz
if [[ "$NO_SNAPSHOT" == 0 && ! -f "$SNAPSHOT" ]]; then
  step "Downloading the FIO game data snapshot (first run only)"
  manage perf_snapshot || echo "Snapshot download failed; seeding fake game data instead." >&2
fi
if [[ "$NO_SNAPSHOT" == 0 && -f "$SNAPSHOT" ]]; then SOURCE=snapshot; else SOURCE=fake; fi

mkdir -p "$RUN_DIR"
cat > "$RUN_DIR/meta.json" <<JSON
{
  "scale": "$SCALE",
  "mode": "$MODE",
  "source": "$SOURCE",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "git_sha": "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)",
  "git_branch": "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)",
  "git_dirty": $([[ -n "$(git status --porcelain 2>/dev/null)" ]] && echo true || echo false),
  "only": "$ONLY",
  "prod_like": $([[ "$PROD_LIKE" == 1 ]] && echo true || echo false),
  "load": {"users": $USERS, "spawn_rate": $SPAWN_RATE, "duration": "$DURATION", "workers": $WORKERS}
}
JSON

step "Starting Postgres + Redis"
$COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
$COMPOSE up -d --wait db redis

step "Migrating"
manage migrate --noinput -v 0

step "Seeding scale=$SCALE, game data: $SOURCE"
manage seed_perf --scale "$SCALE" --source "$SOURCE" --flush --targets-out "$RUN_DIR/targets.json"

step "Benchmarking endpoints (in-process)"
manage perf_bench --out "$RUN_DIR/bench.json" ${ONLY:+--only "$ONLY"}

if [[ "$MODE" == full ]]; then
  step "Starting gunicorn on :$PORT with $WORKERS workers"
  if [[ "$PROD_LIKE" == 1 ]]; then
    GUNICORN_WORKERS="$WORKERS" $COMPOSE up -d --build app
  else
    uv run --env-file "$ENV_FILE" gunicorn -c backend/gunicorn.conf.py \
      --bind "127.0.0.1:$PORT" --workers "$WORKERS" > "$RUN_DIR/server.log" 2>&1 &
    SERVER_PID=$!
  fi
  for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:$PORT/data/materials/" >/dev/null 2>&1 && break
    [[ -z "$SERVER_PID" ]] || kill -0 "$SERVER_PID" 2>/dev/null || { echo "gunicorn exited; see $RUN_DIR/server.log" >&2; exit 1; }
    sleep 1
  done
  curl -sf "http://127.0.0.1:$PORT/data/materials/" >/dev/null || { echo "gunicorn did not come up; see $RUN_DIR/server.log" >&2; exit 1; }

  step "Load test: $USERS users, spawn rate $SPAWN_RATE/s, $DURATION"
  # locust runs in its own tool environment; it only talks HTTP to the server
  PERF_TARGETS="$RUN_DIR/targets.json" uvx --from 'locust>=2.37,<3' locust \
    -f perf/locustfile.py --headless --only-summary \
    -H "http://127.0.0.1:$PORT" -u "$USERS" -r "$SPAWN_RATE" -t "$DURATION" \
    --csv "$RUN_DIR/locust" > "$RUN_DIR/locust.log" 2>&1 || true  # non-zero on any failed request; the report shows them
  tail -n 40 "$RUN_DIR/locust.log"
fi

step "Report"
uv run python perf/report.py "$RUN_DIR"
