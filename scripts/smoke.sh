#!/usr/bin/env bash
# Смоук раскатанного бота на целевой площадке. Запускать после каждой раскатки
# и когда что-то отвалилось: он отвечает на вопрос «работает ли ЭТО СЕЙЧАС»,
# на который зелёные тесты не отвечают.
#
#   DEPLOY_HOST=<ssh-алиас> scripts/smoke.sh
#
# Имя хоста не зашито намеренно: репозиторий публичный.
set -uo pipefail

HOST="${DEPLOY_HOST:-}"
if [ -z "$HOST" ]; then
    echo "смоук: задай DEPLOY_HOST=<ssh-алиас площадки>" >&2
    exit 2
fi
REMOTE_DIR="${DEPLOY_DIR:-C:\\projects\\dodo_audit_service}"
fail=0
say() { printf '%-38s %s\n' "$1" "$2"; }

# Healthcheck после пересоздания контейнера сначала показывает "health: starting"
# — вердикт по первому же ответу назвал бы здоровый бот провалом. Ждём исхода,
# а не мгновенного снимка; HEALTH_WAIT секунд, дальше это уже настоящий провал.
HEALTH_WAIT="${HEALTH_WAIT:-90}"
waited=0
while :; do
    status=$(ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -Command \"cd $REMOTE_DIR; docker compose ps --format '{{.Status}}'\"" 2>/dev/null | tr -d '\r')
    case "$status" in
        *healthy*) say "контейнер бота" "OK ($status)"; break ;;
        *starting*)
            if [ "$waited" -ge "$HEALTH_WAIT" ]; then
                say "контейнер бота" "ПРОВАЛ (за ${HEALTH_WAIT}с не стал здоровым: $status)"; fail=1; break
            fi
            sleep 5; waited=$((waited + 5)) ;;
        *) say "контейнер бота" "ПРОВАЛ ($status)"; fail=1; break ;;
    esac
done

version=$(ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -Command \"cd $REMOTE_DIR; git log -1 --format='%h'\"" 2>/dev/null | tr -d '\r')
local_head=$(git log -1 --format='%h' 2>/dev/null)
if [ "$version" = "$local_head" ]; then
    say "версия на площадке" "OK ($version, совпадает с локальной)"
else
    say "версия на площадке" "РАСХОЖДЕНИЕ (площадка $version, локально $local_head)"
fi

# Каталог на площадке и ОБРАЗ, из которого работает бот, — разные вещи:
# git pull обновляет первое, а без пересборки контейнер продолжает крутить
# старый код. Каталог при этом показывает свежий коммит, и это читается как
# доказательство обновления. Поймано 06.09.2026: образ был на сутки старше
# кода, смоук говорил «всё хорошо». Поэтому спрашиваем версию у САМОГО
# контейнера — он единственный знает, чем отвечает.
in_image=$(ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -Command \"cd $REMOTE_DIR; docker compose exec -T bot printenv BUILD_SHA\"" 2>/dev/null | tr -d '\r\n')
if [ -z "$in_image" ]; then
    say "версия внутри образа" "ПРОВАЛ: в образе её нет — собран мимо scripts/deploy.sh"
    fail=1
elif [ "$in_image" = "$version" ]; then
    say "версия внутри образа" "OK ($in_image)"
else
    say "версия внутри образа" "ПРОВАЛ: внутри $in_image, в каталоге $version — pull без пересборки"
    fail=1
fi


errors=$(ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -Command \"cd $REMOTE_DIR; docker compose logs --tail=60 bot 2>&1 | Select-String -Pattern 'Conflict|Traceback|CRITICAL' | Select-Object -First 3\"" 2>/dev/null | tr -d '\r')
if [ -z "$errors" ]; then say "логи бота" "OK (конфликтов и падений нет)"; else say "логи бота" "ПРОВАЛ: $errors"; fail=1; fi

if [ -f .env ]; then
    set -a; . ./.env; set +a
    if curl -s --max-time 10 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN:-}/getMe" | grep -q '"ok":true'; then
        say "Telegram API снаружи" "OK"
    else
        say "Telegram API снаружи" "ПРОВАЛ"; fail=1
    fi
else
    say "Telegram API снаружи" "пропущено (.env нет)"
fi

[ "$fail" -eq 0 ] && echo "СМОУК ЗЕЛЁНЫЙ" || echo "СМОУК КРАСНЫЙ"
exit "$fail"
