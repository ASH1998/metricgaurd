#!/usr/bin/env bash
# One-command demo environment — warehouse + DataHub + the simulated org.
#
#   scripts/setup_demo.sh              # everything (fresh machine -> working demo)
#   scripts/setup_demo.sh --no-datahub # stop after the warehouse if DataHub is absent
#   scripts/setup_demo.sh --yes        # no confirmation prompts (CI)
#
# Idempotent: every step re-runs safely (drop/recreate tables, re-emit aspects).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

NO_DATAHUB=0; ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --no-datahub) NO_DATAHUB=1 ;;
    --yes) ASSUME_YES=1 ;;
    *) echo "unknown flag: $arg (known: --no-datahub, --yes)"; exit 2 ;;
  esac
done

say()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' is required. $2"; exit 1; }; }

say "MetricGuard demo environment"
need docker "Install Docker Desktop (or the docker engine)."
need uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
docker info >/dev/null 2>&1 || { echo "ERROR: the docker daemon is not running."; exit 1; }

# --- 1. .env -------------------------------------------------------------------
if [ ! -f .env ]; then
  say "Writing .env with local demo defaults"
  cat > .env <<'ENV'
# Demo environment written by scripts/setup_demo.sh.
# To enable the LLM agent, set LLM_MODEL + the matching provider API key.
LLM_MODEL=anthropic:claude-opus-4-8
ANTHROPIC_API_KEY=

# Local demo warehouse (docker-compose.demo.yml)
POSTGRES_DSN=postgresql://metricguard:metricguard@localhost:5433/postgres
DB_HOST=localhost
DB_PORT=5433
DB_NAME=postgres
DB_USER=metricguard
DB_PASSWORD=metricguard
DB_SCHEMA=metric

# Local DataHub quickstart (UI: http://localhost:9002, login datahub/datahub)
DATAHUB_GMS_URL=http://localhost:8080
DATAHUB_TOKEN=
DATAHUB_MCP_TRANSPORT=stdio
DATAHUB_MCP_COMMAND=uvx mcp-server-datahub

METRICGUARD_CONTRACTS_DIR=.metricguard/contracts
METRICGUARD_DIALECT=postgres
METRICGUARD_REQUIRE_APPROVAL=true
ENV
  echo "Wrote .env (LLM key left empty — deterministic paths work without it)."
fi

set -a; # export everything sourced below so datahub ingest sees it
source .env
set +a
DB_HOST="${DB_HOST:-localhost}"
export DB_PORT="${DB_PORT:-5433}" DB_NAME="${DB_NAME:-postgres}"
export DB_USER="${DB_USER:-metricguard}" DB_SCHEMA="${DB_SCHEMA:-metric}"

# --- 2. warehouse ----------------------------------------------------------------
if [ "$DB_HOST" = "localhost" ] || [ "$DB_HOST" = "127.0.0.1" ]; then
  say "Starting the local warehouse (postgres:16 on :${DB_PORT})"
  docker compose -f docker-compose.demo.yml up -d --wait
elif [ "$ASSUME_YES" != 1 ]; then
  echo "Your existing .env targets DB_HOST=$DB_HOST (not localhost)."
  read -r -p "Load fiction-retail data into THAT database? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted — adjust .env (or delete it) for a local demo."; exit 1; }
fi

# --- 3. python deps ---------------------------------------------------------------
say "Installing python deps (uv sync --extra warehouse --extra demo)"
uv sync --extra warehouse --extra demo

# --- 4. data ----------------------------------------------------------------------
say "Loading fiction-retail data (~695k rows across 10 tables)"
uv run python scripts/load_fiction_retail.py

# --- 5. DataHub -------------------------------------------------------------------
GMS="${DATAHUB_GMS_URL:-http://localhost:8080}"
if ! curl -fsS "$GMS/config" >/dev/null 2>&1; then
  if [ "$NO_DATAHUB" = 1 ]; then
    echo "DataHub is not reachable at $GMS and --no-datahub was set."
    echo "Warehouse-only demo ready: signature/compare/divergence work; graph features don't."
    exit 0
  fi
  say "Starting DataHub quickstart (first run downloads images; needs ~8GB free RAM)"
  uv run datahub docker quickstart
fi
for _ in $(seq 1 60); do
  curl -fsS "$GMS/config" >/dev/null 2>&1 && break
  sleep 3
done
curl -fsS "$GMS/config" >/dev/null 2>&1 || { echo "ERROR: DataHub GMS never became reachable at $GMS"; exit 1; }
echo "DataHub GMS reachable at $GMS"

# --- 6. catalog the warehouse -------------------------------------------------------
say "Cataloging the warehouse schema into DataHub (postgres.metric.*)"
uv run datahub ingest -c ingestion/postgres_metric.yml

# --- 7. the simulated org + write-back prerequisites --------------------------------
say "Ingesting the simulated org (8 domains, 8 teams, 12 conflicting definitions + 4 near-miss controls)"
uv run python scripts/simulate_org.py --emit
say "Creating write-back tag + structured-property definitions"
uv run python scripts/bootstrap_writeback_entities.py --emit

# --- 8. smoke test -------------------------------------------------------------------
say "Smoke test: rediscovering the metric conflicts from the graph"
uv run metricguard discover --from-graph

say "Demo environment ready"
cat <<'NEXT'
Explore:
  uv run metricguard discover --from-graph
  uv run metricguard compare seeds/metric_families/weekly_revenue/finance_weekly_revenue.sql \
      seeds/metric_families/weekly_revenue/exec_dashboard_weekly_revenue.sql
  uv run metricguard agent "Investigate weekly revenue from DataHub, quantify divergence \
      with key_col=week_start and value_col=weekly_revenue, recommend a canonical, and \
      stage the resolution."          # needs an LLM API key in .env
  open http://localhost:9002          # DataHub UI (datahub / datahub)

Teardown:
  docker compose -f docker-compose.demo.yml down -v   # the warehouse
  uv run datahub docker nuke                          # DataHub (destructive!)
NEXT
