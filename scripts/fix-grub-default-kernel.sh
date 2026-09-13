#!/bin/bash
# Audit -- and with --fix, correct -- which kernel a game host boots by default.
#
# The property checked: the default boot entry resolves to the newest installed
# lowlatency kernel, both in the grub.cfg that exists now and in the one the
# next update-grub will write (every kernel install runs update-grub).
#
# Fleet canon: GRUB_DEFAULT=0 plus the drop-in
#   /etc/default/grub.d/99-ktp-kernel-flavour.cfg   GRUB_FLAVOUR_ORDER="lowlatency"
# Ubuntu's 10_linux orders kernels by version first, so without the drop-in a
# newer generic kernel becomes entry 0 and the next reboot quietly leaves
# lowlatency. GRUB_FLAVOUR_ORDER is an Ubuntu grub patch (grub-sort-version).
# See docs/runbooks/GRUB_DEFAULT_KERNEL.md.
#
# Usage (on a game host, as root):
#   fix-grub-default-kernel.sh          # audit only, read-only, exit 1 on findings
#   fix-grub-default-kernel.sh --fix    # install the drop-in + update-grub, then re-audit
#
# --fix only acts on the GRUB_DEFAULT=0 shape. It refuses saved, positional and
# pinned defaults, and a grub build without the flavour patch. It NEVER reboots:
# the change takes effect at the next reboot, which is an operator action.

set -euo pipefail

# Overridable for tests; fixtures of measured fleet states live in tests/fixtures.
GRUB_CFG="${KTP_GRUB_CFG:-/boot/grub/grub.cfg}"
GRUB_DEFAULT_FILE="${KTP_GRUB_DEFAULT_FILE:-/etc/default/grub}"
GRUB_DEFAULT_DIR="${KTP_GRUB_DEFAULT_DIR:-/etc/default/grub.d}"
GRUB_ENV="${KTP_GRUB_ENV:-/boot/grub/grubenv}"
BOOT_DIR="${KTP_BOOT_DIR:-/boot}"
SORT_VERSION="${KTP_GRUB_SORT_VERSION:-/usr/lib/grub/grub-sort-version}"
UPDATE_GRUB="${KTP_UPDATE_GRUB:-update-grub}"
BACKUP_DIR="${KTP_GRUB_BACKUP_DIR:-/root}"
DROPIN_NAME="99-ktp-kernel-flavour.cfg"
DROPIN_COMMENT="# Rank lowlatency kernels above generic so entry 0 is the newest lowlatency kernel."
DROPIN_SETTING='GRUB_FLAVOUR_ORDER="lowlatency"'

FIX=0
case "${1:-}" in
    "") ;;
    --fix) FIX=1 ;;
    *) echo "usage: $0 [--fix]" >&2; exit 2 ;;
esac

fail=0
note() { echo "  $*"; }
finding() { echo "FINDING: $*"; fail=1; }
warn() { echo "WARN: $*"; }

[ -r "$GRUB_CFG" ] || { echo "FATAL: cannot read $GRUB_CFG (run as root)" >&2; exit 2; }

# Unit separator, not tab: tab is IFS whitespace, so an empty field would collapse.
US=$'\x1f'
RE_POSITIONAL='^[0-9]+(>[0-9]+)?$'
RE_ENTRY0='^(0|1>0)$'

running="${KTP_UNAME_R:-$(uname -r)}"

mapfile -t kernels < <(ls "$BOOT_DIR"/vmlinuz-* 2>/dev/null | sed 's|.*/vmlinuz-||' | sort -V)
newest_ll="$(printf '%s\n' "${kernels[@]}" | grep -- '-lowlatency$' | tail -1 || true)"
generic_installed="$(printf '%s\n' "${kernels[@]}" | grep -v -- '-lowlatency$' | grep . || true)"

# grub-mkconfig sources /etc/default/grub, then grub.d/*.cfg in glob order; the last assignment wins.
config_files=("$GRUB_DEFAULT_FILE")
while IFS= read -r f; do config_files+=("$f"); done < <(LC_ALL=C ls -1 "$GRUB_DEFAULT_DIR"/*.cfg 2>/dev/null || true)

conf_value() {  # prints "<value>US<file>" for the last assignment of $1, or nothing
    local var="$1" f line val="" src=""
    for f in "${config_files[@]}"; do
        [ -r "$f" ] || continue
        line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${var}=" "$f" | tail -1 || true)"
        [ -n "$line" ] || continue
        val="${line#*=}"; src="$f"
    done
    [ -n "$src" ] || return 0
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#\"}"; val="${val%\"}"; val="${val#\'}"; val="${val%\'}"
    printf '%s%s%s\n' "$val" "$US" "$src"
}

IFS="$US" read -r grub_default grub_default_src < <(conf_value GRUB_DEFAULT; echo) || true
IFS="$US" read -r flavour_order flavour_src < <(conf_value GRUB_FLAVOUR_ORDER; echo) || true

flavour_supported=0
grep -qs GRUB_FLAVOUR_ORDER "$SORT_VERSION" && flavour_supported=1
ll_ranked_first=0
[ "${flavour_order%% *}" = "lowlatency" ] && ll_ranked_first=1

# One record per menu item: path, kind, title, id, kernel (from the entry's linux line).
mapfile -t menu < <(awk -v S="$US" '
    function quoted(line, n,   parts, cnt) { cnt = split(line, parts, "\047"); return (2 * n <= cnt) ? parts[2 * n] : "" }
    function flush() { if (cur != "") print cur S kernel; cur = ""; kernel = "" }
    /^(menuentry|submenu) / {
        flush(); top++; child = 0; insub = ($1 == "submenu")
        if (insub) print (top - 1) S "submenu" S quoted($0, 1) S quoted($0, 2) S
        else cur = (top - 1) S "entry" S quoted($0, 1) S quoted($0, 2)
        next
    }
    insub && /^\tmenuentry / { flush(); cur = (top - 1) ">" child S "entry" S quoted($0, 1) S quoted($0, 2); child++; next }
    cur != "" && kernel == "" && /^\t+linux\t/ { k = $2; sub(/.*\//, "", k); sub(/^vmlinuz-/, "", k); kernel = k; next }
    /^}/ { flush(); insub = 0 }
    END { flush() }
' "$GRUB_CFG")

resolve() {  # GRUB default spec -> menu record, or nothing
    local spec="${1:-0}" comp prefix="" rec path kind title id match
    local -a comps
    IFS='>' read -r -a comps <<<"$spec"
    [ "${#comps[@]}" -gt 0 ] || comps=(0)
    for comp in "${comps[@]}"; do
        match=""
        for rec in "${menu[@]}"; do
            IFS="$US" read -r path kind title id _ <<<"$rec"
            if [ -z "$prefix" ]; then
                [[ "$path" == *">"* ]] && continue
            else
                [[ "$path" == "$prefix>"* && "${path#"$prefix>"}" != *">"* ]] || continue
            fi
            if [[ "$comp" =~ ^[0-9]+$ ]]; then
                [ "${path##*>}" = "$comp" ] || continue
            else
                [ "$title" = "$comp" ] || [ "$id" = "$comp" ] || continue
            fi
            match="$rec"; break
        done
        [ -n "$match" ] || return 0
        prefix="${match%%"$US"*}"
    done
    printf '%s\n' "$match"
}

predict_entry0() {  # the kernel the next update-grub puts at entry 0
    local w k
    if [ "$flavour_supported" = 1 ]; then
        for w in $flavour_order; do
            k="$(printf '%s\n' "${kernels[@]}" | grep -- "-$w\$" | tail -1 || true)"
            [ -n "$k" ] && { echo "$k"; return; }
        done
    fi
    printf '%s\n' "${kernels[@]}" | tail -1
}

pinned=0
check_spec() {  # $1 = default spec, $2 = where it comes from
    case "$1" in
        *[0-9].[0-9]*.[0-9]*-[0-9]*-*)
            pinned=1
            if [[ "$1" == *" "* ]]; then
                finding "$2 is a LITERAL TITLE pin: '$1'"
            else
                finding "$2 is a menu-id pin: '$1'"
            fi
            note "it boots that exact kernel until the entry disappears; later kernels install,"
            note "reboot-required clears because the pinned kernel is the one running, and nothing flags it."
            return ;;
    esac
    if [[ "$1" =~ $RE_POSITIONAL ]] && ! [[ "$1" =~ $RE_ENTRY0 ]]; then
        finding "$2 is a positional literal ('$1') past the first entry"
        note "the index names a place in the menu, and the menu reorders as kernels come and go."
    fi
}

baked="$(grep -E '^\s*set default=' "$GRUB_CFG" | grep -v next_entry | tail -1 | sed 's/.*default="\(.*\)".*/\1/' || true)"
saved="$(grep '^saved_entry=' "$GRUB_ENV" 2>/dev/null | cut -d= -f2- || true)"
next_entry="$(grep '^next_entry=' "$GRUB_ENV" 2>/dev/null | cut -d= -f2- || true)"

effective_spec="$baked"
[ "$baked" = '${saved_entry}' ] && effective_spec="${saved:-0}"
[ -n "$effective_spec" ] || effective_spec=0
config_spec="${grub_default:-0}"
[ "$config_spec" = saved ] && config_spec='${saved_entry}'
next_spec="$config_spec"
[ "$next_spec" = '${saved_entry}' ] && next_spec="${saved:-0}"

echo "running kernel   : $running"
echo "installed kernels: ${kernels[*]:-<none>}"
echo "newest lowlatency: ${newest_ll:-<none>}"
echo "GRUB_DEFAULT     : ${grub_default:-<unset, 0>}${grub_default_src:+  ($grub_default_src)}"
echo "flavour order    : ${flavour_order:-<unset, version order>}${flavour_src:+  ($flavour_src)}"
echo "baked default    : ${baked:-<none>}"
echo "saved_entry      : ${saved:-<none>}"

[ -n "$newest_ll" ] || finding "no lowlatency kernel is installed in $BOOT_DIR"

check_spec "$effective_spec" "the default"
[ "$next_spec" = "$effective_spec" ] || check_spec "$next_spec" "GRUB_DEFAULT"

default_rec="$(resolve "$effective_spec")"
effective_kernel=""
if [ -z "$default_rec" ]; then
    finding "could not resolve the effective default entry ('$effective_spec'); GRUB falls back to entry 0 -- inspect $GRUB_CFG by hand"
else
    IFS="$US" read -r d_path d_kind d_title _ effective_kernel <<<"$default_rec"
    echo "default entry    : $d_path -> '$d_title' -> ${effective_kernel:-<no linux line>}"
    if [ "$d_kind" != "entry" ] || [ -z "$effective_kernel" ]; then
        finding "the default entry '$d_title' is not a bootable kernel entry"
    elif [[ "$d_title" == *"(recovery mode)"* ]]; then
        finding "the default entry is a recovery-mode entry: '$d_title'"
    elif [ -n "$newest_ll" ] && [ "$effective_kernel" != "$newest_ll" ]; then
        finding "the default entry boots $effective_kernel, not the newest lowlatency kernel ($newest_ll)"
    fi
fi

if [ -n "$baked" ] && [ "$config_spec" != "$baked" ]; then
    finding "GRUB_DEFAULT ('${grub_default:-<unset>}') disagrees with the baked grub.cfg ('$baked')"
    note "the next update-grub re-bakes '${grub_default:-0}'."
fi

# Only entry-0 shapes are predictable from the kernel list alone.
if [ "$pinned" = 0 ] && [[ "$next_spec" =~ $RE_ENTRY0 ]] && [ -n "$newest_ll" ]; then
    predicted="$(predict_entry0)"
    echo "next update-grub : entry 0 -> $predicted"
    if [ "$predicted" != "$newest_ll" ] && [ "$predicted" != "$effective_kernel" ]; then
        finding "the next update-grub (any kernel install runs one) makes entry 0 $predicted, not $newest_ll"
    fi
    if [ "$predicted" = "$newest_ll" ] && [ -n "$effective_kernel" ] && [ "$effective_kernel" != "$newest_ll" ]; then
        note "grub.cfg predates the current config: running update-grub fixes the default."
    fi
    if [ -n "$flavour_order" ] && [ "$flavour_supported" = 0 ]; then
        warn "GRUB_FLAVOUR_ORDER is set but $SORT_VERSION does not implement it; entry 0 follows version order"
    fi
fi

if [ -n "$generic_installed" ] && [ "$ll_ranked_first" = 0 ] && [ -n "$newest_ll" ]; then
    warn "non-lowlatency kernels are installed and lowlatency is not ranked first; the next one newer than $newest_ll takes entry 0"
    note "install the flavour drop-in: $0 --fix"
fi
if [ "${grub_default:-0}" = saved ]; then
    warn "GRUB_DEFAULT=saved depends on grubenv being read and written at boot; the canon is GRUB_DEFAULT=0"
fi
if [ -n "$next_entry" ]; then
    one_shot="$(resolve "$next_entry")"
    warn "one-shot next_entry='$next_entry' boots ${one_shot##*"$US"} once at the next reboot"
fi
if [ -n "$newest_ll" ] && [ "$running" != "$newest_ll" ]; then
    note "running $running; the newest lowlatency kernel takes effect at the next reboot"
fi

if [ "$FIX" = 0 ]; then
    [ "$fail" = 0 ] && echo "OK: the default boot entry is the newest lowlatency kernel"
    exit $fail
fi

refuse() { echo "REFUSING --fix: $*" >&2; echo "see docs/runbooks/GRUB_DEFAULT_KERNEL.md" >&2; exit 2; }

if [ "$fail" = 0 ] && [ "$ll_ranked_first" = 1 ]; then
    echo "--fix: nothing to do"
    exit 0
fi
[ -n "$newest_ll" ] || refuse "no lowlatency kernel is installed"
[ "$pinned" = 0 ] || refuse "the default pins a kernel; remove the pin by hand first"
[ "${grub_default:-0}" = 0 ] || refuse "GRUB_DEFAULT is '$grub_default', not 0; set it to 0 by hand first"
[ "$baked" = 0 ] || refuse "grub.cfg bakes default '$baked', not 0"
[ -z "$next_entry" ] || refuse "a one-shot next_entry is pending; let it boot or clear it first"
[ "$flavour_supported" = 1 ] || refuse "$SORT_VERSION does not implement GRUB_FLAVOUR_ORDER"
if [ -n "$flavour_order" ] && [ "$ll_ranked_first" = 0 ] && [ "$flavour_src" != "$GRUB_DEFAULT_DIR/$DROPIN_NAME" ]; then
    refuse "GRUB_FLAVOUR_ORDER='$flavour_order' is set in $flavour_src"
fi

dropin="$GRUB_DEFAULT_DIR/$DROPIN_NAME"
stamp="$(date +%Y%m%d-%H%M%S)"
backup="$BACKUP_DIR/grub.cfg.pre-flavour-$stamp"
mkdir -p "$GRUB_DEFAULT_DIR" "$BACKUP_DIR"
cp -p "$GRUB_CFG" "$backup"
if [ "$(cat "$dropin" 2>/dev/null || true)" != "$DROPIN_COMMENT"$'\n'"$DROPIN_SETTING" ]; then
    printf '%s\n' "$DROPIN_COMMENT" "$DROPIN_SETTING" > "$dropin"
    echo "--fix: wrote $dropin"
fi
log="$BACKUP_DIR/update-grub.flavour-$stamp.log"
# Never pipe update-grub: a closed pipe kills it mid-write.
if ! "$UPDATE_GRUB" >"$log" 2>&1; then
    echo "FATAL: $UPDATE_GRUB failed; see $log (grub.cfg backup: $backup)" >&2
    exit 2
fi
echo "--fix: ran $UPDATE_GRUB (log $log, grub.cfg backup $backup); re-auditing"
echo
set +e
"$BASH" "${BASH_SOURCE[0]}"
rc=$?
set -e
[ "$rc" = 0 ] && echo "DONE. Takes effect at the next reboot; this script never reboots."
exit $rc
