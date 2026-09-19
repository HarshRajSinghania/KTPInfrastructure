"""Pull the raw event stream the momentum ledger needs, from the data server.

Officials (match_type 0) and 12mans (match_type 1) that have flag events.
S9 officials predate flag capture, so as of 2026-09-19 that is 9 officials +
123 twelve-mans; each S10 week adds its officials. Rerun before each fit.

Reads over ssh with the operator's read-only grants (mysql via auth_socket
inside the session -- no tunnel, no credential on this side). Writes one TSV
per table into data/events/, which is gitignored: rows carry player_id.

Usage:
    python momentum_fetch.py            # -> data/events/*.tsv
"""
from __future__ import annotations

import subprocess
from pathlib import Path

HOST = "krodssh@api.ktpdod.com"
OUT = Path(__file__).resolve().parent / "data" / "events"

# Matches in scope: the ledger needs flag state, so a match without it is
# useless whatever its type.
SCOPE = ("(SELECT DISTINCT m.match_id FROM ktp_matches m "
         "JOIN ktp_flag_state_events f ON f.match_id=m.match_id "
         "WHERE m.match_type IN (0,1))")

QUERIES = {
    "matches": ("SELECT match_id, map_name, half, match_type, start_time, end_time "
                f"FROM ktp_matches WHERE match_id IN {SCOPE} ORDER BY start_time, half"),
    "frags": ("SELECT match_id, half, game_time, killerId, victimId, weapon "
              f"FROM hlstats_Events_Frags WHERE match_id IN {SCOPE} AND game_time IS NOT NULL "
              "ORDER BY match_id, half, game_time"),
    "flags": ("SELECT match_id, half, flag_index, flag_name, owner_team, is_initial, game_time, "
              f"event_time FROM ktp_flag_state_events WHERE match_id IN {SCOPE} "
              "ORDER BY match_id, half, game_time, id"),
    "captures": ("SELECT match_id, half, player_id, team, flag_name, event_time "
                 f"FROM ktp_flag_captures WHERE match_id IN {SCOPE} ORDER BY match_id, half, event_time"),
    "players": ("SELECT match_id, player_id, player_name, steam_id "
                f"FROM ktp_match_players WHERE match_id IN {SCOPE}"),
    "lives": ("SELECT match_id, half, player_id, boundary_kind, reason, team, round_live, game_time "
              f"FROM ktp_life_events WHERE match_id IN {SCOPE} ORDER BY match_id, half, game_time"),
}


def fetch(name, sql):
    # -N drops the header; we prepend our own so the TSV is self-describing.
    cols = sql.split("SELECT ", 1)[1].split(" FROM ", 1)[0]
    header = "\t".join(c.strip() for c in cols.split(","))
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", HOST, f"mysql hlstatsx -N -e \"{sql}\""],
                         capture_output=True, text=True, check=True, timeout=600).stdout
    (OUT / f"{name}.tsv").write_text(header + "\n" + out, encoding="utf-8")
    return out.count("\n")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, sql in QUERIES.items():
        print(f"{name:10s} {fetch(name, sql):>8d} rows")


if __name__ == "__main__":
    main()
