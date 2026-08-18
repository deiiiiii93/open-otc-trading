#!/usr/bin/env bash
# ONE TIME: cut https://www.artena.one/arena/ over from the SPA-served copy to a
# static directory behind an nginx alias.
#
# Safety properties:
#  - refuses if rsync --delete would remove anything unexpected from the server
#  - backs up the nginx config on the server before recreating the container
#  - restores the backup and redeploys if the site does not come back
#  - leaves open-slides-zero/frontend/public/arena/ IN PLACE, so reverting the
#    location block alone restores the previous site with no data restore
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$DEPLOY/../../.." && pwd)"
PY="${PY:-$REPO/.venv/bin/python}"

OSZ="${OSZ:-/Users/fuxinyao/open-slides-zero}"
SSH_KEY="${SSH_KEY:-/Users/fuxinyao/ppt-pro-server/slides.pem}"
SSH_TARGET="${SSH_TARGET:-ubuntu@43.156.158.156}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/open-slides-zero}"
REMOTE_ARENA="$DEPLOY_DIR/runtime/arena"
SITE_URL="${SITE_URL:-https://www.artena.one/}"
SSH=(/usr/bin/ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$SSH_TARGET")

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

step() { printf '\n=== %s ===\n' "$1"; }

step "1/6 checking the local build"
[ -f "$DEPLOY/build/index.html" ] || { echo "no build — run: deploy.sh build" >&2; exit 1; }

step "2/6 creating the remote directory"
"${SSH[@]}" "mkdir -p '$REMOTE_ARENA'"

step "3/6 dry run — what would rsync --delete remove?"
DELETES="$("$PY" "$DEPLOY/publish.py" publish --dry-run 2>/dev/null | grep -E '^deleting ' || true)"
if [ -n "$DELETES" ] && [ "$FORCE" -ne 1 ]; then
  echo "$DELETES"
  echo
  echo "Refusing: the server holds files this build does not produce." >&2
  echo "Track them under docs/arena/deploy/static/ first, or re-run with --force." >&2
  exit 1
fi

step "4/6 uploading the site"
# --no-verify: the nginx alias does not exist yet, so /arena/ is still answered by
# the SPA fallback and verification could not possibly pass at this point.
"$PY" "$DEPLOY/publish.py" publish --no-verify
echo "uploaded to $REMOTE_ARENA"

step "5/6 patching open-slides-zero and backing up the live nginx config"
"$PY" "$HERE/patch_osz.py" --osz "$OSZ"
"${SSH[@]}" "cp -a '$DEPLOY_DIR/deploy/nginx/default.conf' '$DEPLOY_DIR/deploy/nginx/default.conf.pre-arena'"
( cd "$OSZ" && ./scripts/deploy_incremental.sh --service nginx )

step "6/6 verifying"
ok=0
for _ in $(seq 1 15); do
  if curl -fsS -o /dev/null --max-time 10 "$SITE_URL"; then ok=1; break; fi
  sleep 2
done

if [ "$ok" -ne 1 ]; then
  echo "site did not come back — restoring the previous nginx config" >&2
  "${SSH[@]}" "cp -a '$DEPLOY_DIR/deploy/nginx/default.conf.pre-arena' '$DEPLOY_DIR/deploy/nginx/default.conf' && cd '$DEPLOY_DIR' && sudo docker compose --env-file .env.production -f compose.prod.yml up -d nginx"
  echo "restored. The /arena/ location was NOT applied." >&2
  exit 1
fi

"$PY" "$DEPLOY/publish.py" publish
echo
echo "Cutover complete. /arena/ is now served from $REMOTE_ARENA."
echo "Rollback: remove the 'location /arena/' block from"
echo "  $OSZ/deploy/nginx/default.conf"
echo "and re-run: (cd $OSZ && ./scripts/deploy_incremental.sh --service nginx)"
