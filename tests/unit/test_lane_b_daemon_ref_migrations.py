"""Lane B applies the migrations the KTPHLStatsX ref under test carries.

A `main`-based daemon ref predates migrations that exist only on `preprod`, and
Lane B used to demand all of them, so every such PR failed at the build step.
These tests use only `ArtifactSet.collect`, the entry point the workflow calls.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.e2e_stats.artifacts import (  # noqa: E402
    ArtifactSet,
    BuildError,
    DEFAULT_SCHEMA_FILES,
)

SEEDS = ("sql/migrate_003_assist_action.sql", "sql/migrate_004_cap_break_action.sql")
NEVER_APPLIED = ("sql/migrate_002_half_damage_score.sql", "sql/migrate_026_match_reports.sql")
LIST_FILE = "schema-migrations.txt"


def _is_migration(rel: str) -> bool:
    return Path(rel).name.startswith("migrate_")


def _ordinal(rel: str) -> int:
    return int(Path(rel).name.split("_")[1])


PREPROD_SHAPE = tuple(sorted({*DEFAULT_SCHEMA_FILES, *SEEDS, *NEVER_APPLIED}))
# KTPHLStatsX main stops at 027.
MAIN_SHAPE = tuple(rel for rel in PREPROD_SHAPE
                   if not _is_migration(rel) or _ordinal(rel) <= 27)
NEWER_THAN_MAIN = tuple(rel for rel in DEFAULT_SCHEMA_FILES
                        if _is_migration(rel) and _ordinal(rel) > 27)


def _daemon(root: Path, files: tuple[str, ...]) -> Path:
    repo = root / "KTPHLStatsX"
    (repo / "scripts").mkdir(parents=True)
    (repo / "sql").mkdir()
    (repo / "scripts" / "hlstats.pl").write_text("#!/usr/bin/perl\n")
    for rel in files:
        (repo / rel).write_text(f"-- {rel}\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "daemon"], check=True)
    return repo


def _collect(tmp_path: Path, repo: Path) -> ArtifactSet:
    return ArtifactSet.collect(
        tmp_path / "out",
        amxx_repo=tmp_path / "unused", amxx_ref="unused",
        daemon_repo=repo, daemon_ref="HEAD",
        include_plugin=False,
    )


def _applied(arts: ArtifactSet) -> list[str]:
    return [f"sql/{p.name}" for p in arts.schema_sql]


def _listed(arts: ArtifactSet) -> list[Path]:
    text = (arts.build_dir / LIST_FILE).read_text(encoding="utf-8")
    return [Path(line) for line in text.splitlines()]


def test_the_shapes_differ_where_it_matters():
    assert NEWER_THAN_MAIN
    assert not set(NEWER_THAN_MAIN) & set(MAIN_SHAPE)
    assert "sql/migrate_027_shot_events.sql" in MAIN_SHAPE


def test_a_main_based_ref_applies_what_it_carries_and_names_each_skip(tmp_path, capsys):
    arts = _collect(tmp_path, _daemon(tmp_path, MAIN_SHAPE))
    out = capsys.readouterr().out

    assert _applied(arts) == [rel for rel in DEFAULT_SCHEMA_FILES if rel in MAIN_SHAPE]
    assert [p.name for p in _listed(arts)][-1] == "migrate_027_shot_events.sql"
    for rel in NEWER_THAN_MAIN:
        assert re.search(rf"skipped {re.escape(rel)}\b", out), out
    assert arts.provenance["daemon"]["schema_skipped"] == list(NEWER_THAN_MAIN)


def test_a_ref_carrying_every_migration_applies_all_of_them(tmp_path, capsys):
    arts = _collect(tmp_path, _daemon(tmp_path, PREPROD_SHAPE))

    assert _applied(arts) == list(DEFAULT_SCHEMA_FILES)
    assert [p.name for p in _listed(arts)] == [
        Path(rel).name for rel in DEFAULT_SCHEMA_FILES if _is_migration(rel)]
    assert "skipped" not in capsys.readouterr().out
    assert arts.provenance["daemon"]["schema_skipped"] == []


def test_the_list_names_files_that_were_extracted(tmp_path):
    arts = _collect(tmp_path, _daemon(tmp_path, MAIN_SHAPE))
    listed = _listed(arts)
    assert listed
    assert all(path.is_file() for path in listed)


def test_a_migration_the_harness_cannot_order_fails_the_build(tmp_path):
    repo = _daemon(tmp_path, (*PREPROD_SHAPE, "sql/migrate_032_not_yet_registered.sql"))
    with pytest.raises(BuildError, match="migrate_032_not_yet_registered"):
        _collect(tmp_path, repo)


def test_a_gap_fails_instead_of_being_skipped(tmp_path):
    repo = _daemon(tmp_path, tuple(
        rel for rel in PREPROD_SHAPE if rel != "sql/migrate_028_shot_events_dedup.sql"))
    with pytest.raises(BuildError, match="migrate_029_shot_target_state"):
        _collect(tmp_path, repo)


@pytest.mark.parametrize("missing", ["sql/ktp_schema.sql", *SEEDS])
def test_the_base_schema_and_seeds_are_never_skipped(tmp_path, missing):
    repo = _daemon(tmp_path, tuple(rel for rel in MAIN_SHAPE if rel != missing))
    with pytest.raises(BuildError, match=re.escape(missing)):
        _collect(tmp_path, repo)


def test_migrations_marked_never_applied_are_not_applied(tmp_path):
    arts = _collect(tmp_path, _daemon(tmp_path, PREPROD_SHAPE))
    extracted = {p.name for p in arts.schema_sql + arts.seed_sql}
    assert not extracted & {Path(rel).name for rel in NEVER_APPLIED}
