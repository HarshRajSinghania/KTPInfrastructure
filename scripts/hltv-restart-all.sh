#!/bin/bash
# KTP HLTV Scheduled Restart Script
# Restarts all HLTV instances and sends Discord notification
#
# Location: /usr/local/bin/hltv-restart-all.sh (on data server)
# Runs via the `hltv-restart.timer` SYSTEMD TIMER (03:00 + 11:00 ET), NOT
# cron — soak-verify greps `journalctl -u hltv-restart`, so redeploying this
# as a cron job (the pre-2026 pattern this header used to document) would
# silently break that check. Log: /var/log/hltv-restart.log

# ============================================================================
# Configuration
# ============================================================================
source "${KTP_RELAY_CONF:-/etc/ktp/discord-relay.conf}"

# Severity, glyph, colour and lane. Replaces the raw-hex constants this script
# carried (65280 / 16750848 / 16711680), which rendered pure green and pure red
# against every other producer's KTP palette.
. "$(dirname "${BASH_SOURCE[0]}")/ktp-alert-routing.sh" || {
    echo "FATAL: ktp-alert-routing.sh not found beside $0 — deploy it first" >&2; exit 3; }

# Last run's severity, so a green that follows a failure still posts its
# all-clear. Two runs a day; a lost state file costs one silent green.
STATE_FILE="${HLTV_RESTART_STATE:-/var/lib/ktp-alerts/hltv-restart-all.state}"

# KTP emoji
KTP_EMOJI="<:KTP:1002382703020212245>"

# Server name for footer
SERVER_NAME="KTP - HLTV"

# How long to wait for restarted proxies to reach their game servers. The slowest
# fire is 03:00, when the game servers restart at the same moment.
CONNECT_WAIT_SECONDS="${CONNECT_WAIT_SECONDS:-180}"
SETTLE_SECONDS="${SETTLE_SECONDS:-5}"
POLL_SECONDS="${POLL_SECONDS:-5}"

# ============================================================================
# Restart Logic
# ============================================================================
TIMESTAMP=$(TZ='America/New_York' date '+%Y-%m-%d %H:%M:%S EST')
LOG_PREFIX="[$TIMESTAMP]"

echo "$LOG_PREFIX Starting HLTV scheduled restart..."

SUCCESS=0
FAILED=0
FAILED_PORTS=""
NOT_CONNECTED_PORTS=""
RESTART_EPOCH=$(date +%s)

RESTARTED_PORTS=""
for port in $(seq 27020 27043); do  # 27044 (chi5) disabled 2026-04-10
    if systemctl restart hltv@$port 2>/dev/null; then
        RESTARTED_PORTS="$RESTARTED_PORTS $port"
    else
        ((FAILED++))
        FAILED_PORTS="$FAILED_PORTS $port"
        echo "$LOG_PREFIX hltv@$port restart command failed"
    fi
done

# systemctl restart returns success once the main process starts — it does NOT
# confirm the process stays up. Give a crash-looper (bad hltv.cfg, port conflict,
# corrupt cache) a moment to fail, then verify actual state, so a dead instance
# isn't counted green. The hourly health cron is otherwise the only backstop,
# leaving up to ~an hour of silent non-recording on that port.
ACTIVE_PORTS=""
if [ -n "$RESTARTED_PORTS" ]; then
    sleep "$SETTLE_SECONDS"
    for port in $RESTARTED_PORTS; do
        if systemctl is-active --quiet hltv@$port; then
            ACTIVE_PORTS="$ACTIVE_PORTS $port"
        else
            ((FAILED++))
            FAILED_PORTS="$FAILED_PORTS $port"
            echo "$LOG_PREFIX hltv@$port restarted but is not active (crash-loop?)"
        fi
    done
fi

# Active is not connected either. A proxy can come up, bind, skip its own config
# and answer "Not connected." until the next restart, recording nothing. Only a
# connect line logged after that unit's own Started line counts.
proxy_connected() {
    journalctl -u "hltv@$1" --since "@$RESTART_EPOCH" --no-pager -o cat 2>/dev/null \
        | awk '/^Started hltv@/ { up = 0 } /^(Received baseline|Connected to Game Server)/ { up = 1 } END { exit (up ? 0 : 1) }'
}

PENDING="$ACTIVE_PORTS"
DEADLINE=$(( $(date +%s) + CONNECT_WAIT_SECONDS ))
while [ -n "$PENDING" ]; do
    STILL=""
    for port in $PENDING; do
        if proxy_connected "$port"; then
            ((SUCCESS++))
        else
            STILL="$STILL $port"
        fi
    done
    PENDING="$STILL"
    if [ -z "$PENDING" ] || [ "$(date +%s)" -ge "$DEADLINE" ]; then
        break
    fi
    sleep "$POLL_SECONDS"
done

for port in $PENDING; do
    ((FAILED++))
    NOT_CONNECTED_PORTS="$NOT_CONNECTED_PORTS $port"
    echo "$LOG_PREFIX hltv@$port is active but failed to connect to its game server within ${CONNECT_WAIT_SECONDS}s"
done

echo "$LOG_PREFIX $SUCCESS succeeded, $FAILED failed"
[ -n "$FAILED_PORTS" ] && echo "$LOG_PREFIX Failed ports:$FAILED_PORTS"
[ -n "$NOT_CONNECTED_PORTS" ] && echo "$LOG_PREFIX Not connected:$NOT_CONNECTED_PORTS"

# ============================================================================
# Discord Notification
# ============================================================================
FOOTER_TIMESTAMP=$(TZ='America/New_York' date '+%m/%d/%Y %I:%M %p EST')
TOTAL=$((SUCCESS + FAILED))

FAIL_DETAIL=""
[ -n "$FAILED_PORTS" ] && FAIL_DETAIL="$FAIL_DETAIL\\n**Failed ports:**$FAILED_PORTS"
[ -n "$NOT_CONNECTED_PORTS" ] && FAIL_DETAIL="$FAIL_DETAIL\\n**Up but not connected, recording nothing:**$NOT_CONNECTED_PORTS"

# >>> ktp-hltv-restart-severity
# hltv_restart_severity <failed> <succeeded> — page / warn / info.
# A clean restart of scheduled work is a confirmation, not an alert, so it is
# `info`; `hltv_restart_should_post` is what decides whether an info run is
# heard at all.
hltv_restart_severity() {
    local failed="$1" succeeded="$2"
    if [ "$failed" -eq 0 ]; then echo info
    elif [ "$succeeded" -gt 0 ]; then echo warn
    else echo page
    fi
}

# hltv_restart_should_post <severity> <previous_severity>
# Silence means healthy: an `info` run posts only when the run before it was
# not, which is the all-clear a page owes. An empty previous (first run, lost
# state file) stays quiet rather than manufacturing a recovery for a page
# nobody saw.
hltv_restart_should_post() {
    local severity="$1" previous="${2:-}"
    case "$severity" in
        page|warn) return 0 ;;
    esac
    case "$previous" in
        page|warn) return 0 ;;
    esac
    return 1
}
# <<< ktp-hltv-restart-severity

SEVERITY=$(hltv_restart_severity "$FAILED" "$SUCCESS")
PREV_SEVERITY=$(cat "$STATE_FILE" 2>/dev/null | tr -d '[:space:]')

if [ "$FAILED" -eq 0 ]; then
    TITLE_TEXT="HLTV Restart Complete"
    DESCRIPTION="All $SUCCESS HLTV instances restarted and connected."
elif [ "$SUCCESS" -gt 0 ]; then
    TITLE_TEXT="HLTV Restart - Partial"
    DESCRIPTION="$SUCCESS/$TOTAL instances restarted and connected.$FAIL_DETAIL"
else
    TITLE_TEXT="HLTV Restart Failed"
    DESCRIPTION="No instance restarted and connected!$FAIL_DETAIL"
fi

# A green run that follows a bad one is the recovery, not another routine post.
POST_SEVERITY="$SEVERITY"
case "$SEVERITY:$PREV_SEVERITY" in
    info:page|info:warn) POST_SEVERITY=recovery ;;
esac

ktp_alert_route "$POST_SEVERITY" || exit 1
TITLE="$KTP_ALERT_GLYPH $KTP_EMOJI $TITLE_TEXT"
COLOR="$KTP_ALERT_COLOR"

mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null && printf '%s\n' "$SEVERITY" > "$STATE_FILE"

# Function to send Discord embed
send_discord_embed() {
    local channel_id="$1"
    if [ -z "$channel_id" ]; then
        return
    fi

    local payload=$(cat <<EOF
{
  "channelId": "$channel_id",
  "embeds": [{
    "title": "$TITLE",
    "description": "$DESCRIPTION",
    "color": $COLOR,
    "footer": {
      "text": "$SERVER_NAME - $FOOTER_TIMESTAMP"
    }
  }]
}
EOF
)

    curl -s -X POST "$RELAY_URL" \
        -H "X-Relay-Auth: $AUTH_SECRET" \
        -H "Content-Type: application/json" \
        -d "$payload"
    echo ""
}

# The CHANNEL_HLTV_STATUS / _EXTERNAL pair is deliberately left alone: the two
# ids are a routing decision for the operator, not something to collapse here.
if hltv_restart_should_post "$SEVERITY" "$PREV_SEVERITY" || [ -n "${KTP_ALERT_ALWAYS_POST:-}" ]; then
    echo "$LOG_PREFIX Sending Discord notifications (severity=$POST_SEVERITY)..."
    send_discord_embed "$CHANNEL_HLTV_STATUS"
    send_discord_embed "$CHANNEL_HLTV_STATUS_EXTERNAL"
else
    echo "$LOG_PREFIX Clean restart ($SUCCESS/$TOTAL connected) — digest line, no post."
    ktp_alert_spool_line hltv-restart-all info \
        "HLTV: $SUCCESS/$TOTAL proxies restarted and connected" ops-daily || true
fi

echo "$LOG_PREFIX HLTV scheduled restart complete."
