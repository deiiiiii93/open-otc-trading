#!/usr/bin/env bash
# Publish arena reports to https://www.artena.one/arena/
#
#   deploy.sh build [--no-pdf]     render the site into build/ (local only)
#   deploy.sh preview              serve build/ on http://localhost:8080
#   deploy.sh stats                pull the access log and refresh stats.json
#   deploy.sh publish [--dry-run]  rsync build/ to the server, then verify
#   deploy.sh status               manifest vs live — what is not published yet
#   deploy.sh bootstrap            ONE TIME: cut /arena/ over to the static dir
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
PY="${PY:-$REPO/.venv/bin/python}"

[ -x "$PY" ] || { echo "python not found at $PY (set PY=...)" >&2; exit 1; }

# Print the header comment block: every leading-# line after the shebang, stopping
# at the first line that is not a comment. Robust to editing the header.
usage() {
  awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  build)     exec "$PY" "$HERE/build.py" "$@" ;;
  publish)   exec "$PY" "$HERE/publish.py" publish "$@" ;;
  stats)     exec "$PY" "$HERE/collect_stats.py" "$@" ;;
  status)    exec "$PY" "$HERE/publish.py" status "$@" ;;
  bootstrap) exec "$HERE/server/bootstrap.sh" "$@" ;;
  preview)
    [ -d "$HERE/build" ] || { echo "no build/ — run: deploy.sh build" >&2; exit 1; }
    echo "serving $HERE/build on http://localhost:8080/  (ctrl-c to stop)"
    exec "$PY" -m http.server 8080 --directory "$HERE/build"
    ;;
  help|-h|--help) usage ;;
  *) echo "Unknown command: $cmd" >&2; usage; exit 1 ;;
esac
