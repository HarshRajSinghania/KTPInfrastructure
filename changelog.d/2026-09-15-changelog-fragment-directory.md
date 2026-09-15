### `changelog`: one fragment file per change, so two PRs never touch the same line (2026-09-15)

Every PR appended its section at the same offset under `## [Unreleased]`, so
merging any one PR conflicted every sibling on `CHANGELOG.md` and on nothing
else — each PR's own files always merged clean. Measured 2026-09-14/15: six open
PRs carried an identical one-hunk conflict, were hand-resolved, and the cascade
re-broke them one at a time as each merge landed. Nine hand-resolutions in a
night, every one "keep both sides".

- **`changelog.d/` holds one file per change**, named `YYYY-MM-DD-<slug>.md`.
  The date orders the release section; the slug comes from the branch name,
  which is unique per PR by construction, so the paths cannot collide. A
  collision that did happen would be an add/add on a file nothing depends on —
  visible, and not a cascade.
- **`## [Unreleased]` becomes a pointer stub and stops being a write surface.**
  This is the part that decides whether the problem comes back: assembling
  fragments into the committed file on every merge would re-create the shared
  offset exactly. `scripts/assemble_changelog.py release` folds the fragments
  into a numbered section, deletes them, and is the only thing that writes
  `CHANGELOG.md` — once per release, on `main`. `check` fails if anything is
  added under the stub, so the old habit cannot quietly return.
- **The assembler is deterministic.** Fragments are ordered by filename, never
  by mtime or directory order, and each is normalised to LF with no leading or
  trailing blank lines — so the same fragments produce the same bytes and a
  regeneration is never a spurious diff.
- The `merge=union` driver on `CHANGELOG.md` stays. It was tried as the fix and
  is not one — **GitHub does not apply merge drivers server-side**, so a union
  rule confirmed present on `main` still produced `DIRTY` PRs on the next merge.
  It helps a local merge and documents the hazard; it is belt and braces now,
  not the mechanism.
- The existing `## [Unreleased]` block moved verbatim into
  `2026-09-14-pre-fragment-backlog.md`, so the first real use of the directory
  is not also a migration and the next release cut reproduces those entries
  byte for byte.
- `tests/unit/test_assemble_changelog.py` merges two branches that each add a
  fragment and asserts git resolves it with no conflict, against a control on
  the same repo that reproduces the old shape and does conflict. Tier 1's path
  filter now fires on `changelog.d/` and `CHANGELOG.md`, which it did not, so a
  malformed fragment reaches the gate that validates it.
