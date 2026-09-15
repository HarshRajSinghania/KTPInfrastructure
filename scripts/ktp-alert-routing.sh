#!/usr/bin/env bash
# Shell twin of ktp_alert_routing.py — sourced, never executed.
#
# The shell producers cannot import the Python module, and shelling out to it
# would put the alert path behind an interpreter start on a box where the alert
# matters most when things are broken. So the constants live twice, and
# tests/unit/test_alert_routing.py fails if the two copies disagree. Change one,
# change the other; the test is what makes that non-optional.
#
#   . "$(dirname "$0")/ktp-alert-routing.sh"
#   ktp_alert_route page                 # sets KTP_ALERT_GLYPH / _COLOR / _LANE
#   ktp_alert_channel "$KTP_ALERT_LANE" "$LEGACY_CHANNEL"   # -> KTP_ALERT_CHANNEL
#
# Channel ids are NOT here. `ktp_alert_channel` reads the lane's id from the
# environment (which the relay conf is sourced into) and falls back to whatever
# the producer already used, so sourcing this changes no routing until an
# operator maps lanes to ids.

# >>> ktp-alert-canon
KTP_GREEN=5763719    # #57F287
KTP_YELLOW=16763904  # #FFCC00
KTP_RED=15548997     # #ED4245
KTP_GREY=9807270     # #95A5A6

KTP_GLYPH_PAGE='🔴'
KTP_GLYPH_WARN='🟠'
KTP_GLYPH_INFO='⚪'
KTP_GLYPH_RECOVERY='🟢'

KTP_LANE_PAGE='page'
KTP_LANE_OPS_DAILY='ops-daily'
KTP_LANE_OPS_WEEKLY='ops-weekly'
KTP_LANE_COMMUNITY='community'
# <<< ktp-alert-canon

# ktp_alert_route <severity> [lane]
# Sets KTP_ALERT_GLYPH, KTP_ALERT_COLOR, KTP_ALERT_LANE. Returns 2 on an
# unknown severity rather than defaulting — a typo must not render as INFO.
ktp_alert_route() {
    local severity="$1" lane="${2:-}"
    case "$severity" in
        page)     KTP_ALERT_GLYPH="$KTP_GLYPH_PAGE";     KTP_ALERT_COLOR="$KTP_RED";    KTP_ALERT_LANE="${lane:-$KTP_LANE_PAGE}" ;;
        warn)     KTP_ALERT_GLYPH="$KTP_GLYPH_WARN";     KTP_ALERT_COLOR="$KTP_YELLOW"; KTP_ALERT_LANE="${lane:-$KTP_LANE_OPS_DAILY}" ;;
        info)     KTP_ALERT_GLYPH="$KTP_GLYPH_INFO";     KTP_ALERT_COLOR="$KTP_GREY";   KTP_ALERT_LANE="${lane:-$KTP_LANE_OPS_DAILY}" ;;
        recovery) KTP_ALERT_GLYPH="$KTP_GLYPH_RECOVERY"; KTP_ALERT_COLOR="$KTP_GREEN";  KTP_ALERT_LANE="${lane:-$KTP_LANE_PAGE}" ;;
        *) echo "ktp_alert_route: unknown severity '$severity'" >&2; return 2 ;;
    esac
    return 0
}

# ktp_alert_channel <lane> [legacy_channel]
# Sets KTP_ALERT_CHANNEL and KTP_ALERT_CHANNEL_SOURCE. Returns 1 when the lane
# is unmapped and no legacy channel was given — it never guesses an id.
ktp_alert_channel() {
    local lane="$1" legacy="${2:-}" key="" value=""
    case "$lane" in
        page)       key='KTP_CHANNEL_PAGE' ;;
        ops-daily)  key='KTP_CHANNEL_OPS_DAILY' ;;
        ops-weekly) key='KTP_CHANNEL_OPS_WEEKLY' ;;
        community)  key='KTP_CHANNEL_COMMUNITY' ;;
        *) echo "ktp_alert_channel: unknown lane '$lane'" >&2; return 2 ;;
    esac
    eval "value=\${$key:-}"
    if [ -n "$value" ]; then
        case "$value" in
            *[!0-9]*|'') echo "ktp_alert_channel: $key is not a channel id: $value" >&2; return 2 ;;
        esac
        KTP_ALERT_CHANNEL="$value"; KTP_ALERT_CHANNEL_SOURCE='env'; return 0
    fi
    if [ -n "$legacy" ]; then
        KTP_ALERT_CHANNEL="$legacy"; KTP_ALERT_CHANNEL_SOURCE='legacy'; return 0
    fi
    echo "ktp_alert_channel: no channel for lane '$lane' (set $key) and no legacy channel" >&2
    return 1
}

# ktp_alert_spool_line <producer> <severity> <text> [lane]
# A digest line instead of a post. Written as one `printf` so two producers
# racing on the spool interleave whole lines rather than fragments.
ktp_alert_spool_line() {
    local producer="$1" severity="$2" text="$3" lane="${4:-ops-daily}"
    local dir="${KTP_ALERT_SPOOL_DIR:-/var/lib/ktp-alerts}"
    mkdir -p "$dir" 2>/dev/null || { echo "ktp_alert_spool_line: cannot create $dir" >&2; return 1; }
    ktp_alert_route "$severity" >/dev/null || return 2
    printf '%s\n' "$(jq -cn \
        --argjson ts "$(date +%s)" \
        --arg producer "$producer" \
        --arg severity "$severity" \
        --arg glyph "$KTP_ALERT_GLYPH" \
        --arg text "$text" \
        '{ts:$ts,producer:$producer,severity:$severity,glyph:$glyph,text:$text}')" \
        >> "$dir/$lane.jsonl"
}
