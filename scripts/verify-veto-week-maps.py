"""
Verify that every ACTIVE veto map and every scheduled `ktp.week.map` exists on
all 24 fleet instances and on FastDL.

Why this exists: a 2026-09-10 sweep found `dod_railyard_s9c.bsp` missing on
all four Chicago instances while present on the other twenty. That file
turned out to matter for nothing live — it isn't a `ktp.week.map`, and the
active veto pool's only railyard entry is the near-miss spelling
`railyard_s9d` (inactive `s9d` in the pool vs. the found-missing `s9c` on
disk). But nothing had ever checked the property that DOES matter: is every
map the site can actually schedule — via the live veto pool or a week's
locked map — present everywhere it needs to be. This script is that check,
run live against the pool instead of a hardcoded snapshot so it keeps
working as the pool changes.

Map naming: `ktp.veto_map.map_name` is the bare, lowercase, versioned engine
name with no `dod_` prefix (`railroad2_s9a`, never `dod_railroad2_s9a`) —
see keep-the-prac's `src/features/veto/map-name.ts`. The on-disk/FastDL
filename is always `dod_<map_name>.bsp`. `ktp.week.map` is stored the other
way — already `dod_`-prefixed (`dod_thunder2`) — so it needs no transform.

Playoff weeks are NULL by contract (the veto decides them), not a gap — see
`ktp.week.kind = 'playoff'`. Regular weeks with a locked map that isn't
built yet (tracked separately, e.g. S10 weeks 5-7 pending art/build) are
reported but not escalated; pass --known-unbuilt to name them explicitly
per run instead of hardcoding a season-specific list here.

Positive control: DOD_POSITIVE_CONTROL_MAP below must read as present
5/5/5/5/4 (Chicago runs 4 instances, not 5 — its 5th, 27019, was deleted
2026-07-13) or the sweep is reading the wrong directory and every other
result in the run is suspect.

Credentials: SSH host list comes from the same `audit-fleet.json` config
`scripts/audit-fleet-drift.py` uses (see `scripts/audit-fleet.json.example`
for the schema) — never hardcode a password here. Supabase access is a
read-only anon key via KTP_SUPABASE_URL / KTP_SUPABASE_ANON_KEY.

Usage:
    export KTP_SUPABASE_URL=https://<project>.supabase.co
    export KTP_SUPABASE_ANON_KEY=<anon/publishable key>
    python3 verify-veto-week-maps.py [--config audit-fleet.json] [--json]

Dependencies: paramiko.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import paramiko

DOD_POSITIVE_CONTROL_MAP = "dod_thunder2"
FASTDL_BASE = "https://fastdl.ktpdod.com/dod/maps"
FASTDL_BOGUS_CONTROL = "dod_zzz_nonexistent_map_control"

DEFAULT_CONFIG_ENV = "KTP_AUDIT_FLEET_CONFIG"
DEFAULT_CONFIG_PATH = "/etc/ktp/audit-fleet.json"


def load_fleet_config(explicit_path: str | None) -> dict:
    path = explicit_path or os.environ.get(DEFAULT_CONFIG_ENV, DEFAULT_CONFIG_PATH)
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Fleet config not found at {p}. Copy scripts/audit-fleet.json.example "
            f"to a private location and point {DEFAULT_CONFIG_ENV} (or --config) at it."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def ssh_connect(host_info: dict, timeout: int = 30) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs = dict(
        hostname=host_info["host"],
        username=host_info["user"],
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    if host_info.get("key_filename"):
        connect_kwargs["key_filename"] = host_info["key_filename"]
    elif host_info.get("password"):
        connect_kwargs["password"] = host_info["password"]
    client.connect(**connect_kwargs)
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 60) -> str:
    _, out, err = client.exec_command(command, timeout=timeout)
    return (out.read().decode("utf-8", "replace") + err.read().decode("utf-8", "replace")).strip()


def supabase_get(path: str) -> list:
    url = os.environ["KTP_SUPABASE_URL"].rstrip("/") + path
    key = os.environ["KTP_SUPABASE_ANON_KEY"]
    req = urllib.request.Request(
        url,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept-Profile": "ktp",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_required_maps(known_unbuilt: set[str]) -> tuple[set[str], dict[str, str]]:
    """Returns (required bsp stems, {stem: reason}) live from Supabase."""
    reasons: dict[str, str] = {}

    veto_rows = supabase_get("/rest/v1/veto_map?select=map_name,active&active=eq.true")
    for row in veto_rows:
        stem = f"dod_{row['map_name']}"
        reasons.setdefault(stem, "active veto pool")

    season_rows = supabase_get("/rest/v1/season?select=id&state=eq.live")
    if not season_rows:
        raise SystemExit("No season with state=live — cannot resolve the current week schedule.")
    season_id = season_rows[0]["id"]

    week_rows = supabase_get(
        f"/rest/v1/week?select=map,week_number,kind&season_id=eq.{season_id}"
    )
    for row in week_rows:
        m = row.get("map")
        if not m:
            continue  # playoff/bye weeks are NULL by contract — the veto decides them
        stem = m.lower()
        if stem in reasons:
            reasons[stem] += f", week {row['week_number']}"
        else:
            reasons[stem] = f"week {row['week_number']}"

    required = set(reasons)
    unbuilt_present = required & known_unbuilt
    for stem in unbuilt_present:
        reasons[stem] += " (known unbuilt - reported, not escalated)"

    return required, reasons


def check_fastdl(stem: str) -> dict:
    def head_code(url: str) -> int:
        req = urllib.request.Request(url, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return -1

    bsp = head_code(f"{FASTDL_BASE}/{stem}.bsp")
    res = head_code(f"{FASTDL_BASE}/{stem}.res")
    return {"bsp": bsp, "res": res}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", help="Path to audit-fleet.json (see .example)")
    ap.add_argument(
        "--known-unbuilt",
        default="",
        help="Comma-separated dod_ map stems that are a scheduled week's map but "
        "not yet built anywhere — reported, never escalated (e.g. an S10-week "
        "map still pending art/build). Empty by default.",
    )
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a report")
    args = ap.parse_args()

    for var in ("KTP_SUPABASE_URL", "KTP_SUPABASE_ANON_KEY"):
        if not os.environ.get(var):
            raise SystemExit(f"{var} is not set — see the module docstring.")

    known_unbuilt = {s.strip() for s in args.known_unbuilt.split(",") if s.strip()}

    required, reasons = get_required_maps(known_unbuilt)
    check_maps = sorted(required | {DOD_POSITIVE_CONTROL_MAP})

    fleet = load_fleet_config(args.config)
    hosts = list(fleet["hosts"])

    presence: dict[str, dict[str, bool]] = {m: {} for m in check_maps}
    errors: list[str] = []

    for host_info in hosts:
        name = host_info["name"]
        ports = host_info.get("ports") or [host_info.get("sample_port", 27015)]
        try:
            client = ssh_connect(host_info)
        except Exception as e:
            errors.append(f"{name}: SSH connect failed: {e}")
            for port in ports:
                for m in check_maps:
                    presence[m][f"{name}:{port}"] = False
            continue
        for port in ports:
            out = run(client, f"ls ~/dod-{port}/serverfiles/dod/maps 2>/dev/null")
            present = {f[:-4] for f in out.splitlines() if f.endswith(".bsp")}
            for m in check_maps:
                presence[m][f"{name}:{port}"] = m in present
        client.close()

    instances = [f"{h['name']}:{p}" for h in hosts for p in (h.get("ports") or [h.get("sample_port", 27015)])]

    control_count = sum(1 for i in instances if presence[DOD_POSITIVE_CONTROL_MAP].get(i))
    control_ok = control_count == len(instances)

    fastdl_results = {m: check_fastdl(m) for m in required}
    bogus = check_fastdl(FASTDL_BOGUS_CONTROL)
    fastdl_control_ok = bogus["bsp"] == 404

    findings = []
    for m in sorted(required):
        fleet_count = sum(1 for i in instances if presence[m].get(i))
        fleet_missing = [i for i in instances if not presence[m].get(i)]
        fd = fastdl_results[m]
        is_known_unbuilt = m in known_unbuilt
        ok = fleet_count == len(instances) and fd["bsp"] == 200
        findings.append(
            {
                "map": m,
                "reason": reasons.get(m, ""),
                "fleet_count": fleet_count,
                "fleet_total": len(instances),
                "fleet_missing": fleet_missing,
                "fastdl_bsp": fd["bsp"],
                "fastdl_res": fd["res"],
                "known_unbuilt": is_known_unbuilt,
                "ok": ok or is_known_unbuilt,
            }
        )

    result = {
        "season_maps_checked": sorted(required),
        "positive_control": {"map": DOD_POSITIVE_CONTROL_MAP, "count": control_count, "total": len(instances), "ok": control_ok},
        "fastdl_bogus_control": {"map": FASTDL_BOGUS_CONTROL, "code": bogus["bsp"], "ok": fastdl_control_ok},
        "findings": findings,
        "ssh_errors": errors,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Positive control {DOD_POSITIVE_CONTROL_MAP}: {control_count}/{len(instances)} "
              f"({'OK' if control_ok else 'FAILED — sweep is reading the wrong directory, distrust everything below'})")
        print(f"FastDL bogus control {FASTDL_BOGUS_CONTROL}.bsp: {bogus['bsp']} "
              f"({'OK' if fastdl_control_ok else 'FAILED — expected 404'})")
        print()
        for f in findings:
            status = "OK" if f["ok"] else "MISSING"
            note = " [known unbuilt]" if f["known_unbuilt"] else ""
            print(f"  [{status}]{note} {f['map']} ({f['reason']}): "
                  f"fleet {f['fleet_count']}/{f['fleet_total']}, FastDL bsp={f['fastdl_bsp']} res={f['fastdl_res']}")
            if f["fleet_missing"] and not f["known_unbuilt"]:
                print(f"      missing on: {', '.join(f['fleet_missing'])}")
        if errors:
            print("\nSSH errors:")
            for e in errors:
                print(f"  {e}")

    bad = [
        f for f in findings
        if not f["ok"]
    ]
    if not control_ok or not fastdl_control_ok:
        bad = findings  # controls failed — nothing below is trustworthy
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
