#!/usr/bin/env python3
"""
Deploy the canonical ktp-scheduled-restart.sh to every game host.

Safety model:
  - CONTENT GUARD, before anything touches the fleet: the canonical is
    gitignored and untracked, so `git status` can never flag drift in it and a
    deploy will otherwise ship whatever happens to be sitting there. The guard
    diffs it against the TRACKED `.example` and refuses unless the only
    differences are the values of allowlisted @hostinfo assignments. Per
    docs/runbooks/SCHEDULED_RESTART_LINEAGES.md the canonical (L2) is the
    `.example` (L3) with two placeholders filled, and that is the whole of its
    licence to differ. Drift runs BOTH ways — `.example` has been ahead.
    The allowlisted values are live Discord channel IDs in a public repo, so
    findings name keys and line numbers and never a value. Override with
    --override-example-guard "<reason>".
  - The canonical script is gitignored (it embeds the relay AUTH_SECRET), so
    there is no git baseline. Instead: fetch every host's deployed copy FIRST
    and require fleet consensus (all identical). The consensus md5 becomes the
    drift baseline; a host that disagrees with consensus is SKIPPED unless
    --force — never silently clobber an out-of-band-edited production script.
    A unified diff of consensus vs the new canonical is printed up front for
    eyeball review.
  - Timestamped backup (~/ktp-scheduled-restart.sh.bak-YYYYMMDD-HHMMSS) before
    every overwrite; a failed backup skips the host.
  - The live script is never written in place (cron executes it at 03:00 ET):
    upload goes to a .tmp sibling, ALL verification runs against the .tmp, and
    only a passing .tmp is atomically posix_rename()d over the target. A failed
    post-swap re-check auto-restores the backup. Still: avoid running within
    ~10 minutes of 03:00 ET.
  - Verification: remote md5 == local md5, `bash -n` passes on the
    host, and the known tripwire greps (from root CLAUDE.md) still hit:
      'Checking for staged .new files'  == 1
      'addons/ktpamx/modules/\\*.new'    >= 2
      'created missing.*monitoring.lock' >= 1
      'ktp_extension_loaded'             >= 3   (R8 assert, added 2026-07-07)
      'stale socket_map_ entry'          >= 1   (socket-map sweep)
      'SOCKMAP_CONTROL'                  >= 2   (...and its positive control)

Usage:
    deploy-restart-script.py [--hosts atlanta,dallas] [--force] [--dry-run]
                             [--override-example-guard REASON]

Password: $KTP_FLEET_SSH_PASSWORD or ~/.ktp_fleet_ssh_password (never hardcoded).
"""

import argparse
import collections
import difflib
import hashlib
import os
import re
import subprocess
import sys
import time

# Windows consoles default to a legacy codepage; the script body contains
# UTF-8 punctuation that must survive diff printing.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

try:
    import paramiko
except ImportError:
    print("ERROR: paramiko not installed. Run: pip install paramiko")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CANONICAL = os.path.join(SCRIPT_DIR, "ktp-scheduled-restart.sh")
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
REMOTE_PATH = "ktp-scheduled-restart.sh"  # relative to ~dodserver
EXAMPLE_REL = "scripts/ktp-scheduled-restart.sh.example"
EXAMPLE = os.path.join(REPO_ROOT, *EXAMPLE_REL.split("/"))

# The only assignments the canonical may differ from the tracked `.example` on.
# Live Discord channel IDs in a public repo: compared by KEY, never by value,
# and never printed. Derived three ways that agree — the `.example` marks
# exactly these two values as placeholders, they are the only assignments that
# actually differ, and SCHEDULED_RESTART_LINEAGES.md calls L2 "L3 with two
# placeholders filled". allowlist_gaps() re-derives the first of those on every
# run so a third placeholder cannot appear without saying so.
HOSTINFO_KEYS = ("CHANNEL_KTP", "CHANNEL_EXTERNAL")
_MASK = "<hostinfo-value-withheld>"

# Single-line anchors: a phrase spanning a line break is a documented false zero
# in this exact file, so each is asserted within one line, exactly once.
EXAMPLE_ANCHORS = (
    "#!/bin/bash",
    "CHANNEL_KTP=",
    "CHANNEL_EXTERNAL=",
    "SOCKMAP_PATTERN='stale socket_map_ entry'",
)
EXAMPLE_MIN_LINES = 400

_ASSIGN = re.compile(r"^(\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=)(.*)$")
_PLACEHOLDER = re.compile(r"(?i)placeholder|changeme|replace_?me|your_|__[A-Z0-9_]+__")

Finding = collections.namedtuple("Finding", "kind detail")

SERVERS = {
    'atlanta': '74.91.121.9',
    'dallas':  '74.91.126.55',
    'denver':  '66.163.114.109',
    'newyork': '74.91.123.64',
    'chicago': '172.238.176.101',
}

# Formatted with the path under test (the .tmp during pre-swap verification).
TRIPWIRES = [
    ("grep -c 'Checking for staged .new files' {path}", 1, "=="),
    ("grep -c 'addons/ktpamx/modules/\\*.new' {path}", 2, ">="),
    ("grep -c 'created missing.*monitoring.lock' {path}", 1, ">="),
    ("grep -c 'ktp_extension_loaded' {path}", 3, ">="),
    # The socket-map sweep and, separately, its per-tree positive control. A
    # deploy that carries the sweep but drops the control ships a check that
    # cannot tell "no hits" from "read no logs" — which is the defect the sweep
    # was written to end, so it is tripwired on its own line.
    ("grep -c 'stale socket_map_ entry' {path}", 1, ">="),
    ("grep -c 'SOCKMAP_CONTROL' {path}", 2, ">="),
]


def normalise(text):
    """Lines, with CRLF/CR folded to LF and trailing whitespace off each line."""
    return [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def normalisation_is_inert(text):
    """True when normalise() only split lines — it discarded no byte that differs.

    Trailing whitespace is content inside a heredoc, so the guard reports when
    this is false instead of quietly comparing something it altered.
    """
    return "\r" not in text and all(ln == ln.rstrip() for ln in text.split("\n"))


def assignment_key(line):
    m = _ASSIGN.match(line)
    return m.group(2) if m else None


def mask_hostinfo(lines, keys=HOSTINFO_KEYS):
    out = []
    for ln in lines:
        m = _ASSIGN.match(ln)
        out.append(m.group(1) + _MASK if m and m.group(2) in keys else ln)
    return out


def allowlist_gaps(example_text, keys=HOSTINFO_KEYS):
    """Placeholder-valued keys in the `.example` that the allowlist misses.

    Blocking, not advisory: a fill-me value this tool does not know about is one
    it cannot tell a filled value from a wrong one for.
    """
    gaps = []
    for i, ln in enumerate(normalise(example_text), 1):
        m = _ASSIGN.match(ln)
        if m and m.group(2) not in keys and _PLACEHOLDER.search(m.group(3).strip().strip("\"'")):
            gaps.append((i, m.group(2)))
    return gaps


def _describe_canonical(idx, line):
    """Canonical lines are never printed — this file is the one holding secrets."""
    key = assignment_key(line)
    return "canonical:%d  %s" % (idx, ("assignment %s=" % key) if key else "(content withheld)")


def _describe_example(idx, line):
    """`.example` lines may be printed: they are tracked in this public repo."""
    return "example:%d    | %s" % (idx, line)


def example_guard(canonical_text, example_text, keys=HOSTINFO_KEYS):
    """Findings that must be empty for the canonical to be safe to ship.

    Pass condition: after normalisation, and after masking the value of every
    allowlisted assignment in BOTH files, the two are line-for-line identical —
    and the canonical's own allowlisted values are filled in rather than the
    placeholders it was regenerated from. An unfilled one kills the 03:00
    Discord notification while the restart still prints green.
    """
    can = mask_hostinfo(normalise(canonical_text), keys)
    ex = mask_hostinfo(normalise(example_text), keys)

    findings = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, can, ex, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        detail = [" -" + _describe_canonical(i + 1, can[i]) for i in range(i1, i2)]
        detail += [" +" + _describe_example(j + 1, ex[j]) for j in range(j1, j2)]
        findings.append(Finding(tag, "\n".join(detail)))

    for i, ln in enumerate(normalise(canonical_text), 1):
        m = _ASSIGN.match(ln)
        if not m or m.group(2) not in keys:
            continue
        value = m.group(3).strip().strip("\"'")
        if not value:
            findings.append(Finding("unfilled", " !canonical:%d  %s is EMPTY" % (i, m.group(2))))
        elif _PLACEHOLDER.search(value):
            findings.append(Finding(
                "unfilled", " !canonical:%d  %s still holds the .example placeholder" % (i, m.group(2))))

    for line_no, key in allowlist_gaps(example_text, keys):
        findings.append(Finding(
            "allowlist-gap",
            " !example:%d    %s is a placeholder the allowlist does not cover — add it to "
            "HOSTINFO_KEYS if it is host-specific" % (line_no, key)))
    return findings


def load_tracked_example(repo_root=REPO_ROOT, refs=("origin/main", "HEAD")):
    """(text, source) for the tracked `.example`, preferring the git blob.

    The working-tree file is the obvious source and the wrong one: a checkout
    behind origin/main carries a stale `.example` and the compare then fails on
    hundreds of lines that are not drift. Argument list, never a shell string —
    Git Bash rewrites `ref:scripts/…` into a Windows path and `git show` then
    fails silently.
    """
    for ref in refs:
        try:
            r = subprocess.run(["git", "-C", repo_root, "show", "%s:%s" % (ref, EXAMPLE_REL)],
                               capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            break
        if r.returncode == 0:
            return r.stdout.decode("utf-8"), "git %s:%s" % (ref, EXAMPLE_REL)
    if os.path.exists(EXAMPLE):
        with open(EXAMPLE, "rb") as fh:
            return fh.read().decode("utf-8"), "working tree (NO git blob readable)"
    raise SystemExit("cannot read the tracked %s from git or the working tree" % EXAMPLE_REL)


def check_example_control(text, source):
    """Prove the real `.example` was read, not an empty string or a stub."""
    lines = text.split("\n")
    if len(lines) < EXAMPLE_MIN_LINES:
        raise SystemExit("example control FAILED: %s yielded %d lines (want >= %d) — "
                         "the guard would have compared against nothing"
                         % (source, len(lines), EXAMPLE_MIN_LINES))
    for anchor in EXAMPLE_ANCHORS:
        n = sum(1 for ln in lines if anchor in ln)
        if n != 1:
            raise SystemExit("example control FAILED: anchor %r found on %d lines in %s (want 1)"
                             % (anchor, n, source))


def validated_override(reason):
    """The override's reason, or SystemExit. A blank one is not an override."""
    if reason is not None and not reason.strip():
        raise SystemExit('--override-example-guard needs a reason, not an empty string')
    return reason


def gate_on_example(canonical_text, override_reason):
    """Run the content guard and refuse the deploy on any finding."""
    example_text, source = load_tracked_example()
    check_example_control(example_text, source)
    print("\nContent guard vs the tracked .example")
    print("  source:    %s (%d bytes)" % (source, len(example_text.encode("utf-8"))))
    print("  allowlist: %s" % ", ".join(HOSTINFO_KEYS))
    for text, label in ((canonical_text, "canonical"), (example_text, "example")):
        if not normalisation_is_inert(text):
            print("  note:      normalisation altered the %s (CR bytes or trailing "
                  "whitespace present) — it is not comparing the raw file" % label)
    if os.path.exists(EXAMPLE):
        with open(EXAMPLE, "rb") as fh:
            if fh.read().decode("utf-8") != example_text and not source.startswith("working tree"):
                print("  note:      the working-tree .example differs from the tracked blob "
                      "(stale checkout) — the tracked blob is what was compared")

    findings = example_guard(canonical_text, example_text)
    if not findings:
        print("  PASS: identical outside the allowlisted @hostinfo values")
        return
    print("  FAIL: %d finding(s) outside the allowlist" % len(findings))
    for f in findings:
        print("  [%s]" % f.kind)
        print(f.detail)
    if not override_reason:
        raise SystemExit(
            "\nRefusing to deploy. Reconcile scripts/ktp-scheduled-restart.sh against the\n"
            "tracked .example (docs/runbooks/SCHEDULED_RESTART_LINEAGES.md — read the\n"
            "DELETIONS, they are the ones that cost something), or rerun with\n"
            '  --override-example-guard "<why the .example is deliberately out of step>"')
    print("\n" + "!" * 72)
    print("EXAMPLE GUARD OVERRIDDEN — shipping content that does not match the tracked .example")
    print("reason: %s" % override_reason)
    print("!" * 72)


def fleet_password():
    pw = os.environ.get('KTP_FLEET_SSH_PASSWORD')
    if not pw:
        p = os.path.expanduser('~/.ktp_fleet_ssh_password')
        if os.path.exists(p):
            pw = open(p).read().strip()
    if not pw:
        raise SystemExit('SSH password not configured — set $KTP_FLEET_SSH_PASSWORD '
                         'or write it to ~/.ktp_fleet_ssh_password')
    return pw


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def fetch_deployed(host, pw):
    """Return (md5, content) of the deployed script on a host, or (None, None)."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username='dodserver', password=pw, timeout=20)
    try:
        sftp = ssh.open_sftp()
        try:
            data = sftp.open(REMOTE_PATH, 'rb').read()
        except IOError:
            return None, None
        return md5_bytes(data), data
    finally:
        ssh.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hosts', help='comma-separated subset (default: all)')
    ap.add_argument('--force', action='store_true',
                    help='overwrite even when a host drifted from the fleet consensus '
                         '(the canonical is gitignored — consensus IS the baseline)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--override-example-guard', metavar='REASON',
                    help='ship despite content-guard findings. REASON is printed and must say '
                         'why the tracked .example is deliberately out of step with the '
                         'canonical; deliberately not a bare --force')
    args = ap.parse_args()
    override_reason = validated_override(args.override_example_guard)

    targets = list(SERVERS) if not args.hosts else [h.strip() for h in args.hosts.split(',')]
    for t in targets:
        if t not in SERVERS:
            raise SystemExit(f"unknown host '{t}' (know: {', '.join(SERVERS)})")

    local = open(CANONICAL, 'rb').read()
    if b'\r' in local:
        raise SystemExit("canonical script contains CR bytes — fix line endings first")
    local_md5 = md5_bytes(local)
    print(f"canonical (working tree): {local_md5}  ({len(local)} bytes)")

    # Before the password, before any socket: a guard that runs after the fleet
    # has been touched is a report, not a gate.
    gate_on_example(local.decode('utf-8'), override_reason)

    pw = fleet_password()

    # Phase 1: fleet-consensus drift baseline
    print("\nPhase 1: fetching deployed copies for consensus check...")
    deployed_md5 = {}
    sample_content = None
    for name in targets:
        try:
            m, data = fetch_deployed(SERVERS[name], pw)
            deployed_md5[name] = m
            if sample_content is None and data is not None:
                sample_content = data
            print(f"  {name}: {m or 'ABSENT'}")
        except Exception as e:
            print(f"  {name}: FETCH ERROR {e}")
            deployed_md5[name] = 'ERROR'
    fetched = {m for m in deployed_md5.values() if m not in (None, 'ERROR')}
    distinct = set(fetched)
    consensus = next(iter(distinct)) if len(distinct) == 1 else None
    if consensus is None and not args.force:
        raise SystemExit(f"No fleet consensus (distinct deployed md5s: {len(distinct)}; "
                         f"absent/errored hosts excluded) — inspect by hand or rerun with --force")
    # "Nothing to deploy" requires EVERY targeted host to already match — an
    # absent or fetch-errored host must not be silently skipped just because
    # the reachable ones form a matching consensus (its cron would run a stale
    # or missing script at 03:00 while this tool reports green).
    if all(m == local_md5 for m in deployed_md5.values()):
        print("\nFleet already matches the canonical — nothing to deploy.")
        return
    if sample_content is not None:
        diff = list(difflib.unified_diff(
            sample_content.decode(errors='replace').splitlines(),
            local.decode(errors='replace').splitlines(),
            fromfile='deployed(consensus)', tofile='canonical(new)', lineterm=''))
        adds = sum(1 for l in diff if l.startswith('+') and not l.startswith('+++'))
        dels = sum(1 for l in diff if l.startswith('-') and not l.startswith('---'))
        print(f"\nConsensus -> new canonical: +{adds} / -{dels} lines")
        for l in diff[:120]:
            print("  " + l)
        if len(diff) > 120:
            print(f"  ... ({len(diff) - 120} more diff lines)")

    if args.dry_run:
        print("\ndry-run: stopping before deploy phase")
        return

    # Phase 2: deploy + verify
    ok = fail = skip = 0
    for name in targets:
        host = SERVERS[name]
        print(f"\n===== {name} ({host}) =====")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(host, username='dodserver', password=pw, timeout=20)
            sftp = ssh.open_sftp()

            dep_md5 = deployed_md5.get(name)
            if dep_md5 == local_md5:
                print("  already current — nothing to do")
                ok += 1
                ssh.close()
                continue
            if dep_md5 not in (consensus, None) and not args.force:
                print(f"  DRIFT: deployed {dep_md5} != consensus {consensus} — SKIPPING")
                skip += 1
                ssh.close()
                continue
            backup = None
            if dep_md5 is not None:
                stamp = time.strftime('%Y%m%d-%H%M%S')
                backup = f"{REMOTE_PATH}.bak-{stamp}"
                _, out, _ = ssh.exec_command(f"cp ~/{REMOTE_PATH} ~/{backup}", timeout=30)
                if out.channel.recv_exit_status() != 0:
                    print(f"  BACKUP FAILED (cp -> ~/{backup}) — SKIPPING host")
                    skip += 1
                    ssh.close()
                    continue
                print(f"  backed up -> ~/{backup}")

            # Upload to a sibling .tmp and verify THERE — the live script (which
            # cron executes at 03:00) is only replaced by an atomic rename of a
            # fully-verified file.
            tmp = f"{REMOTE_PATH}.tmp"
            with sftp.open(tmp, 'wb') as f:
                f.write(local)
            sftp.chmod(tmp, 0o755)

            # Verify: md5, bash -n, tripwires — all against the .tmp
            _, out, _ = ssh.exec_command(f"md5sum ~/{tmp}", timeout=30)
            rmd5 = out.read().decode().split()[0]
            _, out, err = ssh.exec_command(f"bash -n ~/{tmp} && echo SYNTAX_OK", timeout=30)
            syntax = 'SYNTAX_OK' in out.read().decode()
            trip_fail = []
            for tmpl, want, op in TRIPWIRES:
                cmd = tmpl.format(path=f"~/{tmp}")
                _, o, _ = ssh.exec_command(cmd, timeout=30)
                got = int((o.read().decode().strip() or '0'))
                good = (got == want) if op == '==' else (got >= want)
                if not good:
                    trip_fail.append(f"{cmd} -> {got} (want {op}{want})")

            if rmd5 != local_md5 or not syntax or trip_fail:
                print(f"  FAIL (pre-swap, live script untouched): md5={rmd5} "
                      f"(want {local_md5}) syntax={syntax}")
                for t in trip_fail:
                    print(f"    tripwire: {t}")
                try:
                    sftp.remove(tmp)
                except IOError:
                    pass
                fail += 1
                ssh.close()
                continue

            # Atomic swap, then a cheap post-swap re-check; auto-restore on mismatch.
            sftp.posix_rename(tmp, REMOTE_PATH)
            _, out, _ = ssh.exec_command(f"md5sum ~/{REMOTE_PATH}", timeout=30)
            live_md5 = out.read().decode().split()[0]
            if live_md5 == local_md5:
                print(f"  OK: md5 {live_md5}, syntax OK, tripwires pass (atomic swap)")
                ok += 1
            elif backup:
                _, out, _ = ssh.exec_command(
                    f"cp ~/{backup} ~/{REMOTE_PATH} && chmod 755 ~/{REMOTE_PATH}", timeout=30)
                restored = out.channel.recv_exit_status() == 0
                print(f"  FAIL post-swap: live md5={live_md5} (want {local_md5}) — "
                      f"backup restore {'OK' if restored else 'FAILED — FIX BY HAND NOW'}")
                fail += 1
            else:
                print(f"  FAIL post-swap: live md5={live_md5} (want {local_md5}) — "
                      "no backup existed (file was absent) — FIX BY HAND")
                fail += 1
            ssh.close()
        except Exception as e:
            print(f"  ERROR: {e}")
            fail += 1

    print(f"\nSummary: {ok} OK, {skip} skipped (drift), {fail} failed")
    sys.exit(0 if fail == 0 and skip == 0 else 1)


if __name__ == '__main__':
    main()
