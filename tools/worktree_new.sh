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

# Секреты в копию не уезжают: имена переменных нужны, значения нет.
sed 's/=.*/=подставь-своё/' "$root/.env" > "$target/.env"

echo "копия: $target"
echo "данные и эталоны — реальные копии, .env с заглушками, .venv общий"
