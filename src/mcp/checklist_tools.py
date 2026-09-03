"""Инструменты методики: что агент может прочитать и изменить в чек-листе.

Это вторая, пишущая половина блока (T098, решение D049). Первая — чтение
проверок (`src/mcp/tools.py`) — отвечает на вопросы о том, что уже случилось;
эта отвечает за то, по чему будущие проверки будут считаться.

Три правила, которые здесь важнее удобства.

**Правка не переписывает действующий набор.** Каждый вызов даёт НОВУЮ версию
рядом со старой, и делает это хранилище (`src/mcp/checklist.py`), а саму
правку выполняет движок. Версия не становится боевой сама: опубликовать её —
отдельный, названный своим именем вызов.

**Отказ приходит отказом.** Методику, которую движок считать откажется,
хранилище не примет, и вызывающий получает не «готово, но», а отказ словами
движка. Пересказывать эти слова здесь нельзя: правила живут в движке (T103,
T106, T109, T112), и пересказ разошёлся бы с ними при первой же правке.

**Арендатор приходит сверху, из токена.** Как и у инструментов чтения,
аргумента `tenant` здесь нет ни у одного. Право на методику — отдельная
настройка (`MCP_CHECKLIST_TENANTS`), и проверяется оно на входе, до вызова
обработчика: методика одна на всю сеть, и токен партнёра её не открывает.

Оценка здесь не считается ни в каком виде: проверка методики — это запуск
движка подпроцессом, и решает он.
"""

from __future__ import annotations

from typing import Any

from . import checklist as store_api
from .checklist import Outcome, Store
from .errors import ChecklistError

#: Виды пунктов, которые можно завести. `off` сюда не входит намеренно: это не
#: вид пункта, а выключенное состояние, и включают-выключают его
#: `remove_checklist_item` и `restore_checklist_item`. Отданный сюда, он дал бы
#: второй способ выключить пункт — тот, которого никто не ищет в журнале.
ITEM_KINDS = ("violation", "info", "aggregate")

#: Сколько знаков пояснения к правке кладём в журнал. Пояснение — строка для
#: человека, читающего журнал через год, а не место для выгрузки данных.
MAX_NOTE = 500


def _note(note: str | None) -> str | None:
    if note is None:
        return None
    text = note.strip()
    if not text:
        return None
    if len(text) > MAX_NOTE:
        raise ChecklistError(
            f"Пояснение к правке длиннее {MAX_NOTE} знаков. Журнал читает человек: в нём нужна "
            f"причина правки одной фразой, а не выгрузка"
        )
    return text


def _kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    value = kind.strip()
    if value not in ITEM_KINDS:
        raise ChecklistError(
            f"Вид пункта «{kind}» неизвестен, ожидается один из: {', '.join(ITEM_KINDS)}. "
            f"Выключить пункт — это remove_checklist_item, а не вид «off»: так выключение "
            f"видно в журнале одной записью, а не двумя разными способами"
        )
    return value


def _accepted(outcome: Outcome) -> dict[str, Any]:
    """Итог правки для агента. Отклонённая правка возвращается отказом, а не полем.

    Поле `accepted: false` в обычном ответе агент однажды перескажет человеку
    как «сделано»: помеченный отказ он так пересказать не может.
    """
    if not outcome.accepted:
        raise ChecklistError(
            f"Правка отклонена, новой версии не появилось. Движок сказал: {outcome.refusal}"
        )
    return {
        "base_version": outcome.base_version,
        "version": outcome.version,
        "published": False,
        "status": outcome.status,
        "engine": outcome.engine,
    }


def _change(
    store: Store,
    *,
    tenant: str,
    tool: str,
    command: str,
    options: dict[str, Any],
    positional: str | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    return _accepted(
        store_api.apply_change(
            store,
            tenant=tenant,
            tool=tool,
            command=command,
            options=options,
            positional=positional,
            version_name=version_name,
            note=_note(note),
        )
    )


# --- чтение методики ----------------------------------------------------------


def checklist_versions(*, tenant: str, store: Store) -> dict[str, Any]:
    """Версии методики в хранилище, свежие издания впереди.

    Старые версии не удаляются никогда (D050): по ним посчитаны отчёты, и
    отчёт годичной давности обязан оставаться объяснимым. `current` — та
    версия, по которой движок считает проверки сегодня; правка по умолчанию
    отсчитывается не от неё, а от последней записанной, иначе две правки
    подряд теряли бы друг друга.
    """
    действующая = store_api.current_version(store)
    последняя = store_api.tip_version(store)
    найденные = store_api.versions(store)
    return {
        "tenant": tenant,
        "current": действующая,
        "latest": последняя,
        "count": len(найденные),
        "status": (
            f"{len(найденные)} checklist versions stored; the engine currently scores by "
            f"{действующая}"
            + ("" if последняя == действующая else f", while the newest stored one is {последняя}")
        ),
        "versions": [
            {
                "version": версия.version,
                "name": версия.name,
                "published_on": версия.day,
                "fingerprint": версия.fingerprint,
                "current": версия.current,
            }
            for версия in найденные
        ],
    }


def checklist_items(
    *, tenant: str, store: Store, version: str | None = None, process: str | None = None
) -> dict[str, Any]:
    """Пункты чек-листа одной версии — без критериев, они бывают на страницу.

    Критерии одного пункта отдаёт `checklist_item`. Строки идут как лежат в
    методике, вместе с колонками управляющей компании: движок их не читает, но
    и распоряжаться ими не вправе (T109).
    """
    выбранная = version or store_api.current_version(store)
    строки = store_api.read_items(store, version=выбранная)
    отбор = (process or "").strip().lower()
    if отбор:
        строки = [
            r
            for r in строки
            if отбор in (r.get("process_ru", "") + r.get("process_en", "")).lower()
        ]
    зоны = store_api.read_items(store, version=выбранная, what="zones")
    return {
        "tenant": tenant,
        "version": выбранная,
        "filters": {"process": process},
        "count": len(строки),
        "status": (
            f"{len(строки)} checklist items in version {выбранная}"
            if строки
            else f"no checklist items match in version {выбранная}"
        ),
        "items": строки,
        "zones": зоны,
    }


def checklist_item(
    *, tenant: str, store: Store, code: str, version: str | None = None
) -> dict[str, Any]:
    """Один пункт вместе с его критериями D1/D2/D3.

    Критерии — единственный источник того, при каких условиях пункт D1, а при
    каких D2 или D3 (`docs/02-domain.md`): выводить класс «на глаз» нельзя ни
    модели, ни человеку.
    """
    выбранная = version or store_api.current_version(store)
    пункт = store_api.read_item(store, code=code, version=выбранная)
    return {
        "tenant": tenant,
        "version": выбранная,
        "status": f"checklist item {пункт['id']} found in version {выбранная}",
        "item": пункт,
    }


# --- правка пунктов -----------------------------------------------------------


def add_checklist_item(
    *,
    tenant: str,
    store: Store,
    process: str,
    question_ru: str,
    levels: str,
    code: str | None = None,
    process_en: str | None = None,
    question_en: str | None = None,
    zones: str | None = None,
    days: int | None = None,
    criteria: str | None = None,
    kind: str | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Завести пункт чек-листа в новой версии методики.

    Критерии стоит передавать сразу: без них движок методику не примет вовсе —
    пункт без критериев означает, что класс нарушения по фотографии будет
    угадан, а не выведен из правил управляющей компании.
    """
    if code is not None:
        store_api.check_code(code)
    return _change(
        store,
        tenant=tenant,
        tool="add_checklist_item",
        command="add",
        options={
            "id": code,
            "process": process,
            "process-en": process_en,
            "question-ru": question_ru,
            "question-en": question_en,
            "levels": levels,
            "zones": zones,
            "days": days,
            "criteria": criteria,
            "kind": _kind(kind),
        },
        version_name=version_name,
        note=note,
    )


def edit_checklist_item(
    *,
    tenant: str,
    store: Store,
    code: str,
    process: str | None = None,
    process_en: str | None = None,
    question_ru: str | None = None,
    question_en: str | None = None,
    levels: str | None = None,
    zones: str | None = None,
    days: int | None = None,
    criteria: str | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Поправить пункт в новой версии методики. Меняются только названные поля.

    Вида пункта здесь нет намеренно: включение и выключение — это
    `restore_checklist_item` и `remove_checklist_item`, и в журнале они видны
    своим именем, а не как правка поля.
    """
    return _change(
        store,
        tenant=tenant,
        tool="edit_checklist_item",
        command="edit",
        positional=code,
        options={
            "process": process,
            "process-en": process_en,
            "question-ru": question_ru,
            "question-en": question_en,
            "levels": levels,
            "zones": zones,
            "days": days,
            "criteria": criteria,
        },
        version_name=version_name,
        note=note,
    )


def remove_checklist_item(
    *,
    tenant: str,
    store: Store,
    code: str,
    hard: bool | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Выключить пункт в новой версии методики.

    Выключенный пункт остаётся в файле и не предлагается при проверке — так
    видно, что его убрали, и вернуть его можно одной командой. `hard` удаляет
    строку совсем: следа правки при этом не остаётся, и восстанавливать пункт
    придётся из прежней версии.
    """
    return _change(
        store,
        tenant=tenant,
        tool="remove_checklist_item",
        command="remove",
        positional=code,
        options={"hard": hard},
        version_name=version_name,
        note=note,
    )


def restore_checklist_item(
    *,
    tenant: str,
    store: Store,
    code: str,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Включить обратно выключенный пункт — в новой версии методики."""
    return _change(
        store,
        tenant=tenant,
        tool="restore_checklist_item",
        command="restore",
        positional=code,
        options={},
        version_name=version_name,
        note=note,
    )


# --- правка зон ---------------------------------------------------------------


def add_zone(
    *,
    tenant: str,
    store: Store,
    code: str,
    name_ru: str,
    name_en: str | None = None,
    share: float | None = None,
    equal_shares: bool | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Завести физическую зону в новой версии методики.

    Что делать с долями, придётся сказать явно: доли задают вес зоны в оценке,
    складываются в 100% и остаются решением управляющей компании (T112).
    `equal_shares` уравнивает доли всех зон; `share` задаёт вес новой и
    остальных не трогает — после него сумма перестаёт сходиться, и такую
    методику движок считать откажется, то есть версия принята не будет.
    Неравные доли заводятся правкой `zones.csv` рядом с человеком.
    """
    store_api.check_code(code)
    return _change(
        store,
        tenant=tenant,
        tool="add_zone",
        command="zone-add",
        options={
            "code": code,
            "name-ru": name_ru,
            "name-en": name_en,
            "share": share,
            "equal-shares": equal_shares,
        },
        version_name=version_name,
        note=note,
    )


def remove_zone(
    *,
    tenant: str,
    store: Store,
    code: str,
    keep_shares: bool | None = None,
    equal_shares: bool | None = None,
    version_name: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Убрать физическую зону в новой версии методики.

    Убранная зона освобождает свою долю, и раздать её за управляющую компанию
    движок не вправе: `equal_shares` уравнивает доли оставшихся, `keep_shares`
    оставляет их как есть — после чего сумма не сходится и версия принята не
    будет. Зона убирается и из списков зон у пунктов чек-листа.
    """
    return _change(
        store,
        tenant=tenant,
        tool="remove_zone",
        command="zone-remove",
        positional=code,
        options={"keep-shares": keep_shares, "equal-shares": equal_shares},
        version_name=version_name,
        note=note,
    )


# --- публикация ---------------------------------------------------------------


def publish_checklist_version(*, tenant: str, store: Store, version: str) -> dict[str, Any]:
    """Сделать версию той, по которой движок считает проверки.

    Уже посчитанные проверки не пересчитываются и остаются на своей версии
    (D033): отчёт, отправленный партнёру, задним числом не меняется. Откат —
    это публикация прежней версии; удалять версии нельзя вовсе (D050).
    """
    итог = store_api.publish(store, tenant=tenant, version=version)
    return {"tenant": tenant, **итог}
