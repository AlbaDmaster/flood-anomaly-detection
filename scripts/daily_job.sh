#!/usr/bin/env bash
# Daily ingestion and scoring job (issue #30).
#
# Pulls the last 7 days from Open-Meteo for every region, stores any new
# days, scores them, raises alerts and emails HIGH alerts - the same as
# `flask --app run ingest-live --days 7`. Re-running is safe: days already
# stored are skipped. Output is appended to logs/daily_job.log.
#
# Run it as root (Windows Task Scheduler: wsl.exe -d <distro> -u root -- bash <this file>)
# and it starts MySQL if needed, then runs the job as the project's owner.
# Run it as yourself and it only runs the job (MySQL must already be running).
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
OWNER="$(stat -c %U "$PROJECT")"
LOG="$PROJECT/logs/daily_job.log"

if [ "$(id -u)" = 0 ] && [ "$OWNER" != root ]; then
    service mysql status >/dev/null 2>&1 || service mysql start >/dev/null 2>&1
    exec runuser -u "$OWNER" -- bash "$PROJECT/scripts/daily_job.sh" "$@"
fi
if [ "$(id -u)" = 0 ]; then
    service mysql status >/dev/null 2>&1 || service mysql start >/dev/null 2>&1
fi

mkdir -p "$PROJECT/logs"
exec 9>"$PROJECT/logs/.daily_job.lock"
if ! flock -n 9; then
    echo "$(date '+%F %T') previous run still in progress, skipping" >>"$LOG"
    exit 0
fi

{
    echo "=== $(date '+%F %T') daily job started"
    cd "$PROJECT" || exit 1
    if ! "$PROJECT/venv/bin/python" -c "
from app import create_app
from app.extensions import db
with create_app().app_context():
    db.session.execute(db.text('SELECT 1'))
" >/dev/null 2>&1; then
        echo "MySQL is not reachable. Start it with: sudo service mysql start"
        status=1
    else
        "$PROJECT/venv/bin/python" -m flask --app run ingest-live --days 7
        status=$?
    fi
    echo "=== $(date '+%F %T') daily job finished (exit code $status)"
    echo
} >>"$LOG" 2>&1
exit $status
