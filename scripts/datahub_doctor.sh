#!/usr/bin/env bash
#
# datahub_doctor.sh — health check + recovery for the DataHub quickstart box.
#
# Runs ON the server (needs docker + /proc). Read-only by default.
#
#   ./datahub_doctor.sh            # diagnose only (safe; changes nothing)
#   ./datahub_doctor.sh --logs     # diagnose + dump recent ingestion-failure logs
#   ./datahub_doctor.sh --fix      # start Docker/downed containers, then verify UI
#   ./datahub_doctor.sh --enable-autostart
#                                  # set long-running containers to survive reboots
#
# Covers the failure modes we've actually hit:
#   1. OpenSearch dies on native-thread exhaustion (pthread_create EAGAIN) ->
#      "Failed to list secrets / Search query failed" in the UI.
#   2. Managed ingestion fails at venv setup on an empty version pin
#      ("acryl-datahub[postgres]==" with nothing after ==).
#   3. A host reboot leaves Docker or the quickstart containers stopped, or the
#      frontend is not published/listening on port 9002.
#
# From your laptop you can run it over the tunnel host without copying it:
#   ssh internal_too_aws 'bash -s' -- --fix < scripts/datahub_doctor.sh

set -uo pipefail

# ---- args -------------------------------------------------------------------
DO_FIX=0
DO_LOGS=0
DO_AUTOSTART=0
for a in "$@"; do
  case "$a" in
    --fix)  DO_FIX=1 ;;
    --logs) DO_LOGS=1 ;;
    --enable-autostart) DO_AUTOSTART=1 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//' | head -24
      exit 0 ;;
    *) echo "unknown arg: $a (use --fix, --logs, --enable-autostart)"; exit 2 ;;
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

# ---- 0. Docker daemon -------------------------------------------------------
hdr "Docker service"
if command -v systemctl >/dev/null 2>&1; then
  docker_service=$(systemctl is-active docker 2>/dev/null || true)
  if [ "$docker_service" = "active" ]; then
    ok "docker.service is active"
  else
    bad "docker.service is ${docker_service:-unknown}"
    note_issue
    if [ "$DO_FIX" -eq 1 ]; then
      echo "  starting docker.service ..."
      if sudo -n systemctl start docker 2>/dev/null; then
        ok "started docker.service"
      else
        bad "could not start Docker non-interactively"
        warn "run: sudo systemctl start docker"
      fi
    else
      warn "run with --fix, or: sudo systemctl start docker"
    fi
  fi

  docker_enabled=$(systemctl is-enabled docker 2>/dev/null || true)
  if [ "$docker_enabled" = "enabled" ]; then
    ok "docker.service is enabled at boot"
  elif [ "$DO_AUTOSTART" -eq 1 ]; then
    if sudo -n systemctl enable docker >/dev/null 2>&1; then
      ok "enabled docker.service at boot"
    else
      bad "could not enable docker.service non-interactively"
      note_issue
      warn "run: sudo systemctl enable docker"
    fi
  else
    warn "docker.service is ${docker_enabled:-not enabled} at boot"
    note_issue
    warn "run with --enable-autostart, or: sudo systemctl enable docker"
  fi
fi

if ! docker info >/dev/null 2>&1; then
  bad "Docker daemon is not reachable; container checks cannot continue"
  hdr "Summary"
  echo "  ${R}${B}${ISSUES:-1} issue(s) detected${Z}"
  exit 1
fi
ok "Docker daemon is reachable"

# Discover the DataHub containers (quickstart names all contain "datahub").
mapfile -t CONTAINERS < <(docker ps -a --filter name=datahub --format '{{.Names}}' | sort)
OS_CTR=$(printf '%s\n' "${CONTAINERS[@]}" | grep -iE 'opensearch|elastic' | head -1)
ACTIONS_CTR=$(printf '%s\n' "${CONTAINERS[@]}" | grep -i 'actions' | head -1)
FRONTEND_CTR=$(printf '%s\n' "${CONTAINERS[@]}" | grep -i 'frontend' | head -1)

show_port_9002_owner() {
  docker_owner=$(docker ps --filter publish=9002 \
    --format '{{.Names}} ({{.Image}}) {{.Ports}}' 2>/dev/null || true)
  if [ -n "$docker_owner" ]; then
    warn "Docker container publishing 9002:"
    printf '%s\n' "$docker_owner" | sed 's/^/    /'
  fi

  if command -v ss >/dev/null 2>&1; then
    socket_owner=$(ss -ltnp 'sport = :9002' 2>/dev/null | tail -n +2 || true)
    if [ -n "$socket_owner" ]; then
      warn "host listener on 9002:"
      printf '%s\n' "$socket_owner" | sed 's/^/    /'
    elif [ -z "$docker_owner" ]; then
      warn "port 9002 appears occupied to Docker, but its owner is hidden"
      warn "run: sudo ss -ltnp 'sport = :9002'"
    fi
  elif [ -z "$docker_owner" ]; then
    warn "run: sudo lsof -nP -iTCP:9002 -sTCP:LISTEN"
  fi
}

# ---- 1. container status ----------------------------------------------------
hdr "Containers"
STARTABLE=()
if [ "${#CONTAINERS[@]}" -eq 0 ]; then
  bad "no DataHub quickstart containers found"; note_issue
  warn "start/re-create them with the same user that installed DataHub:"
  warn "  datahub docker quickstart"
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
        bad "$line (exit $exitc)"; STARTABLE+=("$c"); note_issue
      fi ;;
    created|dead)
      bad "$line"; STARTABLE+=("$c"); note_issue ;;
    restarting|paused)
      bad "$line"; note_issue ;;
    *) warn "$line"; note_issue ;;
  esac
done

# Quickstart is a Compose deployment, not its own systemd service. Docker
# restart policies are what make the long-running containers survive a reboot.
NO_AUTOSTART=()
for c in "${CONTAINERS[@]}"; do
  printf '%s' "$c" | grep -qi 'system-update' && continue
  policy=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$c" 2>/dev/null)
  case "$policy" in
    always|unless-stopped) ;;
    *) NO_AUTOSTART+=("$c") ;;
  esac
done
if [ "${#NO_AUTOSTART[@]}" -gt 0 ]; then
  warn "${#NO_AUTOSTART[@]} long-running container(s) will not automatically return after reboot"
  note_issue
  warn "run with --enable-autostart to set restart=unless-stopped"
fi

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

# ---- 5. DataHub frontend + host port ----------------------------------------
hdr "DataHub UI (port 9002)"
if [ -z "$FRONTEND_CTR" ]; then
  bad "no DataHub frontend container found"; note_issue
elif [ "$(docker inspect -f '{{.State.Status}}' "$FRONTEND_CTR" 2>/dev/null)" != "running" ]; then
  bad "$FRONTEND_CTR is not running"; note_issue
  if command -v ss >/dev/null 2>&1 && ss -ltn 'sport = :9002' 2>/dev/null | tail -n +2 | grep -q .; then
    bad "host port 9002 is already occupied; the frontend cannot bind it"
    show_port_9002_owner
  fi
else
  published=$(docker port "$FRONTEND_CTR" 9002/tcp 2>/dev/null || true)
  if [ -z "$published" ]; then
    bad "$FRONTEND_CTR does not publish container port 9002 to the host"; note_issue
  else
    ok "published: $(printf '%s' "$published" | tr '\n' ' ')"
  fi

  if command -v curl >/dev/null 2>&1; then
    ui_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
      http://127.0.0.1:9002/ 2>/dev/null || true)
    case "$ui_code" in
      200|301|302|303|307|308)
        ok "server-local http://127.0.0.1:9002/ = $ui_code" ;;
      *)
        bad "server-local http://127.0.0.1:9002/ = ${ui_code:-unreachable}"
        note_issue
        warn "inspect: docker logs --tail 100 $FRONTEND_CTR" ;;
    esac
  else
    warn "curl not installed; skipped server-local HTTP check"
  fi
fi
warn "If this check passes but laptop localhost:9002 fails, recreate the tunnel:"
warn "  ssh -N -L 9002:127.0.0.1:9002 internal_too_aws"

# ---- 6. recent ingestion failures -------------------------------------------
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

# ---- 7. autostart -----------------------------------------------------------
if [ "$DO_AUTOSTART" -eq 1 ]; then
  hdr "Autostart"
  if [ "${#NO_AUTOSTART[@]}" -eq 0 ]; then
    ok "all long-running containers already survive reboots"
  else
    for c in "${NO_AUTOSTART[@]}"; do
      if docker update --restart unless-stopped "$c" >/dev/null; then
        ok "$c -> restart=unless-stopped"
      else
        bad "could not update restart policy for $c"
        note_issue
      fi
    done
    warn "docker.service must also be enabled at boot (normally the package default)"
  fi
fi

# ---- 8. fix -----------------------------------------------------------------
if [ "$DO_FIX" -eq 1 ] && [ "${#STARTABLE[@]}" -gt 0 ]; then
  hdr "Fix: starting downed containers"
  # start opensearch first so dependents come back cleanly
  ordered=$(printf '%s\n' "${STARTABLE[@]}" | grep -iE 'opensearch|elastic'; \
            printf '%s\n' "${STARTABLE[@]}" | grep -ivE 'opensearch|elastic')
  for c in $ordered; do
    echo "  starting $c ..."
    if docker start "$c" >/dev/null; then
      ok "started $c"
    else
      bad "failed to start $c"
      if [ "$c" = "$FRONTEND_CTR" ]; then
        show_port_9002_owner
      fi
    fi
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
  if [ -n "$FRONTEND_CTR" ] \
    && [ "$(docker inspect -f '{{.State.Status}}' "$FRONTEND_CTR" 2>/dev/null)" = "running" ] \
    && command -v curl >/dev/null 2>&1; then
    printf "  waiting for DataHub UI"
    for _ in $(seq 1 30); do
      code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 2 \
        http://127.0.0.1:9002/ 2>/dev/null || true)
      case "$code" in
        200|301|302|303|307|308) echo " -> HTTP $code"; break ;;
        *) printf "."; sleep 2 ;;
      esac
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
  if [ "$DO_FIX" -eq 1 ]; then
    echo "  repairs were attempted; re-run without flags to confirm current health"
  else
    echo "  re-run with ${B}--fix${Z} to start downed services/containers, ${B}--logs${Z} for detail"
  fi
  exit 1
fi
