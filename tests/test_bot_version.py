"""Версия сборки видна изнутри продукта, а не только по датам снаружи (T246, #201).

Поймано на живом стенде 06.09.2026: каталог на сервере показывал свежий коммит,
а контейнер работал на образе суточной давности. `git pull` каталог обновляет
всегда — и это читается как доказательство обновления, хотя доказательством не
является. Снаружи расхождение теперь ловит смоук, но у самого продукта ответа на
вопрос «какая это сборка» не было ни для аудитора в чате, ни для нас в логах.

Здесь проверяется, что ответ есть в трёх местах, и что незнание версии
называется незнанием, а не выдаётся за версию: заглушка обязана быть видимой,
иначе образ, собранный мимо процедуры, будет молча выглядеть нормальным.
"""

from __future__ import annotations

import logging

import pytest
from bot_harness import AUDITOR_ID, feed, make_bot, text_message

from src.bot.app import build_dispatcher, log_startup
from src.bot.config import BotSettings
from src.bot.texts import t
from src.bot.version import UNKNOWN_VERSION, build_version

pytestmark = pytest.mark.asyncio

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Команда проверяется строкой, а не константой: аудитор набирает именно её.
COMMAND = "/version"


def test_версия_берётся_из_окружения_сборки(monkeypatch):
    monkeypatch.setenv("BUILD_SHA", "abc1234")
    assert build_version() == "abc1234"


def test_незнание_версии_называется_незнанием(monkeypatch):
    # Пустая строка и отсутствие переменной — одно и то же: образ собран мимо
    # процедуры. Молчаливый фолбэк на что-то правдоподобное здесь опаснее
    # отсутствия ответа — по нему нельзя отличить сборку от несборки.
    monkeypatch.delenv("BUILD_SHA", raising=False)
    assert build_version() == UNKNOWN_VERSION
    monkeypatch.setenv("BUILD_SHA", "   ")
    assert build_version() == UNKNOWN_VERSION


def test_стартовая_строка_журнала_называет_версию(monkeypatch, caplog):
    # Без версии в логе первое, на что уходит время при разборе отказа, —
    # выяснение того, что вообще крутится на площадке.
    monkeypatch.setenv("BUILD_SHA", "deadbee")
    with caplog.at_level(logging.INFO, logger="src.bot.app"):
        log_startup(SETTINGS)
    assert "deadbee" in caplog.text


async def test_команда_показывает_версию_в_чате(monkeypatch):
    monkeypatch.setenv("BUILD_SHA", "cafe123")
    bot, session = make_bot()
    dispatcher = build_dispatcher(SETTINGS)
    await feed(dispatcher, bot, text_message(COMMAND))
    assert any("cafe123" in text for text in session.texts), session.texts


async def test_ответ_идёт_на_языке_интерфейса(monkeypatch):
    # Язык — параметр развёртывания (BOT_UI_LANG), и версия не исключение:
    # английский стенд не должен отвечать русской строкой.
    monkeypatch.setenv("BUILD_SHA", "cafe123")
    monkeypatch.setenv("BOT_UI_LANG", "en")
    bot, session = make_bot()
    dispatcher = build_dispatcher(SETTINGS)
    await feed(dispatcher, bot, text_message(COMMAND))
    sent = " ".join(session.texts)
    assert t("version.answer", "en", v="cafe123") in sent
    assert t("version.answer", "ru", v="cafe123") not in sent


async def test_версия_отвечает_и_на_ненастроенном_окружении(monkeypatch):
    """Смысл команды — работать тогда, когда не работает остальное.

    Первая реализация брала язык из НАЧАТОЙ ПРОВЕРКИ, а её чтение тянет за
    собой методику: на стенде без смонтированных данных команда отвечала
    «сбой на моей стороне» — ровно в том случае, ради которого заведена.
    Язык версии берётся у стенда, и от методики она не зависит.
    """
    monkeypatch.setenv("BUILD_SHA", "b0bab0b")
    monkeypatch.delenv("AUDIT_DATA_DIR", raising=False)
    monkeypatch.delenv("STATE_DIR", raising=False)
    bot, session = make_bot()
    dispatcher = build_dispatcher(SETTINGS)
    await feed(dispatcher, bot, text_message(COMMAND))
    assert any("b0bab0b" in text for text in session.texts), session.texts
