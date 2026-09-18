#!/usr/bin/env python3
"""Import half-winner labels from HLTV demos into the team-score ledger.

Runs dod-tools over each demo, derives per-half winners on stable roster slots,
and writes ktp_team_score_observations rows (plus the per-match ingest manifest
the ledger's foreign key requires) under producer 'hltv-demo'. Idempotent; never
touches a match owned by another producer.

    # one-off / inspection: print the SQL, touch nothing
    python3 -m scripts.import_demo_team_score --dod-tools /usr/local/bin/dod-tools-cli \\
        --demos /home/hltvserver/hlds/dod/demos/*/ktp/*.dem --sql-out backfill.sql

    # recurring (ktp-demo-publish.sh): official demos filed in the last 3 days
    python3 -m scripts.import_demo_team_score --dod-tools /usr/local/bin/dod-tools-cli \\
        --demos-root /home/hltvserver/hlds/dod/demos --types ktp --since-days 3 \\
        --database hlstatsx --defaults-extra-file /etc/ktp/team-score-import.cnf --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

try:  # direct script execution
    from demo_team_score import (DemoLabelError, labels_from_reports, parse_demo_name,
                                 run_dod_tools, sql_for_labels)
    from team_score_telemetry import MIGRATIONS, MysqlCli, MysqlCommandError
except ModuleNotFoundError:  # package import in tests/tooling
    from scripts.demo_team_score import (DemoLabelError, labels_from_reports, parse_demo_name,
                                         run_dod_tools, sql_for_labels)
    from scripts.team_score_telemetry import MIGRATIONS, MysqlCli, MysqlCommandError

LAN_DATABASE = "hlstatsx_lan"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dod-tools", type=Path, required=True, help="path to dod-tools-cli (build/dod-tools/fetch.sh)")
    src = ap.add_argument_group("demos")
    src.add_argument("--demos", nargs="*", type=Path, default=[], help="demo files")
    src.add_argument("--demos-root", type=Path, help="archive root laid out as <root>/<SERVER>/<type>/*.dem")
    src.add_argument("--types", default="ktp", help="comma-separated types under --demos-root (default: ktp = official)")
    src.add_argument("--since-days", type=float, help="with --demos-root: only files modified in the last N days")
    src.add_argument("--archive-prefix", default="demos/", help="prefix for source_path_sha256 (default: demos/)")
    out = ap.add_argument_group("output")
    out.add_argument("--sql-out", type=Path, help="write the import SQL here and do not connect")
    out.add_argument("--apply", action="store_true", help="execute against the database")
    out.add_argument("--migrate", action="store_true",
                     help="with --apply: first apply the ledger migrations (023 observations, 032 producer); "
                          "both are idempotent")
    db = ap.add_argument_group("mysql")
    db.add_argument("--mysql-bin", default="mysql")
    db.add_argument("--database", help=f"required with --apply (default {LAN_DATABASE} is the LAN schema)")
    db.add_argument("--defaults-extra-file", type=Path)
    db.add_argument("--socket", type=Path)
    db.add_argument("--host")
    db.add_argument("--port", type=int)
    db.add_argument("--user")
    return ap.parse_args(argv)


def collect_demos(args: argparse.Namespace) -> list[tuple[Path, str]]:
    """(file, archive path used for source_path_sha256)."""
    found: list[tuple[Path, str]] = []
    for f in args.demos:
        server = parse_demo_name(f.name).server
        found.append((f, f"{args.archive_prefix}{server}/{parse_demo_name(f.name).type}/{f.name}"))
    if args.demos_root:
        cutoff = time.time() - args.since_days * 86400 if args.since_days else None
        for kind in [t.strip() for t in args.types.split(",") if t.strip()]:
            for f in sorted(args.demos_root.glob(f"*/{kind}/*.dem")):
                if cutoff is not None and f.stat().st_mtime < cutoff:
                    continue
                rel = f.relative_to(args.demos_root).as_posix()
                found.append((f, f"{args.archive_prefix}{rel}"))
    # halves of a match must travel together; drop unparseable names loudly, not silently
    ok: list[tuple[Path, str]] = []
    for f, path in found:
        try:
            parse_demo_name(f.name)
        except DemoLabelError as exc:
            print(f"demo-team-score: skip {f.name}: {exc}", file=sys.stderr)
            continue
        ok.append((f, path))
    return ok


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply and args.database is None:
        print(f"demo-team-score: --apply needs an explicit --database (the default is the LAN schema {LAN_DATABASE})",
              file=sys.stderr)
        return 2
    if not args.apply and args.sql_out is None:
        print("demo-team-score: nothing to do — pass --sql-out to print SQL or --apply to import", file=sys.stderr)
        return 2
    demos = collect_demos(args)
    if not demos:
        print("demo-team-score: no demos matched", file=sys.stderr)
        return 0
    reports = []
    for f, archive_path in demos:
        try:
            rep = run_dod_tools(args.dod_tools, f)
        except Exception as exc:  # a corrupt demo must not stop the batch
            print(f"demo-team-score: dod-tools failed on {f.name}: {exc}", file=sys.stderr)
            continue
        reports.append((f.name, rep, hashlib.sha256(f.read_bytes()).digest(), archive_path))
    try:
        labels = labels_from_reports(reports)
    except DemoLabelError as exc:
        print(f"demo-team-score: {exc}", file=sys.stderr)
        return 2
    # a match with only one half cannot fix roster slots for the ledger; label_match raises — report which
    complete = [l for l in labels if l.halves and l.halves[0].demo.name.half == 1]
    sql, rows, manifests = sql_for_labels(complete)
    summary = {
        "demos": len(demos), "parsed": len(reports), "matches": len(complete),
        "observations": len(rows), "manifests": len(manifests),
        "labels": [{"match": l.match_id, "type": l.match_type, "map": l.map_name,
                    "totals": {str(k): v for k, v in l.totals.items()}, "winner_slot": l.winner_slot,
                    "halves": [{"half": h.demo.name.half, "half_score": {str(k): v for k, v in h.half_score.items()},
                                "winner_team": h.winner_team} for h in l.halves],
                    "side_swap_seen": l.side_swap_seen} for l in complete],
    }
    if args.sql_out:
        args.sql_out.write_text(sql, encoding="utf-8")
        summary["sql_out"] = str(args.sql_out)
    if args.apply:
        mysql = MysqlCli(mysql_bin=args.mysql_bin, database=args.database,
                         defaults_extra_file=args.defaults_extra_file, socket=args.socket,
                         host=args.host, port=args.port, user=args.user)
        try:
            if args.migrate:
                mysql.apply_migrations(MIGRATIONS)
            output = mysql.execute(sql)
        except MysqlCommandError as exc:
            print(f"demo-team-score: {exc}", file=sys.stderr)
            return 2
        result_line = next((l for l in output.splitlines() if l.startswith("KTP_DEMO_TEAM_SCORE_RESULT\t")), None)
        if result_line is None:
            print("demo-team-score: import result marker missing from MySQL output", file=sys.stderr)
            return 2
        cols = result_line.split("\t")
        if len(cols) != 5 or cols[4] != "1":
            print(f"demo-team-score: could not acquire the team-score ledger lock: {result_line}", file=sys.stderr)
            return 2
        summary["result"] = {"manifests": int(cols[1]), "observations": int(cols[2]),
                             "skipped_other_producer": int(cols[3])}
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
