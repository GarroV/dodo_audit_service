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

remote() {
    ssh -o BatchMode=yes "$HOST" "powershell -NoProfile -Command \"cd $REMOTE_DIR; $1\"" 2>/dev/null | tr -d '\r'
}

# Состояние КОНКРЕТНОГО сервиса, а не всего стенда одной строкой. С одним
# сервисом разницы не было, с тремя она решающая: `docker compose ps` печатает
# по строке на сервис, и поиск слова по всему выводу отвечает «здоров», когда
# здоров хоть кто-нибудь.
service_status() {
    remote "docker compose ps --format '{{.Service}}|{{.Status}}'" | awk -F'|' -v s="$1" '$1 == s { print $2 }'
}

# Healthcheck после пересоздания контейнера сначала показывает "health: starting"
# — вердикт по первому же ответу назвал бы здоровый контейнер провалом. Ждём
# исхода, а не мгновенного снимка; HEALTH_WAIT секунд, дальше это уже провал.
HEALTH_WAIT="${HEALTH_WAIT:-90}"

await_health() {
    svc="$1"; label="$2"; waited=0
    while :; do
        status=$(service_status "$svc")
        case "$status" in
            # ВНИМАНИЕ, порядок веток: «unhealthy» содержит в себе «healthy», и
            # проверка на здоровье, стоящая первой, объявляла бы больной
            # контейнер здоровым. Найдено разбором при добавлении второго
            # сервиса — с одним сервисом это молчало.
            *unhealthy*) say "$label" "ПРОВАЛ (нездоров: $status)"; fail=1; return ;;
            *healthy*) say "$label" "OK ($status)"; return ;;
            *starting*)
                if [ "$waited" -ge "$HEALTH_WAIT" ]; then
                    say "$label" "ПРОВАЛ (за ${HEALTH_WAIT}с не стал здоровым: $status)"; fail=1; return
                fi
                sleep 5; waited=$((waited + 5)) ;;
            "") say "$label" "ПРОВАЛ: сервиса $svc нет в стенде"; fail=1; return ;;
            *) say "$label" "ПРОВАЛ ($status)"; fail=1; return ;;
        esac
    done
}

await_health bot "контейнер бота"
await_health mcp "контейнер MCP-сервера"

version=$(remote "git log -1 --format='%h'")
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
in_image=$(remote "docker compose exec -T bot printenv BUILD_SHA" | tr -d '\n')
if [ -z "$in_image" ]; then
    say "версия внутри образа" "ПРОВАЛ: в образе её нет — собран мимо scripts/deploy.sh"
    fail=1
elif [ "$in_image" = "$version" ]; then
    say "версия внутри образа" "OK ($in_image)"
else
    say "версия внутри образа" "ПРОВАЛ: внутри $in_image, в каталоге $version — pull без пересборки"
    fail=1
fi


errors=$(remote "docker compose logs --tail=60 bot 2>&1 | Select-String -Pattern 'Conflict|Traceback|CRITICAL' | Select-Object -First 3")
if [ -z "$errors" ]; then say "логи бота" "OK (конфликтов и падений нет)"; else say "логи бота" "ПРОВАЛ: $errors"; fail=1; fi

# --- подключение к MCP: то, ради чего заведены T255 и T256 -------------------
#
# Контейнер, поднятый и здоровый, доступом не является. Ниже проверяется цепочка
# целиком: сервер отвечает на своей петле, туннель к этой петле поднят и
# зарегистрирован, а адрес, который бот РАЗДАЁТ людям, ведёт наружу, а не на
# машину читающего.

probe=$(remote "docker compose exec -T mcp python tools/mcp_healthcheck.py 2>&1; echo rc=\$LASTEXITCODE")
case "$probe" in
    *rc=0*) say "MCP-сервер отвечает на петле" "OK" ;;
    *) say "MCP-сервер отвечает на петле" "ПРОВАЛ: $(printf '%s' "$probe" | tr '\n' ' ')"; fail=1 ;;
esac

# Наружу площадка выходит ОБЩИМ входом Tailscale Funnel (D102), а не своим
# туннелем: домена в учётке нет, а Funnel даёт постоянный адрес без него —
# ровно так же живёт соседний продукт на этой машине.
#
# Между входом и сервером стоит звено: сервер слушает петлю ВНУТРИ контейнера,
# и проброшенное соединение до неё не доходит — оно приходит на внешний
# интерфейс, которого сервер не слушает. Звено сидит в сетевом пространстве
# сервера, принимает на внешнем интерфейсе и передаёт на петлю.
proxy=$(remote "docker ps --filter name=dodo-mcp-proxy --format '{{.Status}}'")
case "$proxy" in
    "") say "звено до сервера" "ПРОВАЛ: контейнер dodo-mcp-proxy не запущен"; fail=1 ;;
    Up*) say "звено до сервера" "OK ($proxy)" ;;
    *) say "звено до сервера" "ПРОВАЛ ($proxy)"; fail=1 ;;
esac

# Funnel, включённый и не отдающий наш порт, выглядит так же, как выключенный:
# спрашивается сам Tailscale, а не память оператора.
funnel=$(remote "tailscale funnel status 2>&1 | Select-String -Pattern '127.0.0.1:8266' | Select-Object -First 1")
if [ -n "$funnel" ]; then
    say "общий вход отдаёт сервер" "OK"
else
    say "общий вход отдаёт сервер" "ПРОВАЛ: Funnel не проксирует порт звена"; fail=1
fi

# Адрес спрашивается у САМОГО бота, а не у файла рядом: печатает человеку он, и
# отвечает только он. Петля здесь — сегодняшняя поломка целиком: человек из
# другого города получал строку, указывающую на его собственную машину.
url=$(remote "docker compose exec -T bot printenv BOT_MCP_URL" | tr -d '\n')
case "$url" in
    "") say "адрес в строке настройки" "ПРОВАЛ: не задан BOT_MCP_URL — бот печатает заглушку"; fail=1 ;;
    http://127.0.0.1*|http://localhost*|https://127.0.0.1*|https://localhost*|http://\[::1\]*)
        say "адрес в строке настройки" "ПРОВАЛ: петля — строка ведёт на машину того, кто её выполнит"; fail=1 ;;
    https://*) say "адрес в строке настройки" "OK (адрес туннеля, TLS)" ;;
    *) say "адрес в строке настройки" "ПРОВАЛ: без TLS ($url)"; fail=1 ;;
esac

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

echo
echo "Доступ снаружи этим прогоном НЕ проверен: он идёт с той же машины, что и"
echo "раскатка. Ответ на «доступен ли сервер с чужой машины» даёт только"
echo "tools/mcp_outside.py, запущенный НЕ на площадке."
echo
[ "$fail" -eq 0 ] && echo "СМОУК ЗЕЛЁНЫЙ" || echo "СМОУК КРАСНЫЙ"
exit "$fail"
