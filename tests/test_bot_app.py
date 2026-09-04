"""Точка входа бота: что происходит между запуском процесса и первым сообщением.

Проверяется не «функция вызывается», а два решения, которые иначе живут только
в комментарии и молча исчезают при первой же правке:

* обновления обрабатываются **последовательно** (`handle_as_tasks=False`) — это
  и есть механизм задачи T059: пачка, пришедшая разом после потери связи,
  разбирается в порядке доставки, а не вперемешку по задачам;
* методика проверяется **до** первого сообщения, а не при первой записи: пустой
  или неполный каталог методики дал бы честный на вид ответ «нарушений нет»,
  и узнал бы об этом аудитор уже на точке.

До Telegram ни один тест не доходит: `Dispatcher.start_polling` подменён, сеть
не поднимается, токен выдуман.
"""

from __future__ import annotations

from typing import Any

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from bot_harness import AUDITOR_ID, FAKE_TOKEN

from src.bot import app
from src.bot.config import BotSettings
from src.domain.errors import ConfigError

SETTINGS = BotSettings(token=FAKE_TOKEN, allowed_ids=frozenset({AUDITOR_ID}), mode="polling")


class PollingSpy:
    """Подмена `Dispatcher.start_polling`: запоминает, с чем её позвали.

    Экземпляр класса — не функция и потому не дескриптор: подставленный в
    `Dispatcher`, он не связывается с экземпляром диспетчера, и первым
    позиционным приходит сам бот, а не `self`.

    Здесь же складываются объявленные команды меню (T139): их бот сообщает
    телеграму настоящим запросом, и без подмены прогон уходил бы в сеть — то
    самое, чего этот файл не делает по построению.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.commands: list[list[Any]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))

    @property
    def kwargs(self) -> dict[str, Any]:
        assert self.calls, "бот не начал слушать Telegram"
        return self.calls[-1][1]


@pytest.fixture
def polling(monkeypatch: pytest.MonkeyPatch) -> PollingSpy:
    """Бот, поднятый без сети: настройки подставлены, опрос Telegram подменён."""
    spy = PollingSpy()
    monkeypatch.setattr(Dispatcher, "start_polling", spy)
    monkeypatch.setattr(app, "load_bot_settings", lambda: SETTINGS)
    monkeypatch.setattr(app.domain, "check_environment", lambda: None)

    async def remember_commands(bot: Bot, commands: list[Any], **kwargs: Any) -> bool:
        spy.commands.append(commands)
        return True

    monkeypatch.setattr(Bot, "set_my_commands", remember_commands)
    return spy


@pytest.mark.asyncio
async def test_updates_are_processed_one_by_one_so_a_burst_keeps_its_order(
    polling: PollingSpy,
) -> None:
    """T059: опрос поднимается без задачи на обновление — иначе пачка разберётся вперемешку."""
    await app.start_polling()

    assert polling.kwargs["handle_as_tasks"] is False


@pytest.mark.asyncio
async def test_methodology_is_checked_before_the_first_message(
    monkeypatch: pytest.MonkeyPatch, polling: PollingSpy
) -> None:
    """Неполная методика останавливает запуск, а не всплывает записью в отчёте."""

    def refuse() -> None:
        raise ConfigError("в каталоге методики нет checklist.csv")

    monkeypatch.setattr(app.domain, "check_environment", refuse)

    with pytest.raises(ConfigError):
        await app.start_polling()

    assert polling.calls == [], "бот начал слушать Telegram при неполной методике"


@pytest.mark.asyncio
async def test_menu_is_announced_before_polling_starts(polling: PollingSpy) -> None:
    """Меню объявляется на подъёме, а не когда-нибудь (T139).

    Команда, которой нет в меню, спрятана ровно так же, как была спрятана за
    «Завершить» сама функция показа записанного.
    """
    await app.start_polling()

    assert polling.commands, "команды в меню не объявлены при подъёме бота"
    assert "records" in [c.command for c in polling.commands[0]]


@pytest.mark.asyncio
async def test_polling_gets_the_bot_built_from_settings(polling: PollingSpy) -> None:
    """Слушает Telegram именно тот бот, что собран по настройкам, а не какой-то другой."""
    await app.start_polling()

    (args, _kwargs) = polling.calls[-1]
    bot = args[0]
    assert isinstance(bot, Bot)
    assert bot.token == SETTINGS.token


@pytest.mark.asyncio
async def test_session_is_closed_even_when_polling_fails(
    monkeypatch: pytest.MonkeyPatch, polling: PollingSpy
) -> None:
    """Обрыв опроса не оставляет открытую сессию: соединение закрывается в любом случае."""
    closed: list[bool] = []

    async def explode(dispatcher: Dispatcher, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("Telegram недоступен")

    async def remember_close(session: Any) -> None:
        closed.append(True)

    monkeypatch.setattr(Dispatcher, "start_polling", explode)
    monkeypatch.setattr(AiohttpSession, "close", remember_close)

    with pytest.raises(RuntimeError):
        await app.start_polling()

    assert closed == [True]


def test_entry_point_runs_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m src.bot` доходит до опроса: точка входа не пустая обёртка."""
    started: list[bool] = []

    async def fake_start_polling() -> None:
        started.append(True)

    monkeypatch.setattr(app, "start_polling", fake_start_polling)

    app.main()

    assert started == [True]
