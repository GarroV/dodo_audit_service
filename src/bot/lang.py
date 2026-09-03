"""Языки этого чата — так, чтобы на них нельзя было упасть (T126).

Язык интерфейса живёт в самой проверке (`Inspection.ui_lang`), а проверка лежит
файлом. Значит, чтобы поздороваться, боту надо этот файл прочитать — и если он
испорчен, чтение бросает отказ ещё до того, как собрана первая строка ответа.
Именно так бот и немел целиком: сказать «состояние испорчено» он не мог, потому
что падал на выборе языка, которым это сказать.

Поэтому здесь чтение состояния защищено, и защищено намеренно узко: **только
ради выбора языка**. Ошибка не глохнет — работа, которой состояние нужно
по-настоящему (показать проверку, добавить запись, собрать отчёт), читает его
обычным вызовом `domain.get_state` и падает, как падала. Испорченный файл
по-прежнему остаётся отказом; но отказ этот теперь можно произнести.
"""

from __future__ import annotations

import logging

from src import domain
from src.domain.errors import DomainError

from .texts import ui_lang_or_default

logger = logging.getLogger(__name__)


def _state(chat_id: int) -> domain.Inspection | None:
    try:
        return domain.get_state(chat_id)
    except DomainError:
        # В журнал — с разбором: путь к файлу и причина нужны тому, кто будет
        # чинить, и не нужны аудитору на точке.
        logger.exception("состояние чата %s не читается — язык взят по умолчанию", chat_id)
        return None


def chat_ui_lang(chat_id: int) -> str:
    """Язык интерфейса этого чата: из начатой проверки, иначе умолчание.

    До старта проверки состояния нет — спрашивать язык интерфейса отдельным
    шагом мастера спека не просит, а падать на приветствии нельзя.
    """
    inspection = _state(chat_id)
    return ui_lang_or_default(None if inspection is None else inspection.ui_lang)


def chat_langs(chat_id: int) -> tuple[str, str]:
    """Язык интерфейса и язык отчёта этого чата.

    Разные языки, и путать их нельзя: интерфейс аудитор читает сам, а
    формулировка от модели уходит партнёру и обязана быть на языке отчёта.
    """
    inspection = _state(chat_id)
    if inspection is None:
        return ui_lang_or_default(None), ui_lang_or_default(None)
    return ui_lang_or_default(inspection.ui_lang), inspection.report_lang
