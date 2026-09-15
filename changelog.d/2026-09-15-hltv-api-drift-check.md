### `scripts`: compare the running hltv-api.py against its tracked example (2026-09-15)

`/home/hltvserver/hltv-api.py` holds a live `AUTH_KEY`, so it can never be committed, and nothing
compared it to `scripts/hltv-api.py.example`. There was no way to answer "is what runs what we think
runs" without reading the secret.

- `scripts/check-hltv-api-drift.py` normalises every secret-shaped assignment on both sides to the
  same placeholder, then diffs. Redaction is by shape, not by a list of known names, so a secret
  added under a new name is redacted the first time it appears; a secret-shaped line whose value is
  still a bare credential literal aborts the run before anything is printed. Exit 0 identical,
  1 drift, 2 unreadable, 3 redaction failed.
- First run against the box, read-only: the installed file is **behind** the example, not ahead. It
  is the v2.2 body plus the 2026-08-08 dual-key-close comment, and it is missing the v2.3 fail-fast
  that refuses to start on an empty `AUTH_KEY`. `hmac.compare_digest` is on both sides. Latent, not
  live — the installed key is non-empty — but closing it needs a `systemctl restart hltv-api`, which
  `hltv-restart-all.sh` does not do.
- `docs/LIVE_SCRIPT_INVENTORY.md` records the measurement and the direction of the divergence.
