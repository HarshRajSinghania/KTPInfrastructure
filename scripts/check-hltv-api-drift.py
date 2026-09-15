#!/usr/bin/env python3
"""Diff the running /home/hltvserver/hltv-api.py against scripts/hltv-api.py.example.

The deployed file carries a live shared secret, so it can never be committed; the
`.example` is the only tracked copy and until now nothing compared the two. Every
secret-shaped assignment is replaced with the same placeholder on BOTH sides
before the compare, so the key resolution differing (inline literal on the box,
env var in the example) is not reported as drift and the value cannot reach
stdout.

Redaction is by SHAPE, not by a list of known names: a secret added to the live
file under a new name is redacted the first time it appears. A surviving
secret-shaped assignment with a quoted literal aborts before anything is printed.

Run it on the data server:

    python3 check-hltv-api-drift.py --example /opt/ktp-infra/scripts/hltv-api.py.example

Exit: 0 identical, 1 drift, 2 a file is missing or unreadable, 3 redaction failed.
"""
import argparse
import difflib
import os
import re
import sys

DEFAULT_LIVE = "/home/hltvserver/hltv-api.py"
DEFAULT_EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hltv-api.py.example")

PLACEHOLDER = "<redacted-by-drift-check>"

_SECRET_ASSIGN = re.compile(
    r"^(?P<indent>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*"
    r"(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD|PWD))\s*=\s*(?P<value>.*)$"
)
# A BARE string literal this long is a filled-in credential. An os.environ.get()
# expression is not, even though its env-var name is a quoted string of the same
# shape — that distinction is why this matches the whole value, not a substring.
_BARE_LITERAL = re.compile(r"""^['"][A-Za-z0-9+/=_-]{12,}['"]$""")


def _value(line):
    """The assigned expression with any trailing comment removed."""
    m = _SECRET_ASSIGN.match(line)
    if not m:
        return ""
    v = m.group("value")
    # Only strip a comment that starts outside a string literal.
    depth = None
    for i, ch in enumerate(v):
        if depth is None and ch in "\"'":
            depth = ch
        elif depth is not None and ch == depth:
            depth = None
        elif depth is None and ch == "#":
            v = v[:i]
            break
    return v.strip()


def redact(text):
    """Replace the right-hand side of every secret-shaped assignment."""
    out = []
    for line in text.splitlines():
        m = _SECRET_ASSIGN.match(line)
        if m:
            line = "%s%s = %s" % (m.group("indent"), m.group("name"), PLACEHOLDER)
        out.append(line)
    return out


def leaks(lines):
    """Secret-shaped assignments whose value is a bare credential literal."""
    return [n for n, line in enumerate(lines, 1) if _BARE_LITERAL.match(_value(line))]


def read(path):
    with open(path, "r", encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", default=DEFAULT_LIVE, help="the deployed file (default: %(default)s)")
    ap.add_argument("--example", default=DEFAULT_EXAMPLE, help="the tracked copy")
    args = ap.parse_args(argv)

    try:
        live_raw, example_raw = read(args.live), read(args.example)
    except OSError as e:
        print("cannot read: %s" % e, file=sys.stderr)
        return 2

    live, example = redact(live_raw), redact(example_raw)

    bad = leaks(live) + leaks(example)
    if bad:
        print("redaction failed on line(s) %s — refusing to print a diff"
              % ", ".join(str(n) for n in bad), file=sys.stderr)
        return 3

    if live == example:
        print("hltv-api: no drift (%d lines, secrets redacted before compare)" % len(live))
        return 0

    diff = difflib.unified_diff(example, live, fromfile=args.example, tofile=args.live, lineterm="")
    print("\n".join(diff))
    added = sum(1 for d in difflib.ndiff(example, live) if d[0] == "+")
    removed = sum(1 for d in difflib.ndiff(example, live) if d[0] == "-")
    print("\nhltv-api: DRIFT — %d line(s) only on the box, %d only in the example"
          % (added, removed))
    return 1


if __name__ == "__main__":
    sys.exit(main())
