"""T211 (D086/D089): кому открыто снятие проверки из истории — настройка и заслон.

Само снятие — что оно делает, как подтверждается, какими словами отказывает —
проверено в `test_mcp_retraction.py`, живым стендом с базой. Здесь база не
нужна вовсе: проверяется только то, кто вообще вправе позвать инструмент.

Вопрос стоит иначе, чем у методики (`test_mcp_checklist_access.py`). Там
спрашивается, КАКАЯ СТОРОНА — методика одна на всю сеть, и правит её
управляющая компания. У снятия сторона уже названа токеном: снимается
проверка того же арендатора, чей токен предъявлен. Открытым остаётся другой
вопрос — КТО ИМЕННО, потому что у одного арендатора токенов несколько, по
человеку на токен, а снятие необратимо для партнёра: документ у него на
руках, а кадры уезжают из хранилища насовсем. Поэтому право настраивается по
токену (`MCP_RETRACTION_TOKENS`), а не по арендатору, — и главный тест файла
как раз доказывает, что у одного арендатора один токен снимает, а другой,
столь же законный, — нет.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from src.mcp.checklist import Store
from src.mcp.config import (
    MCP_RETRACTION_TOKENS_VAR,
    MCP_TOKENS_VAR,
    MIN_TOKEN_LENGTH,
    Settings,
    load_settings,
    resolve_access,
)
from src.mcp.errors import McpConfigError
from src.mcp.rpc import CHECKLIST_CLOSED, RETRACTION_CLOSED, handle

АРЕНДАТОР = "партнёр-а"
ТОКЕН_ПЕРВЫЙ = "a" * MIN_TOKEN_LENGTH
ТОКЕН_ВТОРОЙ = "b" * MIN_TOKEN_LENGTH


def _окружение(**прочее: str) -> dict[str, str]:
    return {
        MCP_TOKENS_VAR: f"{АРЕНДАТОР}={ТОКЕН_ПЕРВЫЙ},{АРЕНДАТОР}={ТОКЕН_ВТОРОЙ}",
        **прочее,
    }


@pytest.fixture
def настройки() -> Settings:
    """Один арендатор, два его токена, снятие открыто только первому."""
    return Settings(
        tokens={ТОКЕН_ПЕРВЫЙ: АРЕНДАТОР, ТОКЕН_ВТОРОЙ: АРЕНДАТОР},
        tenants=(АРЕНДАТОР,),
        host="127.0.0.1",
        port=0,
        retraction_tokens=(ТОКЕН_ПЕРВЫЙ,),
    )


# --- настройка ------------------------------------------------------------


def test_без_настройки_снятие_закрыто_всем() -> None:
    """Без MCP_RETRACTION_TOKENS право не достаётся никому — даже токену,
    заведённому и рабочему для чтения."""
    настройки = load_settings(_окружение())

    assert настройки.retraction_tokens == ()
    assert not настройки.may_retract(ТОКЕН_ПЕРВЫЙ)
    assert not настройки.may_retract(ТОКЕН_ВТОРОЙ)


def test_право_на_снятие_личное_а_не_стороны(настройки: Settings) -> None:
    """Главное утверждение решения: право настраивается по токену, а не по
    арендатору. У обоих токенов один и тот же арендатор, оба одинаково читают
    его историю, — но снять проверку может только тот, кого назвали в
    MCP_RETRACTION_TOKENS. Будь право выдано стороне, оно досталось бы и
    второму токену — в том числе токену, который однажды отдадут агенту
    только на чтение."""
    assert настройки.may_retract(ТОКЕН_ПЕРВЫЙ)
    assert not настройки.may_retract(ТОКЕН_ВТОРОЙ)
    assert настройки.tenant_for(ТОКЕН_ПЕРВЫЙ) == АРЕНДАТОР
    assert настройки.tenant_for(ТОКЕН_ВТОРОЙ) == АРЕНДАТОР


def test_неизвестный_токен_снятия_это_отказ_на_старте() -> None:
    """Токен, названный в MCP_RETRACTION_TOKENS, но отсутствующий среди
    MCP_TOKENS, — отказ на старте, а не тихо невыданное право: опечатка или
    забытая правка в одном из двух мест иначе выглядела бы работающей
    настройкой."""
    чужой = "z" * MIN_TOKEN_LENGTH

    with pytest.raises(McpConfigError) as отказ:
        load_settings(_окружение(**{MCP_RETRACTION_TOKENS_VAR: чужой}))

    assert MCP_RETRACTION_TOKENS_VAR in str(отказ.value)


def test_отказ_на_неизвестный_токен_снятия_не_печатает_сам_токен() -> None:
    """Отказ настроек уходит в лог процесса, а токен, попавший в лог,
    пришлось бы менять — поэтому называется номер записи, а не значение."""
    чужой = "z" * MIN_TOKEN_LENGTH

    with pytest.raises(McpConfigError) as отказ:
        load_settings(_окружение(**{MCP_RETRACTION_TOKENS_VAR: чужой}))

    assert чужой not in str(отказ.value)


def test_повтор_токена_в_перечне_снятия_не_ошибка_настройки() -> None:
    """Один и тот же токен, названный дважды, — не ошибка, а перечисление
    одного человека дважды: право выдаётся ему один раз."""
    настройки = load_settings(
        _окружение(**{MCP_RETRACTION_TOKENS_VAR: f"{ТОКЕН_ПЕРВЫЙ}\n{ТОКЕН_ПЕРВЫЙ}"})
    )

    assert настройки.retraction_tokens == (ТОКЕН_ПЕРВЫЙ,)


def test_оба_поля_токенов_помечены_repr_false(настройки: Settings) -> None:
    """`repr=False` стоит и на карте токенов, и на списке токенов снятия —
    по отдельности, а не в силу одного и того же поля: второе легко забыть,
    добавляя новое поле секретов рядом со старым."""
    поля = {поле.name: поле for поле in dataclasses.fields(настройки)}

    assert поля["tokens"].repr is False
    assert поля["retraction_tokens"].repr is False


def test_токены_снятия_не_печатаются_в_repr_настроек(настройки: Settings) -> None:
    """`repr` настроек уезжает в трейсбек любой соседней ошибки, а трейсбек —
    в лог."""
    текст = repr(настройки)

    assert ТОКЕН_ПЕРВЫЙ not in текст
    assert ТОКЕН_ВТОРОЙ not in текст


# --- вход: resolve_access доносит право до транспорта ----------------------


def test_resolve_access_различает_право_у_одного_и_того_же_арендатора(
    настройки: Settings,
) -> None:
    """Тот же вызов, тот же арендатор, два токена — и две разные судьбы:
    решает это токен из заголовка, а не что-либо ещё."""
    свой = resolve_access(настройки, f"Bearer {ТОКЕН_ПЕРВЫЙ}")
    чужой = resolve_access(настройки, f"Bearer {ТОКЕН_ВТОРОЙ}")

    assert свой.tenant == АРЕНДАТОР
    assert чужой.tenant == АРЕНДАТОР
    assert свой.may_retract is True
    assert чужой.may_retract is False


# --- rpc: заслон стоит на входе, а не в обработчике -------------------------


def _вызов(
    имя: str,
    аргументы: dict[str, Any],
    *,
    checklist: Store | None = None,
    may_retract: bool = False,
) -> Any:
    return handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": имя, "arguments": аргументы},
        },
        tenant=АРЕНДАТОР,
        checklist=checklist,
        may_retract=may_retract,
    )


def test_снятие_без_права_это_помеченный_отказ() -> None:
    """Отказ приходит помеченным (`isError`), а не пустым результатом или
    ошибкой протокола: агент обязан увидеть, что вызов отвергнут, а не
    пересказать это как «проверок нет»."""
    ответ = _вызов("retract_inspection", {}, may_retract=False)

    assert ответ["result"]["isError"] is True
    assert ответ["result"]["content"][0]["text"] == RETRACTION_CLOSED


def test_отказ_снятия_называет_переменную_настройки() -> None:
    """Читает отказ тот же человек, который держит сервер у себя на петле:
    без имени переменной он пойдёт искать причину в коде, а не в `.env`."""
    ответ = _вызов("retract_inspection", {}, may_retract=False)
    текст = ответ["result"]["content"][0]["text"]

    assert MCP_RETRACTION_TOKENS_VAR in текст


def test_заслон_снятия_срабатывает_раньше_проверки_обязательных_аргументов() -> None:
    """Аргументы отсутствуют вовсе — без заслона это был бы отказ протокола
    («не хватает id, reason, confirm_unit, confirm_date»), а не помеченный
    отказ права. Раз ответ всё равно RETRACTION_CLOSED, до разбора аргументов
    (и тем более до базы) дело не доходит."""
    ответ = _вызов("retract_inspection", {}, may_retract=False)

    assert "error" not in ответ
    assert ответ["result"]["content"][0]["text"] == RETRACTION_CLOSED


def test_заслон_снятия_срабатывает_раньше_проверки_неизвестных_аргументов() -> None:
    """То же самое с заведомо чужим аргументом: без заслона он дал бы отказ
    протокола («инструмент не принимает аргументы: ...»), а не помеченный
    отказ права."""
    ответ = _вызов("retract_inspection", {"совсем_не_тот_аргумент": True}, may_retract=False)

    assert "error" not in ответ
    assert ответ["result"]["content"][0]["text"] == RETRACTION_CLOSED


def test_снятие_видно_в_перечне_инструментов_даже_без_права() -> None:
    """Перечень инструментов от права не зависит — то же правило, что у
    методики: перечень, зависящий от прав, сказал бы «такого инструмента
    нет», и человек пошёл бы искать причину в коде вместо `.env`."""
    ответ = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        tenant=АРЕНДАТОР,
        may_retract=False,
    )

    assert ответ is not None
    имена = {инструмент["name"] for инструмент in ответ["result"]["tools"]}
    assert "retract_inspection" in имена


# --- право не протекает на соседние виды инструментов -----------------------


def test_право_на_снятие_не_открывает_методику() -> None:
    """`may_retract=True` не подставляет доступ к методике: заслон методики
    смотрит только на переданное хранилище версий, и оно по-прежнему `None`."""
    ответ = _вызов("checklist_versions", {}, checklist=None, may_retract=True)

    assert ответ["result"]["content"][0]["text"] == CHECKLIST_CLOSED


def test_доступ_к_методике_не_открывает_снятие(tmp_path: Path) -> None:
    """Открытое хранилище версий методики на входе не подставляет право на
    снятие: это два независимых заслона, и второй смотрит только на
    `may_retract`."""
    store = Store(root=tmp_path / "хранилище", live=tmp_path / "методика")

    ответ = _вызов("retract_inspection", {}, checklist=store, may_retract=False)

    assert ответ["result"]["content"][0]["text"] == RETRACTION_CLOSED
