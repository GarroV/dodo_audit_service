#!/usr/bin/env bash
# Раскатка на площадку одной командой (T246, #201).
#
#   DEPLOY_HOST=<ssh-алиас> scripts/deploy.sh
#
# Существует потому, что порядок шагов оказался НЕОЧЕВИДНЫМ и однажды стоил
# суток: `docker compose up -d --build` пересборку ПРОПУСКАЕТ, а `git pull`
# обновляет каталог, и после него `git log` показывает свежий код, хотя
# контейнер крутит старый. Правило, живущее только в документе, второй раз не
# срабатывает — поэтому оно здесь, вместе с версией сборки и смоуком.
set -uo pipefail

HOST="${DEPLOY_HOST:-}"
if [ -z "$HOST" ]; then
    echo "раскатка: задай DEPLOY_HOST=<ssh-алиас площадки>" >&2
    exit 2
fi
REMOTE_DIR="${DEPLOY_DIR:-C:\\projects\\dodo_audit_service}"
here=$(cd "$(dirname "$0")/.." && pwd)

remote() {
    ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -Command \"cd $REMOTE_DIR; $1\"" 2>&1 | tr -d '\r'
}

step() { printf '\n== %s\n' "$1"; }

step "код на площадку"
remote 'git pull --ff-only' | tail -2 || exit 1

sha=$(remote "git log -1 --format='%h'" | tail -1)
if [ -z "$sha" ]; then
    echo "раскатка: не смог прочитать версию кода на площадке" >&2
    exit 1
fi
echo "версия кода: $sha"

# Явная пара build + up: `up --build` пересборку пропускает.
step "сборка образа с версией $sha"
remote "\$env:BUILD_SHA='$sha'; docker compose build" | tail -2 || exit 1

step "подъём"
remote 'docker compose up -d' | tail -2 || exit 1

step "смоук"
DEPLOY_HOST="$HOST" DEPLOY_DIR="$REMOTE_DIR" "$here/scripts/smoke.sh"
