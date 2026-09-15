#!/usr/bin/env python3
"""Assemble per-change CHANGELOG fragments into a release section.

Every PR used to append under `## [Unreleased]` at the same offset, so merging
one conflicted every sibling on that file and on nothing else. Fragments give
each change its own path; this tool is the only thing that writes CHANGELOG.md,
and it runs at release time on main.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT_DIRNAME = "changelog.d"
CHANGELOG_NAME = "CHANGELOG.md"
UNRELEASED_HEADING = "## [Unreleased]"

# Date prefix orders the release section; the slug is what makes the path
# unique, so two branches can never land on the same file.
FRAGMENT_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9.-]*\.md$")
RELEASE_HEADING = re.compile(r"^## \[", re.MULTILINE)
VERSION = re.compile(r"^\d+\.\d+\.\d+$")

STUB = """\
Unreleased entries are one file per change in [`changelog.d/`](changelog.d/);
see that directory's README for the convention. `python
scripts/assemble_changelog.py preview` renders them, and `release` folds them
into a numbered section below. Nothing is written here by hand — an entry added
under this heading is what made every open PR conflict on this file.
"""


def normalize(text: str) -> str:
    """LF, no leading or trailing blank lines. Fragment bytes must not depend
    on the checkout's line-ending settings."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def fragment_dir(root: Path = ROOT) -> Path:
    return root / FRAGMENT_DIRNAME


def iter_fragments(frag_dir: Path) -> list[Path]:
    """Newest first, matching the order entries already sit in within a release
    section. Sorted by name, never by mtime or directory order."""
    if not frag_dir.is_dir():
        return []
    return sorted(
        (p for p in frag_dir.iterdir() if p.is_file() and p.name != "README.md"),
        key=lambda p: p.name,
        reverse=True,
    )


def validate_fragment(path: Path) -> list[str]:
    errors: list[str] = []
    if not FRAGMENT_NAME.match(path.name):
        errors.append(
            f"{path.name}: name must be YYYY-MM-DD-<slug>.md (lowercase slug). "
            "The date orders the release section; the slug keeps the path unique."
        )
    body = normalize(path.read_text(encoding="utf-8"))
    if not body:
        errors.append(f"{path.name}: empty")
        return errors
    first = body.splitlines()[0]
    if not first.startswith("### "):
        errors.append(f"{path.name}: must open with a `### ` heading, got {first!r}")
    for line in body.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            errors.append(f"{path.name}: `## ` would forge a release boundary: {line!r}")
    return errors


def assemble(frag_dir: Path) -> str:
    """The body of a release section. Same fragments in, same bytes out."""
    bodies = [normalize(p.read_text(encoding="utf-8")) for p in iter_fragments(frag_dir)]
    bodies = [b for b in bodies if b]
    if not bodies:
        return ""
    return "\n\n".join(bodies) + "\n"


def split_unreleased(text: str) -> tuple[str, str, str]:
    """(head through the Unreleased heading, its body, the rest)."""
    lines = text.replace("\r\n", "\n").split("\n")
    try:
        start = lines.index(UNRELEASED_HEADING)
    except ValueError:
        raise SystemExit(f"{CHANGELOG_NAME}: no `{UNRELEASED_HEADING}` heading")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    head = "\n".join(lines[: start + 1]) + "\n"
    body = "\n".join(lines[start + 1 : end])
    rest = "\n".join(lines[end:])
    return head, body, rest


def cmd_preview(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    frag_dir = fragment_dir(root)
    errors = [e for p in iter_fragments(frag_dir) for e in validate_fragment(p)]
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    body = assemble(frag_dir)
    sys.stdout.write(f"{UNRELEASED_HEADING}\n\n" + (body or "_No unreleased entries._\n"))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    frag_dir = fragment_dir(root)
    errors = [e for p in iter_fragments(frag_dir) for e in validate_fragment(p)]

    changelog = root / CHANGELOG_NAME
    _, body, _ = split_unreleased(changelog.read_text(encoding="utf-8"))
    if normalize(body) != normalize(STUB):
        errors.append(
            f"{CHANGELOG_NAME}: `{UNRELEASED_HEADING}` is not the pointer stub. "
            f"Entries go in {FRAGMENT_DIRNAME}/ — appending here is what makes "
            "every open PR conflict on this file."
        )
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    if errors:
        return 1
    print(f"OK: {len(iter_fragments(frag_dir))} fragment(s), Unreleased is the stub")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    frag_dir = fragment_dir(root)
    if not VERSION.match(args.version):
        raise SystemExit(f"--version must be X.Y.Z, got {args.version!r}")
    date = args.date or dt.date.today().isoformat()
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        raise SystemExit(f"--date must be YYYY-MM-DD, got {date!r}")

    fragments = iter_fragments(frag_dir)
    errors = [e for p in fragments for e in validate_fragment(p)]
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not fragments:
        raise SystemExit(f"nothing to release: {FRAGMENT_DIRNAME}/ holds no fragments")

    changelog = root / CHANGELOG_NAME
    head, _, rest = split_unreleased(changelog.read_text(encoding="utf-8"))
    section = f"## [{args.version}] - {date}\n\n{assemble(frag_dir)}"
    new = f"{head}\n{normalize(STUB)}\n\n{section}\n{rest}"

    if args.dry_run:
        sys.stdout.write(new)
        return 0

    changelog.write_text(new, encoding="utf-8", newline="\n")
    for path in fragments:
        path.unlink()
    print(f"released {args.version} ({date}): folded {len(fragments)} fragment(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(ROOT), help="repository root")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("preview", help="render the unreleased section without writing")
    sub.add_parser("check", help="validate fragments and the Unreleased stub")

    rel = sub.add_parser("release", help="fold fragments into a numbered section")
    rel.add_argument("--version", required=True, help="X.Y.Z")
    rel.add_argument("--date", help="YYYY-MM-DD (default: today)")
    rel.add_argument("--dry-run", action="store_true", help="print, do not write")

    args = parser.parse_args(argv)
    # Entries carry em dashes and warning glyphs; a Windows console defaults to
    # cp1252 and dies on them mid-render.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return {"preview": cmd_preview, "check": cmd_check, "release": cmd_release}[
        args.command
    ](args)


if __name__ == "__main__":
    sys.exit(main())
