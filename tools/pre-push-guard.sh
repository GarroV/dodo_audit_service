#!/usr/bin/env bash
# Сторож пуша: не пускает наружу значения из приватного списка.
#
# Список ЖИВЁТ ВНЕ РЕПОЗИТОРИЯ — иначе сам список стал бы той утечкой,
# от которой он защищает. Путь по умолчанию: ~/.config/git/secret-terms.txt
# (одна строка — одно значение, пустые строки и # — комментарии).
# Подключение: ln -sf "$(pwd)/tools/pre-push-guard.sh" .git/hooks/pre-push
#
# Совместим с bash 3.2 (штатный на macOS): без mapfile и ассоциативных массивов.
set -uo pipefail

TERMS_FILE="${GIT_SECRET_TERMS:-$HOME/.config/git/secret-terms.txt}"
if [ ! -r "$TERMS_FILE" ]; then
    echo "сторож пуша: списка $TERMS_FILE нет, проверка НЕ делалась" >&2
    exit 0
fi

TERMS=()
while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    TERMS+=("$line")
done < "$TERMS_FILE"
[ "${#TERMS[@]}" -gt 0 ] || exit 0

ZERO=0000000000000000000000000000000000000000
found=0
while read -r _local_ref local_sha _remote_ref remote_sha; do
    [ "$local_sha" = "$ZERO" ] && continue
    if [ "$remote_sha" = "$ZERO" ]; then range="$local_sha"; else range="$remote_sha..$local_sha"; fi
    payload=$(git diff "$range" 2>/dev/null | grep '^+' || true)
    [ -n "$payload" ] || continue
    for term in "${TERMS[@]}"; do
        if printf '%s' "$payload" | grep -qiF -- "$term"; then
            echo "сторож пуша: в уходящих строках запрещённое значение — начинается на «${term:0:2}…», длина ${#term}" >&2
            found=1
        fi
    done
done

if [ "$found" -ne 0 ]; then
    echo "Пуш остановлен. Замени найденное на плейсхолдер, реальное значение держи вне публичного репозитория." >&2
    echo "Осознанно пропустить: git push --no-verify" >&2
    exit 1
fi
exit 0
