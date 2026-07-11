#!/usr/bin/env bash
#
# datahub_doctor.sh — health check + recovery for the DataHub quickstart box.
#
# Runs ON the server (needs docker + /proc). Read-only by default.
#
#   ./datahub_doctor.sh            # diagnose only (safe; changes nothing)
#   ./datahub_doctor.sh --logs     # diagnose + dump recent ingestion-failure logs
#   ./datahub_doctor.sh --fix      # diagnose, then restart any Exited containers
#
# Covers the failure modes we've actually hit:
#   1. OpenSearch dies on native-thread exhaustion (pthread_create EAGAIN) ->
#      "Failed to list secrets / Search query failed" in the UI.
#   2. Managed ingestion fails at venv setup on an empty version pin
#      ("acryl-datahub[postgres]==" with nothing after ==).
#
# From your laptop you can run it over the tunnel host without copying it:
#   ssh awscooee 'bash -s' -- --fix < scripts/datahub_doctor.sh

set -uo pipefail

# ---- args -------------------------------------------------------------------
DO_FIX=0
DO_LOGS=0
for a in "$@"; do
  case "$a" in
    --fix)  DO_FIX=1 ;;
    --logs) DO_LOGS=1 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//' | head -20
      exit 0 ;;
    *) echo "unknown arg: $a (use --fix, --logs)"; exit 2 ;;
  esac
done

# ---- pretty -----------------------------------------------------------------
if [ -t 1 ]; then R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[1m'; Z=$'\e[0m'
else R=; G=; Y=; B=; Z=; fi
ok()   { echo "  ${G}✓${Z} $*"; }
warn() { echo "  ${Y}!${Z} $*"; }
bad()  { echo "  ${R}✗${Z} $*"; }
hdr()  { echo; echo "${B}== $* ==${Z}"; }

ISSUES=0
note_issue() { ISSUES=$((ISSUES+1)); }

command -v docker >/dev/null || { bad "docker not found — are you on the box?"; exit 2; }

# Discover the DataHub containers (quickstart names all contain "datahub").
mapfile -t CONTAINERS < <(docker ps -a --filter name=datahub --format '{{.Names}}' | sort)
OS_CTR=$(printf '%s\n' "${CONTAINERS[@]}" | grep -iE 'opensearch|elastic' | head -1)
ACTIONS_CTR=$(printf '%s\n' "${CONTAINERS[@]}" | grep -i 'actions' | head -1)

# ---- 1. container status ----------------------------------------------------
hdr "Containers"
EXITED=()
if [ "${#CONTAINERS[@]}" -eq 0 ]; then
  bad "no datahub containers found"; note_issue
fi
for c in "${CONTAINERS[@]}"; do
  status=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$c" 2>/dev/null)
  exitc=$(docker inspect -f '{{.State.ExitCode}}' "$c" 2>/dev/null)
  line="$c — $status${health:+ ($health)}"
  case "$status" in
    running)
      if [ "$health" = "unhealthy" ]; then warn "$line"; note_issue; else ok "$line"; fi ;;
    exited)
      # system-update is meant to exit 0 once; don't flag it
      if printf '%s' "$c" | grep -qi 'system-update' && [ "$exitc" = "0" ]; then
        ok "$line (one-shot, expected)"
      else
        bad "$line (exit $exitc)"; EXITED+=("$c"); note_issue
      fi ;;
    *) warn "$line" ;;
  esac
done

# ---- 2. host resources (the OpenSearch root cause) --------------------------
hdr "Host resources"
read -r _ total used free _ buff avail < <(free -m 2>/dev/null | awk '/^Mem:/{print}')
[ -n "${avail:-}" ] && ok "memory: ${avail}MB available / ${total}MB total"
diskpct=$(df -P / | awk 'END{gsub("%","",$5); print $5}')
if [ "${diskpct:-0}" -ge 90 ]; then bad "disk /: ${diskpct}% used"; note_issue
elif [ "${diskpct:-0}" -ge 80 ]; then warn "disk /: ${diskpct}% used"
else ok "disk /: ${diskpct}% used"; fi

# native-thread ceiling — this is what killed OpenSearch (pthread_create EAGAIN)
tmax=$(cat /proc/sys/kernel/threads-max 2>/dev/null || echo 0)
tnow=$(ps -eLf 2>/dev/null | wc -l)
if [ "$tmax" -gt 0 ]; then
  pct=$(( tnow * 100 / tmax ))
  msg="threads: ${tnow} live / ${tmax} max (${pct}%)"
  if [ "$pct" -ge 80 ]; then bad "$msg — thread exhaustion risk (OpenSearch will die)"; note_issue
  elif [ "$pct" -ge 60 ]; then warn "$msg"; else ok "$msg"; fi
fi
# ES needs vm.max_map_count >= 262144
mmc=$(cat /proc/sys/vm/max_map_count 2>/dev/null || echo 0)
if [ "$mmc" -lt 262144 ]; then warn "vm.max_map_count=$mmc (<262144) — OpenSearch may refuse to start"
else ok "vm.max_map_count=$mmc"; fi

# ---- 3. OpenSearch cluster health -------------------------------------------
hdr "Search index (OpenSearch)"
if [ -z "$OS_CTR" ]; then
  bad "no opensearch/elastic container"; note_issue
elif [ "$(docker inspect -f '{{.State.Status}}' "$OS_CTR")" != "running" ]; then
  bad "$OS_CTR is not running — this is why 'list secrets / search' fails"; note_issue
else
  ch=$(docker exec "$OS_CTR" sh -c 'curl -s localhost:9200/_cluster/health' 2>/dev/null)
  cstatus=$(printf '%s' "$ch" | grep -oE '"status":"[a-z]+"' | cut -d'"' -f4)
  case "$cstatus" in
    green)  ok "cluster status: green" ;;
    yellow) warn "cluster status: yellow (usable; single-node replicas unassigned — normal for quickstart)" ;;
    red)    bad "cluster status: red — indices unavailable"; note_issue ;;
    *)      warn "could not read cluster health (curl missing in container?)" ;;
  esac
fi

# ---- 4. GMS health ----------------------------------------------------------
hdr "GMS"
gms=$(printf '%s\n' "${CONTAINERS[@]}" | grep -i 'gms' | head -1)
if [ -n "$gms" ] && [ "$(docker inspect -f '{{.State.Status}}' "$gms")" = "running" ]; then
  code=$(docker exec "$gms" sh -c 'curl -s -o /dev/null -w "%{http_code}" localhost:8080/health' 2>/dev/null)
  if [ "$code" = "200" ]; then ok "GMS /health = 200"; else warn "GMS /health = ${code:-?}"; fi
else
  bad "GMS not running"; note_issue
fi

# ---- 5. recent ingestion failures -------------------------------------------
hdr "Ingestion"
if [ -n "$ACTIONS_CTR" ]; then
  logs=$(docker logs --tail 400 "$ACTIONS_CTR" 2>&1)
  if printf '%s' "$logs" | grep -q "Venv setup failed"; then
    bad "recent ingestion run failed at venv setup"; note_issue
    # the specific empty-pin bug we hit
    badreq=$(ls -t /tmp/datahub/ingest/*/venv-*/requirements.txt 2>/dev/null | head -5 \
             | xargs grep -lE '==\s*$' 2>/dev/null | head -1)
    if [ -n "$badreq" ]; then
      warn "cause: empty version pin in $badreq"
      warn "  -> the recipe's CLI Version is blank; set it to the server version (e.g. 1.5.0.6)"
    fi
    [ "$DO_LOGS" -eq 1 ] && { echo "  --- last venv/uv lines ---"; \
      printf '%s\n' "$logs" | grep -iE 'uv pip|error:|Venv setup|requirements' | tail -15 | sed 's/^/  /'; }
  else
    ok "no venv-setup failures in recent actions logs"
  fi
else
  warn "no actions/executor container found"
fi

# ---- 6. fix -----------------------------------------------------------------
if [ "$DO_FIX" -eq 1 ] && [ "${#EXITED[@]}" -gt 0 ]; then
  hdr "Fix: restarting exited containers"
  # start opensearch first so dependents come back cleanly
  ordered=$(printf '%s\n' "${EXITED[@]}" | grep -iE 'opensearch|elastic'; \
            printf '%s\n' "${EXITED[@]}" | grep -ivE 'opensearch|elastic')
  for c in $ordered; do
    echo "  starting $c ..."
    docker start "$c" >/dev/null && ok "started $c" || bad "failed to start $c"
  done
  if [ -n "$OS_CTR" ]; then
    printf "  waiting for OpenSearch"
    for _ in $(seq 1 30); do
      s=$(docker exec "$OS_CTR" sh -c 'curl -s localhost:9200/_cluster/health' 2>/dev/null \
          | grep -oE '"status":"[a-z]+"' | cut -d'"' -f4)
      [ "$s" = "green" ] || [ "$s" = "yellow" ] && { echo " -> $s"; break; }
      printf "."; sleep 2
    done
  fi
elif [ "$DO_FIX" -eq 1 ]; then
  hdr "Fix"; ok "nothing to restart"
fi

# ---- summary ----------------------------------------------------------------
hdr "Summary"
if [ "$ISSUES" -eq 0 ]; then
  echo "  ${G}${B}healthy${Z} — no issues detected"
  exit 0
else
  echo "  ${R}${B}${ISSUES} issue(s) detected${Z}"
  [ "$DO_FIX" -eq 0 ] && echo "  re-run with ${B}--fix${Z} to restart downed containers, ${B}--logs${Z} for detail"
  exit 1
fi
