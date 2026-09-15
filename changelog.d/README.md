# changelog.d — one file per change

Put your CHANGELOG entry **here**, in its own file. Do not write under
`## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md); that heading is a pointer
stub and the Tier 1 suite fails if anything is added beneath it.

## Why

Every PR used to append its section at the same offset under `## [Unreleased]`.
Merging any one PR therefore conflicted every sibling **on that file and on
nothing else** — each PR's own files always merged clean. On 2026-09-14 six open
PRs were hand-resolved with an identical one-hunk conflict, and merging the first
re-conflicted the rest within minutes; every resolution was "keep both sides".

A `merge=union` driver in `.gitattributes` is the right resolution for an
append-only file and is still set, but **GitHub does not apply merge drivers
server-side** — it helps a local merge and documents the hazard, and that is all.
The structural fix is that two PRs never touch the same file.

## How

Add `changelog.d/YYYY-MM-DD-<slug>.md`:

- **Date** — the day the change is written. It orders the release section.
- **Slug** — lowercase, hyphenated. Derive it from your branch name; a branch
  name is unique per PR by construction, so the path cannot collide with a
  sibling's. If it somehow does, git reports an add/add conflict on a file
  nobody else's work depends on, which is a collision you see rather than a
  cascade you inherit.

The file holds exactly what you would have written under `## [Unreleased]` —
one or more `### area: what changed (YYYY-MM-DD)` sections, in the house format.
The existing entries in CHANGELOG.md are the format guide. A fragment may not
contain a `## ` heading: that would forge a release boundary when folded in.

```bash
python scripts/assemble_changelog.py preview   # what the next release will say
python scripts/assemble_changelog.py check     # what CI runs
```

## At release time

On `main`, and only on `main`:

```bash
python scripts/assemble_changelog.py release --version 1.6.0
```

That folds every fragment into a new `## [1.6.0] - <date>` section, deletes the
fragments, and leaves the stub in place. It is the only thing that writes
CHANGELOG.md, and it runs once per release — so the file is never a shared line
in an open PR.

`2026-09-14-pre-fragment-backlog.md` is the `## [Unreleased]` block as it stood
when this directory was created, moved verbatim. It is not an example of one
change; it is everything that merged before fragments existed.
