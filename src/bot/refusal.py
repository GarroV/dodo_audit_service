"""Отказ движка — сырьё, а не сообщение (задача T127).

Движок отвечает тому, кто зовёт его из командной строки, и отвечает по делу:
`CLN05 в зоне hot_kitchen уже зафиксировано — запись #1. Доснимите фото
(audit.py photo 1 --add ...) или поправьте её (audit.py edit --n 1 ...)`.
Аудитор стоит на точке с телефоном: командной строки у него нет, `hot_kitchen`
он читать не обязан, а язык интерфейса у него может быть не русский. До этой
задачи весь этот текст уходил в чат как есть.

Поэтому отказ здесь разбирается, а не пересказывается. Бот знает, что он
пытался записать, — пункт и зону он и называет, по-человечески и на языке
интерфейса. Сам текст движка идёт в журнал: там он на своём месте, там его
читает тот, кто чинит.

**Занятая пара «пункт + зона» — не ошибка, а частый случай.** Тот же пункт в
той же зоне аудитор снимает дважды за обход. Такой отказ разбирается отдельно:
называется номер уже существующей записи, а под сообщением встают её кнопки
правки — «доснять фото» и «поправить» из совета движка ровно ими и делаются.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src import domain
from src.domain.errors import DomainError

from .inspection import read_inspection
from .texts import t
from .view import zone_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Refusal:
    """Что сказать аудитору и есть ли запись, кнопки которой стоит показать."""

    text: str
    #: Запись, которая уже занимает эту пару «пункт + зона». Не пуста — под
    #: сообщением показываются её кнопки правки: чинить надо именно её.
    clash: domain.Finding | None = None


def item_title(code: str, lang: str) -> str:
    """Вопрос чек-листа словами. Незнакомый код показываем кодом — врать нельзя."""
    try:
        return domain.get_item(code).question(lang)
    except DomainError:
        logger.warning("пункт %s не нашёлся в методике при разборе отказа", code)
        return code


def occupied_by(
    chat_id: int, code: str, zone: str, *, skip: int | None = None
) -> domain.Finding | None:
    """Запись, уже занявшая эту пару, — или ничего.

    `skip` — номер записи, которую сейчас правят: сама с собой она не спорит.
    """
    try:
        inspection = read_inspection(chat_id)
    except DomainError:
        # Состояние не читается — но отказ аудитору сказать всё равно надо, и
        # он его получит без номера записи, а не вместо него молчание (T126).
        logger.exception("состояние чата %s не читается при разборе отказа", chat_id)
        return None
    if inspection is None:
        return None
    for finding in inspection.findings:
        if finding.n != skip and finding.code == code and finding.zone == zone:
            return finding
    return None


def _refusal(
    chat_id: int,
    *,
    code: str,
    zone: str,
    lang: str,
    exc: DomainError,
    keys: tuple[str, str],
    skip: int | None = None,
    n: int | None = None,
) -> Refusal:
    logger.warning("движок отказал в чате %s (%s в зоне %s): %s", chat_id, code, zone, exc)
    duplicate_key, failed_key = keys
    clash = occupied_by(chat_id, code, zone, skip=skip)
    item = item_title(code, lang)
    place = zone_title(zone, lang)
    if clash is not None:
        return Refusal(t(duplicate_key, lang, n=clash.n, item=item, zone=place), clash)
    if n is None:
        return Refusal(t(failed_key, lang, item=item, zone=place))
    return Refusal(t(failed_key, lang, n=n, item=item, zone=place))


def not_recorded(chat_id: int, *, code: str, zone: str, lang: str, exc: DomainError) -> Refusal:
    """Запись не появилась. Пара занята — назвать занявшую и дать её поправить."""
    return _refusal(
        chat_id,
        code=code,
        zone=zone,
        lang=lang,
        exc=exc,
        keys=("record.duplicate", "record.failed"),
    )


def not_changed(
    chat_id: int, n: int, *, code: str, zone: str, lang: str, exc: DomainError
) -> Refusal:
    """Правка не прошла. Та же логика, но запись #n из поиска исключена."""
    return _refusal(
        chat_id,
        code=code,
        zone=zone,
        lang=lang,
        exc=exc,
        keys=("edit.duplicate", "edit.failed"),
        skip=n,
        n=n,
    )
