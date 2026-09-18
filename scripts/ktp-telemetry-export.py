#!/usr/bin/env python3
"""Push the daily per-server performance rollup from the data server to the site.

`ktp_telemetry_baselines` is written here, on the data server, by
`ktp-perf-rollup` (cron `ktp-perf-rollup-daily`, 04:30 ET). Any web surface that
wants to draw a trend line needs those rows, and every candidate surface now
lives somewhere that cannot read them.

Push rather than pull, and not by preference: MySQL here binds 127.0.0.1, so a
site-side pull would need a tunnel from a serverless function into a production
game database. The same reasoning already governs `ktp-stats-export.py`, and the
site states it at the receiving end.

This script stays dumb on purpose -- it selects a window, sorts it, and POSTs it.
It does not group by server, compute a sparkline, decide what "recent" means, or
know which page renders the result. That is what makes it independent of where
the panel eventually lands: shaping belongs to whoever draws it, and a shaping
decision baked in here would have to be re-cut when that is settled.

    ktp-telemetry-export.py [--days N] [--dry-run] [--quiet]

Config: /etc/ktp/telemetry-export.conf (mode 600), or the environment.
    TELEMETRY_INGEST_URL     the site's internal telemetry endpoint
    TELEMETRY_INGEST_SECRET  shared secret, matches the site env of the same name
    TELEMETRY_DB_USER        read-only MySQL user (default: support_web)
    TELEMETRY_DB_PASS        omit for a socket-authenticated user

⚠️ The receiving endpoint does not exist yet. Deploy this, but do not enable the
timer until it does: the unit alerts on failure by design, so an enabled timer
against a missing route pages someone every hour to report a 404.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONF = "/etc/ktp/telemetry-export.conf"
DB_NAME = "hlstatsx"
DEFAULT_DB_USER = "support_web"

# Matches the window the rollup's own consumers use. Wider is free to ask for on
# the command line; wider by default would ship months of history every hour to
# draw a month of it.
DEFAULT_DAYS = 30


class Db:
    """Thin wrapper over the `mysql` CLI, returning JSON.

    No python MySQL driver is installed system-wide on this host -- the one that
    exists belongs to the aggregator's venv, and depending on another service's
    virtualenv to read six columns would couple this script's survival to that
    service's next rebuild. `ktp-stats-export.py` shells out for the same reason.
    """

    def __init__(self, user: str, password: str, db: str = DB_NAME):
        self.args = ["mysql", "--default-character-set=utf8mb4", "--batch",
                     "--raw", "-N", "-u", user]
        if password:
            self.args.append(f"-p{password}")
        self.args.append(db)

    def scalar(self, sql: str) -> str | None:
        out = self._run(sql)
        return None if out in ("", "NULL") else out

    def json_rows(self, sql: str) -> list:
        body = self._run(sql)
        # JSON_ARRAYAGG over an empty set is NULL, which prints as the literal
        # "NULL" -- not an error and not valid JSON.
        if not body or body == "NULL":
            return []
        return json.loads(body)

    def _run(self, sql: str) -> str:
        out = subprocess.run(self.args + ["-e", sql], capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
        if out.returncode != 0:
            raise SystemExit(f"mysql failed: {out.stderr.strip()[:400]}")
        return out.stdout.strip()


def log(msg: str, *, quiet: bool = False) -> None:
    if not quiet:
        print(msg, flush=True)


def load_config() -> dict[str, str]:
    """Environment wins over the file, so a one-off run can override."""
    conf: dict[str, str] = {}
    if os.path.exists(CONF):
        with open(CONF, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                conf[k.strip()] = v.strip().strip('"').strip("'")
    for key in ("TELEMETRY_INGEST_URL", "TELEMETRY_INGEST_SECRET",
                "TELEMETRY_DB_USER", "TELEMETRY_DB_PASS"):
        if os.environ.get(key):
            conf[key] = os.environ[key]
    return conf


def fetch_rows(db: Db, days: int) -> list[dict]:
    """The rollup's window, one entry per server per day.

    MySQL builds the JSON rather than this parsing tab-separated columns, so a
    float that renders in scientific notation or a NULL cannot be mistaken for a
    value by a delimiter split.

    `warn_fps` and `warn_spikes` are TINYINT, so they arrive as 0/1 and not as
    JSON booleans. Left as they are: coercing here would be the first shaping
    decision, and the reader has to handle a missing key anyway.
    """
    sql = f"""
        SELECT JSON_ARRAYAGG(JSON_OBJECT(
                 'endpoint', server_endpoint,
                 'day', DATE_FORMAT(day, '%Y-%m-%d'),
                 'fps_p50', fps_p50_today,
                 'spike_total', spike_total_today,
                 'warn_fps', warn_fps,
                 'warn_spikes', warn_spikes))
        FROM ktp_telemetry_baselines
        WHERE day >= CURDATE() - INTERVAL {int(days)} DAY
    """
    rows = db.json_rows(sql)
    # JSON_ARRAYAGG does not promise an order and ORDER BY inside it is not
    # honoured, so sort here. An unordered payload would still render, which is
    # what makes this worth doing rather than leaving to the reader.
    rows.sort(key=lambda r: (str(r.get("endpoint") or ""), str(r.get("day") or "")))
    return rows


def fetch_source_stamp(db: Db, days: int) -> int | None:
    """When the rollup last WROTE, as distinct from when this last PUSHED.

    🔑 Without this the two failures are indistinguishable. This script refreshes
    the document's `generated` stamp every run, so if the rollup cron dies the
    site keeps receiving a punctually-delivered, ever-fresh envelope wrapped around
    rows that stopped moving -- and a freshness check on the envelope reports
    healthy. The reader judges the CONTENT by this.
    """
    raw = db.scalar(
        "SELECT UNIX_TIMESTAMP(MAX(computed_at)) FROM ktp_telemetry_baselines "
        f"WHERE day >= CURDATE() - INTERVAL {int(days)} DAY")
    try:
        return int(float(raw)) if raw else None
    except ValueError:
        return None


def build(rows: list[dict], source_stamp: int | None, days: int) -> dict:
    return {
        "generated": int(time.time()),
        "window_days": days,
        "source_computed_at": source_stamp,
        "rows": rows,
    }


def post(url: str, secret: str, payload: dict, quiet: bool) -> bool:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json",
                 "x-internal-telemetry": secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            log(f"  -> {resp.status} {resp.read(400).decode('utf-8', 'replace')}",
                quiet=quiet)
            return True
    except urllib.error.HTTPError as exc:
        # Print the body. A bare status against an unattended exporter is
        # indistinguishable from the site being down.
        detail = exc.read(800).decode("utf-8", "replace")
        print(f"  !! HTTP {exc.code}: {detail}", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"  !! {exc}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--dry-run", action="store_true",
                    help="build and print the payload, POST nothing")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2

    conf = load_config()
    db = Db(conf.get("TELEMETRY_DB_USER", DEFAULT_DB_USER),
            conf.get("TELEMETRY_DB_PASS", ""))

    rows = fetch_rows(db, args.days)
    payload = build(rows, fetch_source_stamp(db, args.days), args.days)

    # An empty window is a failure, not an empty fleet: the rollup writes every
    # server every day, so nothing to send means the source stopped. Exiting 0
    # here would push an empty document and let the panel render a blank fleet
    # as if it had measured one.
    if not rows:
        print(f"no rows in the last {args.days} days -- is ktp-perf-rollup running?",
              file=sys.stderr)
        return 1

    log(f"{len(rows)} rows, {len({r.get('endpoint') for r in rows})} endpoints",
        quiet=args.quiet)

    if args.dry_run:
        print(json.dumps(payload, indent=1, sort_keys=True))
        return 0

    url, secret = conf.get("TELEMETRY_INGEST_URL"), conf.get("TELEMETRY_INGEST_SECRET")
    if not url or not secret:
        print(f"TELEMETRY_INGEST_URL and TELEMETRY_INGEST_SECRET must be set "
              f"(env or {CONF})", file=sys.stderr)
        return 2

    return 0 if post(url, secret, payload, args.quiet) else 1


if __name__ == "__main__":
    sys.exit(main())
