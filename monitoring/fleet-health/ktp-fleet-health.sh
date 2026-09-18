#!/bin/bash
# ktp-fleet-health.sh — per-host fleet alerter, runs every minute via cron.
#
# Fires a Discord webhook alert when `pgrep -c hlds_linux` drops below the
# expected instance count for N consecutive minutes. Single alert per state
# transition (one "DEGRADED" post on decline, one "RECOVERED" post on return).
# Silent when healthy. Designed to catch any scenario that takes instances
# offline — not just the specific bug that caused the 2026-04-24 outage.
#
# CONFIG SOURCES (loaded in order; later overrides earlier):
#   1. Script defaults (below) — empty webhook means "monitor silently".
#   2. /etc/ktp/fleet-health.conf — system-wide config (root-owned, dodserver-readable).
#   3. ~/.ktp-fleet-health/config.sh — per-host overrides.
#
# CONFIG KEYS (all optional; sensible defaults below):
#   EXPECTED=N             — instance count. Defaults to NUM_INSTANCES.
#   BASE_PORT=27015        — first game port; enumerate up to NUM_INSTANCES.
#   NUM_INSTANCES=5        — used for both EXPECTED and port enumeration.
#   THRESHOLD_MINUTES=3    — debounce window before firing DEGRADED alert.
#   WEBHOOK_URL=""         — Discord webhook. Empty = local-only monitoring.
#   MENTION_USER_ID=""     — Discord user to @-mention. Empty = no ping.
#   LOCATION=""            — override the hostname-derived location code.
#   RESTART_STATE_DIR=""   — where ktp-scheduled-restart.sh keeps its cron
#                            backup. Defaults to ${KTP_STATE_DIR:-$HOME/.ktp};
#                            must match that script or the gate below suppresses
#                            nothing.
#   RESTART_GRACE_MINUTES=10 — how long a stripped monitor cron is treated as a
#                            restart in flight rather than a fault.
#   DISK_MOUNTS=""         — mounts to watch, space-separated. Default: the
#                            filesystem holding $HOME (where demos, HLTV
#                            recordings and logs land) plus /, deduplicated.
#   DISK_PCT_WARN=75       — usage or inode percent that opens a disk alert.
#   DISK_PCT_CLEAR=72      — percent it must fall back under to close it. Same
#                            deadband as the data server's own disk check.
#
# STATE (~/.ktp-fleet-health/state):
#   CONSECUTIVE_BAD=N      — minutes consecutively below expected
#   ALERT_STATE=healthy|unhealthy
#   LAST_RUN=epoch
#   LAST_RUNNING=N
#   CRON_STATE=armed|incomplete
#   LAST_MONITOR_CRONS=N
#   DISK_WARN_MOUNTS="..."  — mounts currently in the warned state
#
# CRON:
#   * * * * * /home/dodserver/ktp-fleet-health.sh >/dev/null 2>&1

set -euo pipefail

HOME_DIR=${HOME:-/home/dodserver}
STATE_DIR=$HOME_DIR/.ktp-fleet-health
STATE_FILE=$STATE_DIR/state
SYSTEM_CONFIG=/etc/ktp/fleet-health.conf
USER_CONFIG=$STATE_DIR/config.sh
HOSTNAME_SHORT=$(hostname -s 2>/dev/null || hostname)

mkdir -p "$STATE_DIR"

# Defaults — overridable via either config file.
BASE_PORT=27015
NUM_INSTANCES=5
EXPECTED=""                # auto-derives from NUM_INSTANCES if unset after config load
THRESHOLD_MINUTES=3
WEBHOOK_URL=""             # empty = silent monitoring (no Discord posts)
MENTION_USER_ID=""
LOCATION=""
RESTART_STATE_DIR=""       # auto-derives below if unset after config load
RESTART_GRACE_MINUTES=10
DISK_MOUNTS=""             # auto-derives below if unset after config load
DISK_PCT_WARN=75
DISK_PCT_CLEAR=72

# Load configs (system first, then per-host)
[ -r "$SYSTEM_CONFIG" ] && source "$SYSTEM_CONFIG"
[ -r "$USER_CONFIG"   ] && source "$USER_CONFIG"

# Derive EXPECTED if config didn't set it explicitly.
[ -z "$EXPECTED" ] && EXPECTED=$NUM_INSTANCES

# Same default as ktp-scheduled-restart.sh's CRON_STATE_DIR; if that script is
# pointed elsewhere, both need the override.
[ -z "$RESTART_STATE_DIR" ] && RESTART_STATE_DIR=${KTP_STATE_DIR:-$HOME_DIR/.ktp}
RESTART_CRON_BACKUP=$RESTART_STATE_DIR/monitor-cron.bak

# Resolve LOCATION if config didn't override. Known KTP hosts map to short
# codes for tidier alert titles; everything else falls back to hostname.
if [ -z "$LOCATION" ]; then
    case "$HOSTNAME_SHORT" in
        neinatl*|neinatlanta)    LOCATION="ATL" ;;
        neindallas|neindal*)     LOCATION="DAL" ;;
        neindenver|neinden*)     LOCATION="DEN" ;;
        neinnewyork|neinny*)     LOCATION="NY"  ;;
        neinchicago|neinchi*)    LOCATION="CHI" ;;
        *)                       LOCATION="$HOSTNAME_SHORT" ;;
    esac
fi

# Count running instances. procps pgrep -c prints "0" AND exits 1 when nothing
# matches, so `|| echo 0` INSIDE the substitution captured "0\n0" — the integer
# compare below then errored (swallowed by the cron redirect) and took the
# else-branch, resetting the debounce every minute: a TOTAL outage (0/5) never
# alerted while partial outages worked. `|| true` outside the substitution
# keeps pgrep's own "0" and absorbs the exit-1 for set -e.
RUNNING=$(pgrep -c hlds_linux 2>/dev/null) || true
[ -n "$RUNNING" ] || RUNNING=0

# Load state
CONSECUTIVE_BAD=0
ALERT_STATE=healthy
DISK_WARN_MOUNTS=""
[ -f "$STATE_FILE" ] && source "$STATE_FILE"

# Update consecutive-bad counter
if [ "$RUNNING" -lt "$EXPECTED" ]; then
    CONSECUTIVE_BAD=$((CONSECUTIVE_BAD + 1))
else
    CONSECUTIVE_BAD=0
fi

# Minimal JSON-escape for embed description (handles only common chars)
json_escape() {
    local s=${1//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\n'/\\n}
    printf '%s' "$s"
}

send_alert() {
    local title="$1"
    local desc="$2"
    local color="$3"
    # No webhook configured: monitor-only mode. Skip the network call entirely
    # rather than emitting a curl error that nobody will see.
    [ -z "$WEBHOOK_URL" ] && return 0
    local safe_title safe_desc
    safe_title=$(json_escape "$title")
    safe_desc=$(json_escape "$desc")
    local content=""
    local allowed_mentions='"allowed_mentions":{"parse":[]}'
    if [ -n "$MENTION_USER_ID" ]; then
        content="\"content\":\"<@${MENTION_USER_ID}>\","
        allowed_mentions="\"allowed_mentions\":{\"users\":[\"${MENTION_USER_ID}\"]}"
    fi
    local payload
    payload=$(printf '{%s"embeds":[{"title":"%s","description":"%s","color":%s}],%s}' \
        "$content" "$safe_title" "$safe_desc" "$color" "$allowed_mentions")
    curl -s -m 10 -X POST "$WEBHOOK_URL" \
        -H "Content-Type: application/json" \
        -d "$payload" >/dev/null 2>&1 || true
}

# Enumerate which ports look down (cosmetic, for the alert body).
# Range derives from BASE_PORT + NUM_INSTANCES so LAN/custom deployments work.
down_ports() {
    local out="" port
    for ((i=0; i<NUM_INSTANCES; i++)); do
        port=$((BASE_PORT + i))
        [ -d "$HOME_DIR/dod-$port" ] || continue
        if ! pgrep -f "hlds_linux.*-port $port" >/dev/null 2>&1; then
            out="${out}${port} "
        fi
    done
    printf '%s' "${out% }"
}

# Is the per-instance monitor cron still armed?
#
# This script alerts when instances are DOWN. It cannot tell whether they will
# come back: recovery rides on LinuxGSM's per-minute monitor cron, and the
# nightly restart strips that cron and puts it back at the end. If it ever dies
# between the two the cron stays off, and a later reboot leaves the host down
# with nothing to lift it -- silently, because an absent cron looks exactly like
# a quiet one.
#
# grep -c prints "0" AND exits 1 when nothing matches -- the same trap the
# pgrep comment above documents, and it bites hardest HERE because zero is the
# condition being alerted on. `|| true` sits outside the substitution so the
# "0" survives and set -e does not fire.
#
# Count, do not just test presence: a host that lost ONE instance's line still
# greps non-zero, so `-gt 0` would pass while that instance is unprotected.
# EXPECTED, not NUM_INSTANCES: Chicago runs 4 and sets EXPECTED=4 per-host while
# NUM_INSTANCES stays at its default 5, so the latter false-alarms there.
MONITOR_CRONS=$(crontab -l 2>/dev/null | grep -c '^[^#]*monitor') || true
[ -n "$MONITOR_CRONS" ] || MONITOR_CRONS=0
CRON_STATE=${CRON_STATE:-armed}

# >>> ktp-monitor-cron-gate
# ktp-scheduled-restart.sh strips these same lines on purpose and puts them back
# a minute or so later, so a bare count fires on every host every night and
# buries the one case worth a ping. That script keeps monitor-cron.bak on disk
# for exactly the stripped window (written before the strip, removed by its EXIT
# trap), so the sentinel answers what the count cannot: a FRESH one means
# maintenance, a STALE one means the restart never re-armed.
monitor_cron_gate() {
    local count=$1 expected=$2 state=$3 sentinel=$4 grace_s=$5 now=$6
    local mtime age
    CRON_VERDICT=quiet
    CRON_STATE=$state

    if [ "$count" -ge "$expected" ]; then
        if [ "$state" = "incomplete" ]; then
            CRON_VERDICT=clear
            CRON_STATE=armed
        fi
        return 0
    fi

    if [ -f "$sentinel" ]; then
        # An unreadable sentinel yields mtime 0, so the age is enormous and the
        # gate alerts. A guard that cannot read its own evidence must fire, never
        # suppress; a backwards clock jump gives a negative age and does the same.
        mtime=$(stat -c %Y "$sentinel" 2>/dev/null) || mtime=0
        [ -n "$mtime" ] || mtime=0
        age=$((now - mtime))
        if [ "$age" -ge 0 ] && [ "$age" -lt "$grace_s" ]; then
            CRON_VERDICT=suppressed
            return 0
        fi
    fi

    if [ "$state" = "armed" ]; then
        CRON_VERDICT=warn
        CRON_STATE=incomplete
    fi
    return 0
}
# <<< ktp-monitor-cron-gate

monitor_cron_gate "$MONITOR_CRONS" "$EXPECTED" "$CRON_STATE" \
    "$RESTART_CRON_BACKUP" "$((RESTART_GRACE_MINUTES * 60))" "$(date +%s)"

case "$CRON_VERDICT" in
    warn)
        send_alert "⚠️ ${LOCATION} monitor cron INCOMPLETE — ${MONITOR_CRONS}/${EXPECTED}" "Auto-restart is not armed for every instance and no scheduled restart is in flight. A reboot now would leave instances down with nothing to bring them back. Check: crontab -l | grep monitor" 16776960
        ;;
    clear)
        send_alert "✅ ${LOCATION} monitor cron re-armed — ${MONITOR_CRONS}/${EXPECTED}" "Auto-restart is armed for every instance again." 3066993
        ;;
esac

# ---- Disk ----
# The data server has watched its own disks since May; the five game hosts had
# nothing, and they are the hosts that write demos, HLTV recordings and logs
# DURING a match. A full disk here does not page -- it shows up as a server that
# stopped recording, or one that crashed mid-half. Same thresholds and the same
# deadband as the data server's check, so a value parked on the line does not
# flap, and one post per transition like everything else in this file.
#
# >>> ktp-disk-gate
# disk_gate <mount> <pct> <ipct> <warn> <clear> <was-warned:0|1>
# Sets DISK_VERDICT=quiet|warn|clear. A non-numeric reading (df failed, mount
# gone) is quiet, never a clear: a check that cannot read its evidence must not
# report a recovery it did not observe.
disk_gate() {
    local mount=$1 pct=$2 ipct=$3 warn=$4 clear=$5 was=$6 worst
    DISK_VERDICT=quiet
    [[ $pct =~ ^[0-9]+$ ]] || return 0
    [[ $ipct =~ ^[0-9]+$ ]] || ipct=0
    worst=$pct
    [ "$ipct" -gt "$worst" ] && worst=$ipct
    if [ "$worst" -ge "$warn" ]; then
        [ "$was" -eq 0 ] && DISK_VERDICT=warn
    elif [ "$worst" -lt "$clear" ]; then
        [ "$was" -eq 1 ] && DISK_VERDICT=clear
    fi
    return 0
}
# <<< ktp-disk-gate

# Default mounts: whatever holds $HOME_DIR (the game trees), plus /. df -P
# resolves the mount for a path; a host where both are one filesystem yields
# one entry after the dedupe.
if [ -z "$DISK_MOUNTS" ]; then
    home_mount=$(df -P "$HOME_DIR" 2>/dev/null | awk 'NR==2 {print $6}') || true
    DISK_MOUNTS=$(printf '%s\n' "${home_mount:-/}" "/" | sort -u | tr '\n' ' ')
fi

NEW_DISK_WARN=""
for mount in $DISK_MOUNTS; do
    read -r pct ipct < <(
        { df -P -k "$mount" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); printf "%s ", $5}';
          df -P -i "$mount" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}'; } ) || true
    was=0
    case " $DISK_WARN_MOUNTS " in *" $mount "*) was=1 ;; esac
    disk_gate "$mount" "${pct:-}" "${ipct:-}" "$DISK_PCT_WARN" "$DISK_PCT_CLEAR" "$was"
    case "$DISK_VERDICT" in
        warn)
            send_alert "💽 ${LOCATION} disk ${mount} at ${pct}% (inodes ${ipct:-?}%)" "Above ${DISK_PCT_WARN}%. Demos, HLTV recordings and logs land here; a full disk stops recording first and the server second. Check: df -h ${mount}; du -sh ~/dod-*/serverfiles/dod/*.dem 2>/dev/null | sort -h | tail" 16776960
            was=1
            ;;
        clear)
            send_alert "✅ ${LOCATION} disk ${mount} back to ${pct}%" "Below ${DISK_PCT_CLEAR}% again." 3066993
            was=0
            ;;
    esac
    [ "$was" -eq 1 ] && NEW_DISK_WARN="${NEW_DISK_WARN}${mount} "
done
DISK_WARN_MOUNTS=${NEW_DISK_WARN% }

# State transitions
if [ "$CONSECUTIVE_BAD" -ge "$THRESHOLD_MINUTES" ] && [ "$ALERT_STATE" = "healthy" ]; then
    PORTS_DOWN=$(down_ports)
    # Format the ports-down line. If enumeration returned nothing but the counter
    # says we're under expected (weird state — e.g. all processes running but count
    # mismatch from some other cause), say so explicitly instead of "unknown".
    if [ -n "$PORTS_DOWN" ]; then
        PORTS_LINE="**Missing:** ${PORTS_DOWN// /, }"
    else
        PORTS_LINE="**Missing:** (no port missing from enumeration — investigate count source mismatch)"
    fi
    send_alert \
        "🚨 ${LOCATION} DEGRADED — ${RUNNING}/${EXPECTED} hlds_linux" \
        "Below expected for **${CONSECUTIVE_BAD} min**.
${PORTS_LINE}" \
        15158332
    ALERT_STATE=unhealthy
elif [ "$RUNNING" -eq "$EXPECTED" ] && [ "$ALERT_STATE" = "unhealthy" ]; then
    send_alert \
        "✅ ${LOCATION} recovered — ${RUNNING}/${EXPECTED} hlds_linux" \
        "Back to expected instance count. Outage window: previous alert → now." \
        3066993
    ALERT_STATE=healthy
fi

# Persist state
cat > "$STATE_FILE" <<EOF
CONSECUTIVE_BAD=$CONSECUTIVE_BAD
ALERT_STATE=$ALERT_STATE
LAST_RUN=$(date +%s)
LAST_RUNNING=$RUNNING
CRON_STATE=$CRON_STATE
LAST_MONITOR_CRONS=$MONITOR_CRONS
DISK_WARN_MOUNTS="$DISK_WARN_MOUNTS"
EOF
