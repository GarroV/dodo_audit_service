#!/bin/sh
# Обновление демо-стенда из `main` (задача T102, решение D059).
#
# Демо отставало от продукта, потому что было разовым ручным сидом: кто-то
# должен был вспомнить его пересобрать. Этот скрипт — тот самый механизм,
# который делает отставание невозможным: он приводит рабочую копию к `main`,
# пересобирает демо-образ и пересевает демо-данные. Запускать его руками не
# нужно — он вешается на расписание площадки (на Windows-сервере это
# `schtasks`, на unix — cron), и тогда демо догоняет `main` само.
#
#   tools/demo_refresh.sh          # обновить демо
#   tools/demo_refresh.sh --check  # ничего не менять, только проверить, что сможет
#
# Боевой стенд не трогается: пересобираются и пересоздаются поимённо только
# сервисы демо-профиля. Пример строки расписания (unix, раз в 10 минут):
#
#   */10 * * * * /path/to/repo/tools/demo_refresh.sh >> /var/log/demo-refresh.log 2>&1
#
# Переменные:
#   DEMO_REFRESH_REF    что считать источником демо (по умолчанию origin/main)
#   DEMO_REFRESH_FORCE  1 — обновлять поверх незакоммиченных правок
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REF=${DEMO_REFRESH_REF:-origin/main}
REMOTE=${REF%%/*}
BRANCH=${REF#*/}
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say() { printf '[demo-refresh] %s\n' "$*"; }
die() { printf '[demo-refresh] ОТКАЗ: %s\n' "$*" >&2; exit 1; }

cd "$ROOT"

# Незакоммиченные правки. `reset --hard` ниже уничтожит их без следа, поэтому
# отказ громкий, а не «на всякий случай пропустим шаг»: молча не обновившийся
# демо-стенд — ровно та беда, которую скрипт и должен убрать. `--check`
# отказывает здесь ровно так же и с тем же кодом: проверка, которая говорит
# «всё хорошо» там, где обновление откажет, бесполезна.
if [ -n "$(git status --porcelain)" ] && [ "${DEMO_REFRESH_FORCE:-0}" != "1" ]; then
    die "в рабочей копии $ROOT есть незакоммиченные правки — обновление их сотрёт.
       Разберитесь с ними или запустите с DEMO_REFRESH_FORCE=1"
fi

say "источник демо: $REF"
git fetch --quiet "$REMOTE" "$BRANCH" || die "не удалось получить $BRANCH из $REMOTE"
TARGET=$(git rev-parse "$REF")
CURRENT=$(git rev-parse HEAD)
say "сейчас $CURRENT, в $REF $TARGET"

if [ "$CHECK_ONLY" = "1" ]; then
    # Проверка без изменений: связь с origin есть, файл стенда разбирается,
    # демо-профиль в нём объявлен. Дальше «сможет обновить» — уже про докер.
    docker compose --profile demo config --services >/dev/null \
        || die "docker-compose.yml не разбирается или профиль demo не объявлен"
    docker compose --profile demo config --services | grep -qx demo \
        || die "в docker-compose.yml нет сервиса demo"
    if [ "$CURRENT" = "$TARGET" ]; then
        say "проверка пройдена: демо уже на $REF"
    else
        say "проверка пройдена: демо отстаёт от $REF и будет обновлён"
    fi
    exit 0
fi

git reset --hard --quiet "$REF"
say "рабочая копия приведена к $REF"

# Поимённо `demo-seed demo`, а не весь профиль: `up` без имён перезапустил бы
# и боевого бота, оборвав идущие проверки. `--force-recreate` нужен именно
# сиду — без него уже отработавший контейнер не запускается заново, и демо
# осталось бы с прошлыми данными, то есть отстало бы снова.
docker compose --profile demo up -d --build --force-recreate demo-seed demo
say "демо пересобрано и пересеяно"

docker compose --profile demo logs --no-log-prefix --tail 6 demo-seed
docker compose --profile demo ps demo-seed demo
