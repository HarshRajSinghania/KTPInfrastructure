#!/bin/bash
# Refresh /opt/keep-the-prac to origin/main before the reconciler runs.
#
# Why a script and not two ExecStartPre lines: the fetch needs a credential
# helper, and systemd expands $VAR in Exec= lines - so writing the helper
# inline would either expand the token into the unit's argv (visible in ps for
# the life of the fetch) or need $$ escaping that is easy to get wrong once and
# never notice, because a wrong token and a wrong quote fail identically.
#
# The token is read by git's helper from the INHERITED environment, so it never
# appears in any process's arguments. Single quotes below are load-bearing:
# they keep $GH_TOKEN literal in the argument and let sh expand it inside git.
#
# Lives outside the checkout on purpose - `git reset --hard` below would
# otherwise be able to rewrite the script that is running it.
set -euo pipefail

REPO=/opt/keep-the-prac

if [ -z "${GH_TOKEN:-}" ]; then
  echo "GH_TOKEN missing - add it to /etc/ktp/identity-reconcile.env" >&2
  exit 2
fi
if [ ! -d "$REPO/.git" ]; then
  echo "$REPO is not a git checkout - clone it first" >&2
  exit 2
fi

HELPER='!f(){ echo username=x-access-token; echo "password=$GH_TOKEN"; };f'

git -C "$REPO" -c credential.helper="$HELPER" fetch --quiet origin main
git -C "$REPO" reset --hard --quiet FETCH_HEAD

# A silent no-op fetch and a successful one look identical, so say which commit
# the run is about to report from. The unit's alert carries this journal tail.
echo "reconciler tree at $(git -C "$REPO" rev-parse --short HEAD)"
