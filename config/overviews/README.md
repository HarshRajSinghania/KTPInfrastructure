# Corrected overview descriptors

Drop-in replacements for `dod/overviews/<map>.txt` on the fleet, for maps whose shipped image
and shipped descriptor disagree. Each file is the fleet's own bytes with **only** the `ZOOM`
value and `ORIGIN`'s first two components changed; comments, tabs, the camera-pivot z, CRLF and
a missing trailing newline are all as they were.

⛔ **Not deployed.** The measurement behind each number, what was declined, and the deploy
hazards are in [`docs/OVERVIEW_DESCRIPTOR_FIXES.md`](../../docs/OVERVIEW_DESCRIPTOR_FIXES.md).
⛔ **This directory is not the corpus.** The other ~65 descriptors the fleet ships are not
tracked in any repo; only the corrected ones live here.

Regenerate any of them from a measured transform:

```bash
python scripts/correct_overview_descriptor.py <fleet>/overviews/dod_schwetz.txt \
    --scale 1.050505 --dx -1 --dy -1 --out-dir config/overviews
```
