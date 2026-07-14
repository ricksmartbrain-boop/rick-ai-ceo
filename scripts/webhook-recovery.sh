#!/usr/bin/env bash
# webhook-recovery.sh — one-command recovery for the disabled Stripe webhook
# we_1TEH8KD9G3v6e0OsqiOehQeL (runbook: ~/rick-vault/operations/api-redeploy-runbook.md §8).
#
# Modes:
#   --status               DEFAULT. Strictly read-only (Stripe GETs + API health GET):
#                          endpoint state, target-API health verdict, inventory of
#                          retained events with retention-expiry dates.
#   --enable  [--yes]      Re-enable the endpoint (POST disabled=false), then re-GET
#                          and print the new status. REFUSES if the API health check
#                          fails (enabling a webhook that delivers into a 404 just
#                          gets auto-disabled again).
#   --replay  [--dry-run] [--yes]
#                          Resend retained events via the stripe CLI, OLDEST-FIRST
#                          (closest to retention expiry first), rate-limited.
#                          --dry-run lists exactly what would be resent (read-only).
#                          Real mode REFUSES while the endpoint is disabled or the
#                          API is unhealthy. Double-delivery is idempotent thanks to
#                          migrations/007_stripe_events_idempotency.sql
#                          (stripe_events.stripe_event_id UNIQUE).
#
# Confirmation contract for mutating modes (--enable, --replay without --dry-run):
#   BOTH the --yes flag AND, when run on a TTY, typing "yes" at the prompt.
#   Without --yes they always refuse (exit 3). Non-interactive use: --yes alone.
#
# Exit codes: 0 ok · 2 --status found something needing attention · 3 refused/fatal.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${RICK_ENV_FILE:-$ROOT_DIR/config/rick.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

WEBHOOK_ID="we_1TEH8KD9G3v6e0OsqiOehQeL"
STRIPE_API="https://api.stripe.com/v1"
STRIPE_BIN="${STRIPE_BIN:-/opt/homebrew/bin/stripe}"
RETENTION_DAYS=30
EXPIRY_WARN_DAYS=5
RESEND_SLEEP_SECS=2

if [[ -z "${STRIPE_SECRET_KEY:-}" ]]; then
  echo "FATAL: STRIPE_SECRET_KEY not set (looked in $ENV_FILE)" >&2
  exit 3
fi

MODE="status"
YES=false
DRY_RUN=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --status)  MODE="status" ;;
    --enable)  MODE="enable" ;;
    --replay)  MODE="replay" ;;
    --dry-run) DRY_RUN=true ;;
    --yes)     YES=true ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "FATAL: unknown arg: $1 (see --help)" >&2; exit 3 ;;
  esac
  shift
done

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

die() { echo "FATAL: $*" >&2; exit 3; }

banner() {
  echo ""
  echo "!!! ============================================================ !!!"
  printf '!!! %s\n' "$@"
  echo "!!! ============================================================ !!!"
  echo ""
}

# ── Stripe endpoint state (GET only) ─────────────────────────────────────────
fetch_endpoint() {
  curl -sS -m 20 -G "$STRIPE_API/webhook_endpoints/$WEBHOOK_ID" \
    -u "${STRIPE_SECRET_KEY}:" > "$WORK_DIR/endpoint.json" \
    || die "could not reach api.stripe.com for endpoint GET"
  python3 - "$WORK_DIR" <<'PY' || die "endpoint GET returned an error (see above)"
import json, sys
work = sys.argv[1]
d = json.load(open(f"{work}/endpoint.json"))
if "error" in d:
    print("Stripe error:", d["error"].get("message"), file=sys.stderr)
    sys.exit(1)
open(f"{work}/ep_status", "w").write(d["status"])
open(f"{work}/ep_url", "w").write(d["url"])
open(f"{work}/ep_types.txt", "w").write("\n".join(d.get("enabled_events", [])) + "\n")
print(f"Webhook endpoint {d['id']}")
print(f"  status         : {d['status'].upper()}")
print(f"  url            : {d['url']}")
print(f"  enabled_events : {len(d.get('enabled_events', []))}")
for t in d.get("enabled_events", []):
    print(f"    - {t}")
PY
  EP_STATUS="$(cat "$WORK_DIR/ep_status")"
  EP_URL="$(cat "$WORK_DIR/ep_url")"
}

# ── Target-API health (GET only) ─────────────────────────────────────────────
# Route chosen by reading ~/meetrick/api/src: /api/v1/health (routes/health.js,
# the Railway healthcheck route per railway.json) — root /health also exists.
# Healthy = HTTP 200 AND body status=="ok" AND db=="connected" (the webhook
# handler writes to Postgres; a degraded DB means deliveries 500 and the
# endpoint gets auto-disabled again).
health_check() {
  local host; host="${EP_URL#https://}"; host="${host#http://}"; host="${host%%/*}"
  HEALTH_URL="https://$host/api/v1/health"
  HEALTH_OK=false
  HEALTH_REASON=""
  echo ""
  echo "API health check: GET $HEALTH_URL"
  local raw code body
  if ! raw="$(curl -sS -m 15 -w $'\n%{http_code}' "$HEALTH_URL" 2>&1)"; then
    HEALTH_REASON="curl failed: $raw"
  else
    code="${raw##*$'\n'}"
    body="${raw%$'\n'*}"
    echo "  HTTP $code — body: $(printf '%s' "$body" | head -c 300)"
    if [[ "$code" == "200" ]]; then
      if HEALTH_REASON="$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("200 but body is not JSON"); sys.exit(1)
if d.get("status") == "ok" and d.get("db") == "connected":
    print("ok"); sys.exit(0)
s = d.get("status"); db = d.get("db")
print(f"200 but status={s!r} db={db!r}"); sys.exit(1)
')"; then
        HEALTH_OK=true
      fi
    else
      HEALTH_REASON="HTTP $code from $HEALTH_URL"
      # Disambiguate "app dark" vs "route moved": try the root /health too.
      local raw2
      if raw2="$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "https://$host/health" 2>&1)"; then
        echo "  (root /health on same host returned HTTP $raw2)"
      fi
    fi
  fi
  if $HEALTH_OK; then
    echo "  VERDICT: HEALTHY (status=ok, db=connected)"
  else
    banner "API HEALTH CHECK FAILED — DO NOT ENABLE THE WEBHOOK" \
           "Reason: $HEALTH_REASON" \
           "Deliveries into a dead/404 URL fail and the endpoint will be" \
           "auto-disabled again. Fix the app/domain first (runbook §2-§7)."
  fi
}

# ── Inventory of retained events (GET only, paginated) ───────────────────────
build_inventory() {
  local window_start starting_after page_args next
  window_start=$(( $(date +%s) - RETENTION_DAYS * 86400 ))
  starting_after=""
  : > "$WORK_DIR/events.jsonl"
  while :; do
    page_args=(-sS -m 30 -G "$STRIPE_API/events" -u "${STRIPE_SECRET_KEY}:"
               --data-urlencode "limit=100"
               --data-urlencode "created[gte]=$window_start")
    if ! grep -qx '\*' "$WORK_DIR/ep_types.txt"; then
      while IFS= read -r t; do
        if [[ -n "$t" ]]; then page_args+=(--data-urlencode "types[]=$t"); fi
      done < "$WORK_DIR/ep_types.txt"
    fi
    if [[ -n "$starting_after" ]]; then
      page_args+=(--data-urlencode "starting_after=$starting_after")
    fi
    curl "${page_args[@]}" > "$WORK_DIR/page.json" \
      || die "could not reach api.stripe.com for events GET"
    next="$(python3 - "$WORK_DIR" <<'PY'
import json, sys
work = sys.argv[1]
d = json.load(open(f"{work}/page.json"))
if "error" in d:
    print("Stripe error:", d["error"].get("message"), file=sys.stderr)
    sys.exit(1)
data = d.get("data", [])
with open(f"{work}/events.jsonl", "a") as f:
    for ev in data:
        f.write(json.dumps(ev) + "\n")
if d.get("has_more") and data:
    print(data[-1]["id"])
PY
)" || die "events GET returned an error (see above)"
    if [[ -z "$next" ]]; then break; fi
    starting_after="$next"
  done

  python3 - "$WORK_DIR" "$RETENTION_DAYS" "$EXPIRY_WARN_DAYS" <<'PY'
import json, sys, time
from datetime import datetime, timezone
work, retention_days, warn_days = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
events = [json.loads(l) for l in open(f"{work}/events.jsonl") if l.strip()]
events.sort(key=lambda e: e["created"])  # oldest first == expiring first
now = time.time()
iso = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")
print("")
print(f"Retained events in the {retention_days}-day window for the endpoint's enabled types: {len(events)}")
print("(Per-endpoint delivery status is NOT exposed by the Events API; the endpoint")
print(" has been disabled, so every event below is presumed undelivered to it.")
print(" pending_webhooks>0 = Stripe still counts undelivered attempts somewhere.)")
print("")
hdr = f"{'EVENT ID':<32} {'TYPE':<32} {'CREATED (UTC)':<17} {'EXPIRES (UTC)':<17} {'DAYS LEFT':>9}  {'PW':>2}  FLAG"
print(hdr); print("-" * len(hdr))
expiring = 0
with open(f"{work}/resend_ids.txt", "w") as rf:
    for ev in events:
        exp = ev["created"] + retention_days * 86400
        days_left = (exp - now) / 86400
        flag = f"!! EXPIRES <= {warn_days}d" if days_left <= warn_days else ""
        if flag: expiring += 1
        print(f"{ev['id']:<32} {ev['type']:<32} {iso(ev['created']):<17} {iso(exp):<17} {days_left:>9.1f}  {ev.get('pending_webhooks', '?'):>2}  {flag}")
        rf.write(ev["id"] + "\n")
counts = {}
for ev in events:
    counts[ev["type"]] = counts.get(ev["type"], 0) + 1
print("")
print("By type: " + (", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none"))
open(f"{work}/n_events", "w").write(str(len(events)))
open(f"{work}/n_expiring", "w").write(str(expiring))
if expiring:
    print(f"\n!!! {expiring} event(s) fall out of Stripe retention within {warn_days} days — replay those first (the tool resends oldest-first). !!!")
PY
  N_EVENTS="$(cat "$WORK_DIR/n_events")"
  N_EXPIRING="$(cat "$WORK_DIR/n_expiring")"
}

# ── Local-DB cross-check (read-only; states what it can't verify) ────────────
db_crosscheck() {
  echo ""
  if [[ -n "${DATABASE_URL:-}" ]] && command -v psql >/dev/null 2>&1; then
    echo "DB cross-check against stripe_events (migration 007 idempotency table):"
    if psql "$DATABASE_URL" -Atc "SELECT stripe_event_id FROM stripe_events" \
         > "$WORK_DIR/processed.txt" 2>"$WORK_DIR/psql.err"; then
      python3 - "$WORK_DIR" <<'PY'
import sys
work = sys.argv[1]
processed = set(l.strip() for l in open(f"{work}/processed.txt") if l.strip())
ids = [l.strip() for l in open(f"{work}/resend_ids.txt") if l.strip()]
done = [i for i in ids if i in processed]
todo = [i for i in ids if i not in processed]
print(f"  already processed by the API: {len(done)}; NOT processed: {len(todo)}")
for i in todo:
    print(f"    UNPROCESSED: {i}")
PY
    else
      echo "  psql query FAILED: $(cat "$WORK_DIR/psql.err")"
    fi
  else
    echo "DB cross-check: CANNOT VERIFY LOCALLY."
    echo "  The processed-events table (stripe_events, migration 007) lives in Railway"
    echo "  Postgres; DATABASE_URL is not set on this machine$(command -v psql >/dev/null 2>&1 || printf ' and psql is not installed')."
    echo "  Verified from Stripe only: $N_EVENTS event(s) of the endpoint's enabled types"
    echo "  exist in the window. Which of them the API already processed is NOT verified."
    echo "  (To check: cd ~/meetrick/api && railway run psql \"\$DATABASE_URL\" -c 'SELECT stripe_event_id FROM stripe_events' — needs railway login.)"
    echo "  Safe either way: 007's UNIQUE constraint makes double-delivery idempotent."
  fi
}

# ── Double-confirmation gate for mutating modes ──────────────────────────────
confirm_gate() {
  local action="$1"
  if ! $YES; then
    banner "REFUSED: $action requires the --yes flag." \
           "This mode mutates Stripe state. Re-run with --yes; on a TTY you" \
           "will additionally be asked to type \"yes\". Nothing was changed."
    exit 3
  fi
  if [[ -t 0 ]]; then
    printf 'Type "yes" to %s (anything else aborts): ' "$action"
    local ans; read -r ans
    if [[ "$ans" != "yes" ]]; then
      banner "REFUSED: interactive confirmation not given. Nothing was changed."
      exit 3
    fi
  else
    echo "(non-interactive; --yes accepted as confirmation)"
  fi
}

# ── Modes ─────────────────────────────────────────────────────────────────────
cmd_status() {
  echo "=== webhook-recovery --status (read-only) · $(date -u '+%Y-%m-%d %H:%M UTC') ==="
  echo ""
  fetch_endpoint
  health_check
  build_inventory
  db_crosscheck
  echo ""
  echo "=== Verdict ==="
  local attention=false
  if [[ "$EP_STATUS" != "enabled" ]]; then
    echo "  - Endpoint is $(printf '%s' "$EP_STATUS" | tr '[:lower:]' '[:upper:]'). Next: $0 --enable --yes (after health passes)."
    attention=true
  fi
  if ! $HEALTH_OK; then
    echo "  - API health FAILED ($HEALTH_REASON). Do NOT enable until fixed."
    attention=true
  fi
  if [[ "$N_EXPIRING" -gt 0 ]]; then
    echo "  - $N_EXPIRING event(s) expire from retention within $EXPIRY_WARN_DAYS days. Replay soon:"
    echo "      $0 --replay --dry-run   (preview)"
    echo "      $0 --replay --yes       (real, after --enable)"
    attention=true
  fi
  if $attention; then exit 2; fi
  echo "  All green: endpoint enabled, API healthy, nothing expiring."
}

cmd_enable() {
  echo "=== webhook-recovery --enable ==="
  echo ""
  fetch_endpoint
  if [[ "$EP_STATUS" == "enabled" ]]; then
    echo "Endpoint is already ENABLED — nothing to do."
    exit 0
  fi
  health_check
  if ! $HEALTH_OK; then
    banner "REFUSING to enable: API health check failed ($HEALTH_REASON)." \
           "Nothing was changed."
    exit 3
  fi
  confirm_gate "re-enable webhook endpoint $WEBHOOK_ID"
  echo "POST $STRIPE_API/webhook_endpoints/$WEBHOOK_ID disabled=false ..."
  curl -sS -m 20 "$STRIPE_API/webhook_endpoints/$WEBHOOK_ID" \
    -u "${STRIPE_SECRET_KEY}:" -d "disabled=false" > "$WORK_DIR/enable_resp.json" \
    || die "enable POST could not reach api.stripe.com"
  python3 - "$WORK_DIR" <<'PY' || die "enable POST returned an error (see above)"
import json, sys
d = json.load(open(f"{sys.argv[1]}/enable_resp.json"))
if "error" in d:
    print("Stripe error:", d["error"].get("message"), file=sys.stderr)
    sys.exit(1)
PY
  echo "Re-fetching endpoint to confirm ..."
  echo ""
  fetch_endpoint
  if [[ "$EP_STATUS" == "enabled" ]]; then
    echo ""
    echo "SUCCESS: endpoint is ENABLED. Next: $0 --replay --dry-run"
  else
    die "endpoint still reports status=$EP_STATUS after POST"
  fi
}

cmd_replay() {
  local hdr_suffix=""
  if $DRY_RUN; then hdr_suffix=" --dry-run (read-only)"; fi
  echo "=== webhook-recovery --replay$hdr_suffix ==="
  echo ""
  if ! $DRY_RUN && ! $YES; then
    banner "REFUSED: real --replay requires the --yes flag (plus a typed \"yes\" on a TTY)." \
           "Use --replay --dry-run to preview with zero mutations. Nothing was changed."
    exit 3
  fi
  fetch_endpoint
  if ! $DRY_RUN; then
    if [[ "$EP_STATUS" != "enabled" ]]; then
      banner "REFUSED: endpoint is still $(printf '%s' "$EP_STATUS" | tr '[:lower:]' '[:upper:]') — resends would go nowhere." \
             "Run $0 --enable --yes first. Nothing was changed."
      exit 3
    fi
    health_check
    if ! $HEALTH_OK; then
      banner "REFUSED: API health check failed ($HEALTH_REASON) — resends would" \
             "fail and re-disable the endpoint. Nothing was changed."
      exit 3
    fi
  fi
  build_inventory
  echo ""
  echo "Note: migrations/007_stripe_events_idempotency.sql (stripe_events.stripe_event_id"
  echo "UNIQUE) makes double-delivery idempotent — resending already-processed events is safe."
  echo ""
  if [[ "$N_EVENTS" -eq 0 ]]; then
    echo "No retained events to resend."
    exit 0
  fi

  # Verify the installed CLI actually supports per-endpoint resend (do not guess).
  local cli_ok=false
  if [[ -x "$STRIPE_BIN" ]] \
     && "$STRIPE_BIN" events resend --help 2>/dev/null | grep -q -- '--webhook-endpoint'; then
    cli_ok=true
  fi

  if ! $cli_ok; then
    banner "stripe CLI at $STRIPE_BIN is missing or lacks 'events resend --webhook-endpoint'." \
           "Falling back to dashboard instructions — no automated resend possible."
    echo "Dashboard steps (per event listed above, OLDEST FIRST):"
    echo "  A. Failed deliveries (attempted while endpoint was up):"
    echo "     Dashboard -> Developers -> Webhooks -> $WEBHOOK_ID -> Event deliveries"
    echo "     -> filter Failed -> Resend."
    echo "  B. Never-attempted events (created while endpoint was disabled):"
    echo "     Dashboard -> Developers -> Events -> filter by type/date -> open the"
    echo "     event -> 'Resend' / 'Send test webhook' to this endpoint."
    exit 3
  fi

  if $DRY_RUN; then
    echo "DRY RUN — the following $N_EVENTS command(s) WOULD run, oldest-first,"
    echo "with 'sleep $RESEND_SLEEP_SECS' between each. NOTHING has been sent (zero non-GET calls):"
    echo ""
    while IFS= read -r eid; do
      echo "  $STRIPE_BIN events resend $eid --webhook-endpoint=$WEBHOOK_ID --api-key \$STRIPE_SECRET_KEY --confirm"
    done < "$WORK_DIR/resend_ids.txt"
    echo ""
    echo "Run for real: $0 --replay --yes"
    exit 0
  fi

  confirm_gate "resend $N_EVENTS event(s) to $WEBHOOK_ID"
  local i=0 ok=0 fail=0 eid out
  while IFS= read -r eid; do
    i=$((i + 1))
    printf '[%d/%d] resend %s ... ' "$i" "$N_EVENTS" "$eid"
    if out="$("$STRIPE_BIN" events resend "$eid" \
                --webhook-endpoint="$WEBHOOK_ID" \
                --api-key "$STRIPE_SECRET_KEY" --confirm 2>&1)"; then
      ok=$((ok + 1)); echo "OK"
    else
      fail=$((fail + 1)); echo "FAIL"
      printf '%s\n' "$out" | sed 's/^/      /'
    fi
    if [[ "$i" -lt "$N_EVENTS" ]]; then sleep "$RESEND_SLEEP_SECS"; fi
  done < "$WORK_DIR/resend_ids.txt"
  echo ""
  echo "Done: $ok OK, $fail FAILED out of $N_EVENTS."
  if [[ "$fail" -gt 0 ]]; then
    echo "Failures above — re-run is safe (idempotent, see migration 007)."
    exit 2
  fi
}

case "$MODE" in
  status) cmd_status ;;
  enable) cmd_enable ;;
  replay) cmd_replay ;;
esac
