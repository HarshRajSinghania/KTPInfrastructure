"""changelog.d fragments: the merge that used to cascade, and a deterministic assembler.

Two legs matter here. `test_fragments_merge_clean` merges two branches that each
add their own fragment; `test_shared_offset_still_conflicts` is its control —
the same throwaway repo, the same two branches, both appending under
`## [Unreleased]` instead — and it reproduces the 2026-09-14 conflict. Without
the control the first test is a check that passes for everything.

The control repos carry no `.gitattributes`, deliberately: `merge=union` masks
the conflict locally, which is exactly why it was mistaken for a fix.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "assemble_changelog", _ROOT / "scripts" / "assemble_changelog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["assemble_changelog"] = mod
    spec.loader.exec_module(mod)
    return mod


ac = _load()

FRAGMENT_A = "### `a`: the first change (2026-01-02)\n\nBody of A.\n"
FRAGMENT_B = "### `b`: the second change (2026-01-03)\n\nBody of B.\n"

BASE_CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n\n### seed\n"


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "core.autocrlf=false", *args],
        capture_output=True,
        text=True,
        env=_git_env(),
    )


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    (repo / "CHANGELOG.md").write_text(BASE_CHANGELOG, encoding="utf-8", newline="\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-qm", "base").returncode == 0


def _branch_commit(repo: Path, branch: str, write) -> None:
    assert _git(repo, "checkout", "-q", "-b", branch, "main").returncode == 0
    write()
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-qm", branch).returncode == 0


def _append_under_unreleased(repo: Path, text: str) -> None:
    path = repo / "CHANGELOG.md"
    old = path.read_text(encoding="utf-8")
    head, _, tail = old.partition("## [Unreleased]\n")
    path.write_text(head + "## [Unreleased]\n\n" + text + tail, encoding="utf-8", newline="\n")


def _write_fragment(directory: Path, name: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# --- the merge behaviour, and its control -----------------------------------


def test_fragments_merge_clean(tmp_path: Path) -> None:
    repo = tmp_path / "frag"
    _init(repo)
    _branch_commit(
        repo, "pr-a", lambda: _write_fragment(repo / "changelog.d", "2026-01-02-a.md", FRAGMENT_A)
    )
    _branch_commit(
        repo, "pr-b", lambda: _write_fragment(repo / "changelog.d", "2026-01-03-b.md", FRAGMENT_B)
    )

    assert _git(repo, "checkout", "-q", "pr-a").returncode == 0
    merge = _git(repo, "merge", "--no-edit", "pr-b")

    assert merge.returncode == 0, merge.stdout + merge.stderr
    assert (repo / "changelog.d" / "2026-01-02-a.md").exists()
    assert (repo / "changelog.d" / "2026-01-03-b.md").exists()
    assert "<<<<<<<" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


def test_shared_offset_still_conflicts(tmp_path: Path) -> None:
    """The defect, reproduced. If this ever passes, the hazard is gone and the
    test above has stopped proving anything."""
    repo = tmp_path / "offset"
    _init(repo)
    _branch_commit(repo, "pr-a", lambda: _append_under_unreleased(repo, FRAGMENT_A + "\n"))
    _branch_commit(repo, "pr-b", lambda: _append_under_unreleased(repo, FRAGMENT_B + "\n"))

    assert _git(repo, "checkout", "-q", "pr-a").returncode == 0
    merge = _git(repo, "merge", "--no-edit", "pr-b")

    assert merge.returncode != 0
    assert "<<<<<<<" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


# --- determinism -------------------------------------------------------------


def test_assemble_is_byte_identical_across_runs(tmp_path: Path) -> None:
    a = tmp_path / "one" / "changelog.d"
    b = tmp_path / "two" / "changelog.d"
    # Written in opposite order so directory order and mtime disagree between
    # the two trees; only the filename sort may decide the output.
    _write_fragment(a, "2026-01-02-a.md", FRAGMENT_A)
    _write_fragment(a, "2026-01-03-b.md", FRAGMENT_B)
    _write_fragment(b, "2026-01-03-b.md", FRAGMENT_B)
    _write_fragment(b, "2026-01-02-a.md", FRAGMENT_A)

    first = ac.assemble(a).encode("utf-8")
    second = ac.assemble(b).encode("utf-8")

    assert first == second
    assert ac.assemble(a).encode("utf-8") == first
    assert b"\r" not in first
    assert first.endswith(b"\n") and not first.endswith(b"\n\n")


def test_assemble_orders_newest_first_by_name(tmp_path: Path) -> None:
    frag = tmp_path / "changelog.d"
    _write_fragment(frag, "2026-01-02-a.md", FRAGMENT_A)
    _write_fragment(frag, "2026-01-03-b.md", FRAGMENT_B)

    body = ac.assemble(frag)

    assert body.index("the second change") < body.index("the first change")


def test_crlf_fragment_assembles_to_lf(tmp_path: Path) -> None:
    frag = tmp_path / "changelog.d"
    path = frag / "2026-01-02-a.md"
    frag.mkdir(parents=True)
    path.write_bytes(FRAGMENT_A.replace("\n", "\r\n").encode("utf-8"))

    assert "\r" not in ac.assemble(frag)


def test_assemble_ignores_the_readme(tmp_path: Path) -> None:
    frag = tmp_path / "changelog.d"
    _write_fragment(frag, "README.md", "# how to\n")
    _write_fragment(frag, "2026-01-02-a.md", FRAGMENT_A)

    assert "how to" not in ac.assemble(frag)


def test_empty_directory_assembles_to_nothing(tmp_path: Path) -> None:
    assert ac.assemble(tmp_path / "changelog.d") == ""


# --- validation --------------------------------------------------------------


def test_validate_accepts_a_well_formed_fragment(tmp_path: Path) -> None:
    path = _write_fragment(tmp_path, "2026-01-02-a-slug.md", FRAGMENT_A)
    assert ac.validate_fragment(path) == []


@pytest.mark.parametrize(
    "name,text",
    [
        ("a.md", FRAGMENT_A),
        ("2026-1-2-a.md", FRAGMENT_A),
        ("2026-01-02-Mixed-Case.md", FRAGMENT_A),
        ("2026-01-02-a.md", "## [1.0.0]\n\nbody\n"),
        ("2026-01-02-a.md", "Just prose, no heading.\n"),
        ("2026-01-02-a.md", "\n\n"),
    ],
)
def test_validate_rejects(tmp_path: Path, name: str, text: str) -> None:
    path = _write_fragment(tmp_path / name.replace(".md", ""), name, text)
    assert ac.validate_fragment(path) != []


# --- the release cut ---------------------------------------------------------


def _release_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "changelog.d").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        + ac.normalize(ac.STUB)
        + "\n\n## [1.0.0] - 2026-01-01\n\n### seed\n",
        encoding="utf-8",
        newline="\n",
    )
    return root


def test_release_folds_fragments_and_removes_them(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    _write_fragment(root / "changelog.d", "2026-01-02-a.md", FRAGMENT_A)
    _write_fragment(root / "changelog.d", "2026-01-03-b.md", FRAGMENT_B)

    rc = ac.main(["--root", str(root), "release", "--version", "1.1.0", "--date", "2026-01-04"])

    assert rc == 0
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.1.0] - 2026-01-04" in text
    assert text.index("## [1.1.0]") < text.index("## [1.0.0]")
    assert "the first change" in text and "the second change" in text
    assert ac.normalize(ac.STUB) in text
    assert list((root / "changelog.d").iterdir()) == []
    assert b"\r" not in (root / "CHANGELOG.md").read_bytes()


def test_release_is_byte_identical_for_the_same_fragments(tmp_path: Path) -> None:
    outputs = []
    for run in ("x", "y"):
        root = _release_repo(tmp_path / run)
        _write_fragment(root / "changelog.d", "2026-01-03-b.md", FRAGMENT_B)
        _write_fragment(root / "changelog.d", "2026-01-02-a.md", FRAGMENT_A)
        ac.main(["--root", str(root), "release", "--version", "1.1.0", "--date", "2026-01-04"])
        outputs.append((root / "CHANGELOG.md").read_bytes())

    assert outputs[0] == outputs[1]


def test_release_refuses_a_malformed_fragment(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    _write_fragment(root / "changelog.d", "nope.md", FRAGMENT_A)

    assert ac.main(["--root", str(root), "release", "--version", "1.1.0"]) == 1
    assert "## [1.1.0]" not in (root / "CHANGELOG.md").read_text(encoding="utf-8")


def test_release_refuses_an_empty_directory(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    with pytest.raises(SystemExit):
        ac.main(["--root", str(root), "release", "--version", "1.1.0"])


# --- the stub gate -----------------------------------------------------------


def test_check_passes_on_the_stub(tmp_path: Path) -> None:
    root = _release_repo(tmp_path)
    _write_fragment(root / "changelog.d", "2026-01-02-a.md", FRAGMENT_A)

    assert ac.main(["--root", str(root), "check"]) == 0


def test_check_fails_when_an_entry_is_added_under_unreleased(tmp_path: Path) -> None:
    """The habit returning is the failure mode this gate exists for."""
    root = _release_repo(tmp_path)
    path = root / "CHANGELOG.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "## [Unreleased]\n\n", "## [Unreleased]\n\n" + FRAGMENT_A + "\n"
        ),
        encoding="utf-8",
        newline="\n",
    )

    assert ac.main(["--root", str(root), "check"]) == 1


# --- the repo's own tree -----------------------------------------------------


def test_this_repo_passes_check() -> None:
    assert ac.main(["--root", str(_ROOT), "check"]) == 0


def test_this_repo_has_fragments() -> None:
    assert ac.iter_fragments(ac.fragment_dir(_ROOT)), "changelog.d/ collected nothing"
