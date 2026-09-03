#!/bin/bash
# Мост stdio ↔ HTTP для MCP-сервера проверок (T095).
#
# Claude говорит с MCP построчным JSON по stdio, наш сервер — по HTTP
# JSON-RPC. Мосту достаточно переложить строку в POST и вернуть ответ: bash и
# curl есть на любой машине, и это ровно тот же приём, которым уже работает
# swarm-mcp (D055) — своей зависимости и своего Node ради одного перекладывания
# строк не заводится.
#
# Как подключить (Claude Code, разово):
#   claude mcp add dodo-audit -- /путь/к/репозиторию/tools/mcp_bridge.sh
# Переменные берутся из окружения запускающего:
#   DODO_MCP_URL   — адрес поднятого сервера, например http://127.0.0.1:8265/
#   DODO_MCP_TOKEN — личный токен, тот же, что стоит в MCP_TOKENS в .env
#
# Токен в этот файл не вписывается никогда: файл лежит в публичном
# репозитории, значения живут только в .env и в окружении (конституция).
set -u
URL="${DODO_MCP_URL:?не задан DODO_MCP_URL — адрес поднятого MCP-сервера}"
TOKEN="${DODO_MCP_TOKEN:?не задан DODO_MCP_TOKEN — личный токен доступа}"

while IFS= read -r line; do
  [ -n "$line" ] || continue
  # Уведомление (нет "id") ответа не требует — по JSON-RPC отвечать на него
  # нельзя, ответ выбивает клиента.
  case "$line" in
    *'"id"'*) want_reply=1 ;;
    *) want_reply=0 ;;
  esac
  resp="$(printf '%s' "$line" | curl -sS --max-time 120 -X POST "$URL" \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $TOKEN" \
      --data-binary @- 2>/dev/null)"
  if [ "$want_reply" = "1" ] && [ -n "$resp" ]; then
    printf '%s\n' "$resp"
  fi
done
