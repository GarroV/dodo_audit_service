#!/usr/bin/env bash
# Рабочая копия для блока стройки — одинаково каждый раз.
#
# Заводится руками эта копия была четыре раза по-разному, и дважды это стоило
# защиты: без `examples/` регрессионный якорь молча пропускается, а копия
# `data/`, снятая когда-то, делает детектор утечки слабее настоящего (#175).
# Поэтому процедура здесь, а не в голове у диспетчера.
#
#   tools/worktree_new.sh <блок> <ветка>
#
# Данные кладутся РЕАЛЬНОЙ копией, не симлинком, намеренно: с симлинком
# `cd examples/belgrade-1 && ../../engine/audit.py` уводит в движок основной
# копии, и блок меряет чужой код.
set -euo pipefail

block=${1:?нужно имя блока, например bot17}
branch=${2:?нужно имя ветки, например feat/bot17}
root=$(git rev-parse --show-toplevel)
home=${WORKTREE_HOME:-$HOME/Documents/workbench/worktrees}
target="$home/$(basename "$root")-$block"

[ -e "$target" ] && { echo "уже есть: $target" >&2; exit 1; }

git -C "$root" worktree add -q "$target" -b "$branch" main
cp -R "$root/data" "$target/data"
cp -R "$root/examples" "$target/examples"
ln -sfn "$root/.venv" "$target/.venv"

# Секреты в копию не уезжают, а пути — уезжают: заглушка вместо `AUDIT_DATA_DIR`
# роняет тест демо-стенда (`docker compose config` не разбирает её как том), и
# блок получает красное, не связанное с его работой. Такое красное учит не
# смотреть на красное — цена выше, чем польза от единообразия.
# Пути ведут В СВОЮ копию, а не в основную: с чужим STATE_DIR блок писал бы
# состояние проверок в основную копию, а с чужим AUDIT_DATA_DIR читал бы не ту
# методику, которую ему положили рядом.
python3 - "$root/.env" "$target/.env" "$target" <<'PY'
import pathlib, sys
ПУТИ = {"AUDIT_DATA_DIR": "data", "STATE_DIR": ".state", "MCP_CHECKLIST_STORE": ".store"}
копия = pathlib.Path(sys.argv[3])
строки = []
for строка in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    имя = строка.split("=", 1)[0].strip()
    if "=" not in строка:
        строки.append(строка)
    elif имя in ПУТИ:
        строки.append(f"{имя}={копия / ПУТИ[имя]}")
    else:
        строки.append(f"{имя}=подставь-своё")
pathlib.Path(sys.argv[2]).write_text("\n".join(строки) + "\n", encoding="utf-8")
PY

echo "копия: $target"
echo "данные и эталоны — реальные копии, .env с заглушками, .venv общий"
