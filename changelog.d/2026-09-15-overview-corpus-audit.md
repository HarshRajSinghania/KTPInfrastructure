### `scripts`: audit a whole overview corpus against its own descriptors (2026-09-15)

`scripts/audit_overview_corpus.py` sweeps an `overviews/` directory and, per map, answers
the question the engine cannot: does the shipped `.bmp` agree with the `.txt` that ships
beside it? It renders each map through its own descriptor with `render_overview_bmp.py`,
then searches scale and offset for the transform that best lines the two transparency
footprints up. Same map, same projection, so whatever is left is the reference or us.

- **A low IoU is not a verdict.** The tool reports baseline (what the engine draws), best,
  and the transform at best. Identity means the asset is self-consistent; a transform that
  buys real IoU measures how far the shipped image misplaces every player dot drawn on it.
  Buckets are `agrees` / `reference-defective` / `renderer-gap` / `not-comparable`, and the
  reason string names the evidence.
- **An image that keys zero pixels is refused, not scored.** A fully painted overview has
  every pixel opaque, so IoU collapses to "what fraction did we paint" and reads as a bad
  renderer. Those are excluded and listed.
- **A sub-floor score on an image shared with other revisions is not evidence about us**,
  so the report says which maps share an image and the gap list marks it.
- **Byte-identical images are grouped by md5** and a group is flagged when its descriptors
  disagree on ZOOM/ORIGIN/ROTATED, because at most one of those placements can be right.
- The **search re-centres until the optimum is interior** and flags a result that is still
  on the boundary. A fixed grid reports the edge of its own box as an answer:
  `dod_railroad2_b3` wants `dx -31` and a +/-24 grid says -24 with a straight face.
- Inventory covers parentless assets and records a suffix that is not lower case rather than
  normalising it away. The engine asks for `overviews/<map>.bmp`; on the Linux fleet a
  `.BMP` is a missing overview.

Findings across the shipped Steam corpus and the Dallas fleet copy are in
`docs/OVERVIEW_CORPUS_AUDIT.md`, including the ranked defect list and the two maps whose
gap is ours rather than the asset's.

Tests: `tests/unit/test_audit_overview_corpus.py` builds its own images and plants known
defects -- a shift, a scale, a missing key, a descriptor disagreement, a clipped search --
and checks each is recovered and bucketed on the evidence that belongs to it. The anchors
against real shipped maps run only with `KTP_OVERVIEW_CORPUS` set and say so when skipped.
