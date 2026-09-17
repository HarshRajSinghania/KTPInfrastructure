### `scripts`: hltv-api.py.example reconciled with what actually runs (2026-09-16)

`hltv-api.py` carries a live shared secret, so the `.example` is the only tracked copy — and
nothing had ever compared the two. `check-hltv-api-drift.py` (added in #387) now can, and its
first run says exit 1: **51 lines only in the example, 100 only in the deployed file.**

The drift is bidirectional, which the earlier note did not capture. The example was described as
being ahead of the box; it is also behind it. Content live on the data server since before
2026-08-08 and tracked nowhere:

- `PIPE_WRITE_TIMEOUT`
- `STATE_TRIGGER_POLL_SEC` / `_MAX_WAIT` / `_FRESH_SEC`, the `/state` trigger-flush polling
- the `GET /health` route

So "fix the drift by copying the example over the live file" would have deleted working code.

This regenerates the example from the deployed file and re-applies the two things the example
had and the box lacks: `import sys`, and the v2.3 empty-key fail-fast that refuses to start on an
empty `AUTH_KEY` (an empty `hmac.compare_digest` authenticates anonymous requests). The key
resolution stays env-var-then-inline with an empty default, so the tracked file cannot carry a
secret and an unconfigured copy refuses to boot rather than running open.

⚠️ The deployed file is unchanged by this commit. It is still the v2.2 body without the
fail-fast, and closing that needs `systemctl restart hltv-api` — which `hltv-restart-all.sh`
does not do. Operator call, and worth a quiet window: the plugin POSTs `record` /
`stoprecording` to this service.
