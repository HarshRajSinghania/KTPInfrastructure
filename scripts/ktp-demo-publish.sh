#!/bin/bash
# Publish newly recorded demos: file them, then rebuild the archive pages.
#
# Both halves, in this order, or it does nothing useful: the organizer moves
# demos out of the recording root into demos/<SERVER>/<TYPE>/, and the index
# generator only ever describes what is already filed. Running just the second
# rebuilds the same pages.
#
# Before this existed both ran once a day (04:00 and 04:45), so a demo that
# finished at 21:00 was invisible until the next morning -- and on 2026-08-06
# the organizer silently stopped filing anything, which nobody saw for five
# days because the only signal was in a once-daily log.
set -u

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] publish: $*"; }

log "start"

if ! /usr/local/bin/ktp-organize-hltv-demos.sh; then
    log "ERROR: organizer exited non-zero; rebuilding pages anyway so the site is not left stale"
fi

# --apply is not optional. Without it the generator prints a full, plausible
# list of pages, says "DRY RUN - nothing written", and exits 0 -- a silent no-op
# that reads exactly like success.
if /usr/bin/python3 /usr/local/bin/ktp-fastdl-indexes.py --apply; then
    log "done"
else
    log "ERROR: index generation failed"
    exit 1
fi

# Winner labels from the demos just filed. Downstream of publishing and never a
# reason for it to fail: if the parser, the checkout or the credentials file is
# missing, say so and leave the archive pages as they are. Every match filed in
# the last 3 days is re-labelled each run; the import is idempotent. See
# scripts/demo_team_score.py for what a label is and how it was validated.
INFRA_ROOT="${INFRA_ROOT:-/opt/ktp-infra}"
DOD_TOOLS="${DOD_TOOLS:-/usr/local/bin/dod-tools-cli}"
TEAM_SCORE_CNF="${TEAM_SCORE_CNF:-/etc/ktp/team-score-import.cnf}"
TEAM_SCORE_DB="${TEAM_SCORE_DB:-hlstatsx}"
DEMOS_ROOT="${DEMOS_ROOT:-/home/hltvserver/hlds/dod/demos}"
if [ -x "$DOD_TOOLS" ] && [ -f "$INFRA_ROOT/scripts/import_demo_team_score.py" ] && [ -r "$TEAM_SCORE_CNF" ]; then
    if (cd "$INFRA_ROOT" && /usr/bin/python3 -m scripts.import_demo_team_score \
            --dod-tools "$DOD_TOOLS" --demos-root "$DEMOS_ROOT" --types ktp --since-days 3 \
            --database "$TEAM_SCORE_DB" --defaults-extra-file "$TEAM_SCORE_CNF" --migrate --apply); then
        log "labels: done"
    else
        log "ERROR: winner-label import failed (archive pages are fine)"
    fi
else
    # Name only what is absent. Listing all three sent an investigator hunting two
    # files that were present while one missing .cnf skipped the import for 11 runs.
    missing=
    [ -x "$DOD_TOOLS" ] || missing="$missing $DOD_TOOLS"
    [ -f "$INFRA_ROOT/scripts/import_demo_team_score.py" ] || missing="$missing $INFRA_ROOT/scripts/import_demo_team_score.py"
    [ -r "$TEAM_SCORE_CNF" ] || missing="$missing $TEAM_SCORE_CNF"
    log "ERROR: labels skipped, missing:$missing (archive pages are fine)"
fi
