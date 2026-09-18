"""The demo importer writing as the hltv-demo producer, against a real ledger.

Nothing had executed this path before the 2026-09-18 backfill (unit tests
compare SQL text; --sql-out never connects), and it took eight fixes
(KTPInfrastructure#441-#448). This is the case that would have caught them.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts import import_demo_team_score as cli
from scripts import match_analytics as ma
from scripts.fit_team_score_labels import LEDGER_FINALS_SQL, half_winners
from tests.e2e_stats.ephemeral_mysql import EphemeralMysql
from tests.e2e_stats.test_team_score_import_integration import (
    OBSERVATIONS, MANIFESTS, SOURCE_SERVER, event, load_ledger, prepare_match, write_events)
from scripts import team_score_telemetry as score

FIXTURES = Path(__file__).parents[1] / "fixtures" / "demo_team_score"
NY1 = ("ktp_1789326428-NY1_h1-2609131459-dod_thunder2.dem",
       "ktp_1789326428-NY1_h2-2609131529-dod_thunder2.dem")


def _demos(root: Path, names) -> None:
    for name in names:
        p = root / "NY1" / "ktp" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"not a demo")


def _run(db: EphemeralMysql, root: Path, monkeypatch) -> int:
    monkeypatch.setattr(cli, "run_dod_tools",
                        lambda _cli, demo: json.loads((FIXTURES / f"{demo.name}.json").read_text(encoding="utf-8")))
    return cli.main(["--dod-tools", str(root / "fake-cli"), "--demos-root", str(root), "--types", "ktp",
                     "--apply", "--migrate", "--database", db.database, "--mysql-bin", db.client,
                     "--socket", str(db.socket_path), "--user", "root"])


def test_apply_writes_as_hltv_demo_and_is_idempotent(tmp_path, monkeypatch):
    with EphemeralMysql.start(parent=tmp_path) as db:
        _demos(tmp_path, NY1)
        assert _run(db, tmp_path, monkeypatch) == 0
        assert db.count(f"SELECT COUNT(*) FROM {OBSERVATIONS} WHERE BINARY producer='hltv-demo'") == 4
        assert db.count(f"SELECT COUNT(*) FROM {MANIFESTS} WHERE BINARY producer='hltv-demo'") == 1
        assert db.count(f"SELECT COUNT(*) FROM {OBSERVATIONS}") == 4
        # Same demos again: nothing new, nothing rejected.
        assert _run(db, tmp_path, monkeypatch) == 0
        assert db.count(f"SELECT COUNT(*) FROM {OBSERVATIONS}") == 4
        assert db.count(f"SELECT COUNT(*) FROM {MANIFESTS}") == 1
        # The fit driver's label query reads the same rows back: slot 1 won
        # both halves (142-12 as Allies, then 131-13 as Axis).
        finals = ma.tsv_rows(db.sql(LEDGER_FINALS_SQL.format(types="0, 4")))
        assert half_winners(finals) == {"1789326428-NY1": {1: 1, 2: 2}}


def test_apply_skips_a_match_the_hud_producer_already_owns(tmp_path, monkeypatch):
    with EphemeralMysql.start(parent=tmp_path) as db:
        load_ledger(db)
        prepare_match(db, "1789326428-NY1")
        mid = "1789326428-NY1"
        hud = [event(match_id=mid), event(tick=1200.75, sequence=2, allies=3, kind="final", match_id=mid),
               event(half=2, tick=0.1, axis=3, allies_team=2, axis_team=1, match_id=mid),
               event(half=2, tick=1200.2, sequence=2, allies=2, axis=3, allies_team=2, axis_team=1,
                     kind="final", match_id=mid)]
        path = write_events(tmp_path / "hud", hud, match_id=mid)
        mysql = score.MysqlCli(mysql_bin=db.client, database=db.database, socket=db.socket_path, user="root")
        mysql.import_observations(score.read_event_files(
            [path], source_server_roots={SOURCE_SERVER: path.parent.parent}))
        assert db.count(f"SELECT COUNT(*) FROM {OBSERVATIONS} WHERE BINARY producer='KTPHudObserver'") == 4
        _demos(tmp_path, NY1)
        assert _run(db, tmp_path, monkeypatch) == 0
        assert db.count(f"SELECT COUNT(*) FROM {OBSERVATIONS} WHERE BINARY producer='hltv-demo'") == 0
        assert db.count(f"SELECT COUNT(*) FROM {MANIFESTS}") == 1
