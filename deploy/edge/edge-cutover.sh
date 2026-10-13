#!/usr/bin/env bash
# Phase 1 of the production plan: move the VM's edge Caddy out of the
# ResearcherX demo stack (container researcherx-demo-caddy-1) into its own
# stack at /srv/edge (container edge-caddy), so no app's deploy or teardown
# can take the other apps' HTTPS down.
#
# Run as root on the VM, from an up-to-date checkout:
#   sudo -i
#   cd /srv/researcherx && git fetch && git checkout main && git pull --ff-only
#   deploy/edge/edge-cutover.sh plan       # checks only (pulls the Caddy image), changes nothing
#   deploy/edge/edge-cutover.sh cutover    # the switch; seconds of downtime for every site
#   deploy/edge/edge-cutover.sh rollback   # back to the old Caddy, if anything looks wrong
#   deploy/edge/edge-cutover.sh cleanup    # once you are happy: remove the old, stopped Caddy
#   deploy/edge/edge-cutover.sh install    # a fresh VM with no old Caddy: install and start the edge
#
# What `cutover` does, in order:
#   1. Preflight: root, docker, the proxy network, the env file, the old Caddy
#      running. Then pulls the Caddy image, so every later step uses it.
#   2. Installs /srv/edge/{docker-compose.yml,Caddyfile} and renders
#      /srv/edge/sites/researcherx.caddy for $DEMO_DOMAIN. Other apps' site
#      files are left untouched; an existing researcherx.caddy is backed up.
#   3. Seeds the new certificate volume from the old Caddy's /data, so nothing
#      is re-issued against Let's Encrypt's rate limits.
#   4. Validates the new config in a throwaway container. Stops here on error.
#   5. Attaches the running ResearcherX backend/frontend to `proxy` as
#      rx-backend/rx-frontend (no restart; they keep their current network).
#   6. Stop old Caddy -> start edge Caddy -> health
#      checks. Any failed check rolls back automatically.
#
# Nothing here pulls or deploys application code, and nothing deletes a
# volume. The old Caddy is only STOPPED by cutover; `cleanup` removes it.
set -euo pipefail

REPO="${REPO:-/srv/researcherx}"
EDGE_DIR="${EDGE_DIR:-/srv/edge}"
ENV_FILE="${ENV_FILE:-/etc/researcherx-demo.env}"
OLD="researcherx-demo-caddy-1"
NEW="edge-caddy"
IMAGE="caddy:2-alpine" # must match deploy/edge/docker-compose.yml
RX_PROJECT="researcherx-demo"
EDGE_COMPOSE=(docker compose -p edge -f "$EDGE_DIR/docker-compose.yml")

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

load_env() {
  [[ -r "$ENV_FILE" ]] || die "cannot read $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  [[ "${DEMO_DOMAIN:-}" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] \
    || die "DEMO_DOMAIN missing or not a bare hostname"
}

rx_container() { # $1 = compose service name
  docker ps -q \
    --filter "label=com.docker.compose.project=$RX_PROJECT" \
    --filter "label=com.docker.compose.service=$1"
}

old_volume() { # $1 = mount destination in the old Caddy, e.g. /data
  docker inspect -f "{{range .Mounts}}{{if eq .Destination \"$1\"}}{{.Name}}{{end}}{{end}}" "$OLD"
}

# Every docker run below uses the image the edge runs, pulled first, so the
# config is validated by the same Caddy version that then serves it.
pull_image() {
  say "Pulling $IMAGE"
  docker pull -q "$IMAGE" >/dev/null
}

# Every hostname that starts a site block in a site file.
site_hosts() {
  grep -hoE '^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}' "$EDGE_DIR"/sites/*.caddy 2>/dev/null | sort -u
}

preflight() {
  say "Preflight"
  [[ $EUID -eq 0 ]] || die "run as root (sudo -i)"
  command -v docker >/dev/null || die "docker not found"
  [[ -f "$REPO/deploy/edge/docker-compose.yml" ]] || die "$REPO has no deploy/edge; pull main first"
  docker network inspect proxy >/dev/null 2>&1 || die "network 'proxy' missing (docker network create proxy)"
  load_env
  echo "domain:        $DEMO_DOMAIN"
  echo "old caddy:     $(docker inspect -f '{{.State.Status}} ({{.Config.Image}})' "$OLD" 2>/dev/null || echo 'NOT FOUND')"
  echo "new caddy:     $(docker inspect -f '{{.State.Status}}' "$NEW" 2>/dev/null || echo 'not created yet')"
  echo "rx backend:    $(rx_container backend || true)"
  echo "rx frontend:   $(rx_container frontend || true)"
  echo "site files:    $(ls "$EDGE_DIR"/sites/*.caddy 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ' ')"
  echo "80/443 held by: $(docker ps --format '{{.Names}} {{.Ports}}' | grep -E ':(80|443)->' | cut -d' ' -f1 | tr '\n' ' ')"
  # The old Caddy had DEMO_DOMAIN in its environment; the edge Caddy has no
  # environment at all. A site file reading {$VAR} would silently get "".
  local placeholders
  placeholders="$(grep -lF '{$' "$EDGE_DIR"/sites/*.caddy 2>/dev/null || true)"
  [[ -z "$placeholders" ]] || die "these site files use {\$VAR} placeholders, which the edge Caddy cannot resolve; write literal values: $placeholders"
}

install_files() {
  say "Installing $EDGE_DIR"
  install -d -m 755 "$EDGE_DIR" "$EDGE_DIR/sites"
  install -m 644 "$REPO/deploy/edge/docker-compose.yml" "$EDGE_DIR/docker-compose.yml"
  install -m 644 "$REPO/deploy/edge/Caddyfile" "$EDGE_DIR/Caddyfile"
  local rendered
  rendered="$(mktemp)"
  sed "s/__DOMAIN__/$DEMO_DOMAIN/g" "$REPO/deploy/edge/sites/researcherx.caddy.template" > "$rendered"
  if [[ -f "$EDGE_DIR/sites/researcherx.caddy" ]] && ! cmp -s "$rendered" "$EDGE_DIR/sites/researcherx.caddy"; then
    cp -p "$EDGE_DIR/sites/researcherx.caddy" "$EDGE_DIR/researcherx.caddy.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    echo "backed up the previous researcherx.caddy into $EDGE_DIR"
  fi
  # In place (cat >), never mv: a bind-mounted file keeps its inode.
  cat "$rendered" > "$EDGE_DIR/sites/researcherx.caddy"
  chmod 644 "$EDGE_DIR/sites/researcherx.caddy"
  rm -f "$rendered"
}

seed_certificates() {
  say "Seeding certificates"
  local old_vol
  old_vol="$(old_volume /data)"
  [[ -n "$old_vol" ]] || die "could not find the old Caddy's /data volume"
  docker volume create edge_caddy_data >/dev/null
  docker volume create edge_caddy_config >/dev/null
  if docker run --rm -v edge_caddy_data:/to:ro "$IMAGE" sh -c '[ -n "$(ls -A /to)" ]'; then
    echo "edge_caddy_data already has content; leaving it as is"
  else
    docker run --rm -v "$old_vol":/from:ro -v edge_caddy_data:/to "$IMAGE" sh -c 'cp -a /from/. /to/'
    echo "copied $old_vol -> edge_caddy_data"
  fi
}

validate() {
  say "Validating the new config"
  docker run --rm --network proxy \
    -v "$EDGE_DIR/Caddyfile":/etc/caddy/Caddyfile:ro \
    -v "$EDGE_DIR/sites":/etc/caddy/sites:ro \
    "$IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
}

attach_rx() {
  say "Attaching ResearcherX to the proxy network"
  local svc alias id
  for svc in backend frontend; do
    alias="rx-$svc"
    id="$(rx_container "$svc")"
    [[ -n "$id" ]] || die "ResearcherX $svc is not running"
    if docker inspect -f '{{json .NetworkSettings.Networks}}' "$id" | grep -q '"proxy"'; then
      echo "$svc already on proxy"
    else
      docker network connect --alias "$alias" proxy "$id"
      echo "$svc attached as $alias"
    fi
  done
}

# $1 = host, $2 = path, $3 = regex of accepted status codes. Retries for about
# a minute, so one transient 502 from an app restarting cannot fail a cutover.
probe() {
  local code=000 _
  for _ in $(seq 1 20); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
      --resolve "$1:443:127.0.0.1" "https://$1$2" 2>/dev/null || true)"
    [[ "$code" =~ $3 ]] && break
    sleep 3
  done
  echo "$1$2 -> $code"
  [[ "$code" =~ $3 ]]
}

health() {
  say "Health checks (through 127.0.0.1, so DNS is not involved)"
  local ok=1 host
  probe "$DEMO_DOMAIN" /v1/health '^200$' || ok=0
  for host in $(site_hosts); do
    [[ "$host" == "$DEMO_DOMAIN" ]] && continue
    # Any real HTTP answer means TLS and routing work; 5xx or no answer does not.
    probe "$host" / '^[1-4][0-9][0-9]$' || ok=0
  done
  [[ $ok -eq 1 ]]
}

do_rollback() {
  say "Rolling back to $OLD"
  "${EDGE_COMPOSE[@]}" stop caddy 2>/dev/null || true
  "${EDGE_COMPOSE[@]}" rm -f caddy 2>/dev/null || true
  # The old Caddy imports this same sites dir AND defines $DEMO_DOMAIN itself in
  # Caddyfile.demo. Left in place, the rendered researcherx.caddy is a second
  # definition of that host, Caddy refuses to start ("ambiguous site
  # definition"), and every site is down. A later cutover renders it again.
  if [[ -f "$EDGE_DIR/sites/researcherx.caddy" ]]; then
    mv "$EDGE_DIR/sites/researcherx.caddy" "$EDGE_DIR/researcherx.caddy.disabled"
    echo "moved researcherx.caddy out of $EDGE_DIR/sites"
  fi
  docker start "$OLD"
  load_env
  if health; then say "Rolled back: $OLD is serving again"; else die "rolled back, but health checks still fail -- look at: docker logs $OLD"; fi
}

cmd_plan() {
  preflight
  pull_image
  local tmp
  tmp="$(mktemp -d)"
  sed "s/__DOMAIN__/$DEMO_DOMAIN/g" "$REPO/deploy/edge/sites/researcherx.caddy.template" > "$tmp/researcherx.caddy"
  [[ -d "$EDGE_DIR/sites" ]] && find "$EDGE_DIR/sites" -maxdepth 1 -name '*.caddy' ! -name researcherx.caddy -exec cp {} "$tmp/" \;
  say "Validating the config cutover would load (in a temp dir)"
  docker run --rm --network proxy \
    -v "$REPO/deploy/edge/Caddyfile":/etc/caddy/Caddyfile:ro \
    -v "$tmp":/etc/caddy/sites:ro \
    "$IMAGE" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  rm -rf "$tmp"
  say "Plan OK. Nothing was changed. Next: $0 cutover"
}

cmd_cutover() {
  preflight
  docker inspect -f '{{.State.Running}}' "$OLD" 2>/dev/null | grep -q true \
    || die "$OLD is not running; nothing to cut over from (is $NEW already serving?)"
  pull_image
  install_files
  seed_certificates
  validate
  attach_rx
  say "Switching: stop $OLD, start $NEW (downtime starts)"
  docker stop "$OLD" >/dev/null
  if ! "${EDGE_COMPOSE[@]}" up -d; then do_rollback; exit 1; fi
  if health; then
    say "Cutover done. $NEW serves 80/443; $OLD is stopped, not removed."
    echo "Check every site in a browser, then: $0 cleanup"
  else
    echo "Health checks failed -- rolling back"
    docker logs --tail 50 "$NEW" || true
    do_rollback
    exit 1
  fi
}

cmd_cleanup() {
  preflight
  docker inspect -f '{{.State.Running}}' "$NEW" 2>/dev/null | grep -q true || die "$NEW is not running; not removing $OLD"
  docker inspect -f '{{.State.Running}}' "$OLD" 2>/dev/null | grep -q false || die "$OLD is running or missing; not removing it"
  local old_vols
  old_vols="$(old_volume /data) $(old_volume /config)"
  docker rm "$OLD"
  say "Removed $OLD. Its volumes are kept; once you are sure, remove them with:"
  echo "  docker volume rm $old_vols"
}

cmd_install() {
  preflight
  if docker inspect "$OLD" >/dev/null 2>&1; then die "$OLD exists; use cutover, not install"; fi
  pull_image
  install_files
  validate
  "${EDGE_COMPOSE[@]}" up -d
  if health; then say "Edge installed and serving."; else die "edge started but health checks fail -- docker logs $NEW"; fi
}

case "${1:-}" in
  plan) cmd_plan ;;
  install) cmd_install ;;
  cutover) cmd_cutover ;;
  rollback) preflight; do_rollback ;;
  cleanup) cmd_cleanup ;;
  *) echo "usage: $0 plan|cutover|rollback|cleanup|install" >&2; exit 2 ;;
esac
