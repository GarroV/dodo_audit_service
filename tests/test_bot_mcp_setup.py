"""Пункт меню «Установка MCP»: команда для терминала и токен, которого в ней нет.

Задача T209, решение D087. Владелец назвал пункт сам и назвал, как он устроен:
внутри — готовая команда для терминала, как в соседнем продукте.

Главное здесь не команда, а токен. У соседнего продукта он в строку вписан,
потому что там он выпускается ботом на конкретного человека и отзывается одной
командой. Здесь токены живут в `.env` записями «арендатор=токен», принадлежат
СТОРОНЕ и открывают всю её историю проверок, а бот про собеседника знает ровно
одно — что его ID стоит в общем списке разрешённых. Значит, напечатанный токен
был бы выдан наугад, и проверяется это тестом, а не обещанием: в окружении
стоит правдоподобная карта токенов, и ни одно сообщение бота её значений не
содержит.

Второе, что проверяется, — чего в меню нет. По решению D086 снятие сданной
проверки делает управляющая компания через MCP, и входа в него у бота нет
вовсе; меню — самое вероятное место, куда такой вход когда-нибудь припишут.
"""

from __future__ import annotations

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, STRANGER_ID, feed, make_bot, text_message

from src.bot.app import MENU_COMMANDS, announce_commands, build_dispatcher
from src.bot.config import UI_LANG_VAR, BotSettings
from src.bot.routers.mcp import MCP_COMMAND, MCP_URL_VAR
from src.bot.texts import t
from src.domain import start_inspection

pytestmark = pytest.mark.asyncio

COMMAND = f"/{MCP_COMMAND}"

#: Правдоподобная карта токенов ровно в том виде, в каком её задаёт человек в
#: `.env` (`src/mcp/config.py`, `MCP_TOKENS`). Значение выдуманное и живёт
#: только в этом файле.
TOKENS_VAR = "MCP_TOKENS"
TOKEN = "MFVLDaN4gJH2sQ7cX1kZpR8tYbW3eU6o"
TOKENS = f"uk={TOKEN}"

SETTINGS = BotSettings(
    token="unused-in-tests",
    allowed_ids=frozenset({AUDITOR_ID}),
    mode="polling",
    auditor_names={},
)


async def test_команда_для_терминала_приходит_готовой_строкой(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """То, ради чего пункт заведён: строку вставляют в терминал, не собирая её."""
    monkeypatch.setenv(MCP_URL_VAR, "http://127.0.0.1:8265/")
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    команда = session.last_text
    assert команда.startswith("claude mcp add"), "команды для терминала в ответе нет"
    assert "tools/mcp_bridge.sh" in команда, "команда не ведёт в мост подключения"
    assert "http://127.0.0.1:8265/" in команда, "адрес стенда в команду не подставился"


async def test_команда_отдельным_сообщением(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сообщение копируется целиком, поэтому объяснение к команде не приклеено."""
    monkeypatch.delenv(MCP_URL_VAR, raising=False)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    assert len(session.texts) == 2, "объяснение и команда обязаны прийти разными сообщениями"
    assert session.texts[-1].startswith("claude mcp add"), (
        "последней пришла не голая команда — в терминал уедет лишний текст"
    )


async def test_личный_токен_в_переписку_не_печатается(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сам смысл задачи: бот не знает, чей токен ваш, — значит, не печатает ничей.

    Токен открывает историю проверок всей стороны, отзывается правкой `.env` и
    перезапуском сервера, а в переписке остаётся навсегда и пересылается одним
    движением. Напечатать его «тому, кто спросил» — это выдать чужой доступ.
    """
    monkeypatch.setenv(TOKENS_VAR, TOKENS)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    for отправлено in session.texts:
        assert TOKEN not in отправлено, "личный токен уехал в переписку"


async def test_про_личный_токен_сказано_словами(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Молча недоговорить — не честнее, чем напечатать: человеку нужен выход.

    Пункт отдаёт наружу способ подключиться, поэтому он же — место, где сказано,
    что токен личный и откуда его берут. Без этого человек ищет токен сам и
    берёт первый попавшийся, то есть чужой.
    """
    monkeypatch.delenv(MCP_URL_VAR, raising=False)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    assert session.texts[0] == t("mcp.setup", "ru")
    assert "личный" in session.texts[0], "про личный токен не сказано"


async def test_адрес_не_назван_стендом_видно_в_самой_строке(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заглушка вместо молчаливой петли: догадка, выданная за настройку, дороже."""
    monkeypatch.delenv(MCP_URL_VAR, raising=False)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    assert t("mcp.url_unknown", "ru") in session.last_text


async def test_язык_интерфейса_параметр_и_здесь(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Английский стенд не имеет права отвечать на установку по-русски."""
    monkeypatch.setenv(UI_LANG_VAR, "en")
    monkeypatch.delenv(MCP_URL_VAR, raising=False)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))

    assert session.texts[0] == t("mcp.setup", "en")
    assert t("mcp.url_unknown", "en") in session.last_text


async def test_работает_и_до_начала_проверки_и_во_время(
    domain_env: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Установка — не шаг обхода: она не спрашивает проверку и не мешает ей.

    До начала проверки бот на обычный текст отвечает «проверка не начата», и
    попасть под это правило пункту меню было бы легко.
    """
    monkeypatch.delenv(MCP_URL_VAR, raising=False)
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND))
    до = session.last_text

    start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-09-06", auditor="Гарро")
    session.clear()
    await feed(dp, bot, text_message(COMMAND))

    assert до.startswith("claude mcp add")
    assert session.last_text.startswith("claude mcp add")


async def test_постороннему_не_отвечает(domain_env: object) -> None:
    """Пункт даёт способ подключиться к проверкам — за общей дверью, как всё."""
    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)

    await feed(dp, bot, text_message(COMMAND, user_id=STRANGER_ID, chat_id=STRANGER_ID))

    assert not any(текст.startswith("claude mcp add") for текст in session.texts)


async def test_пункт_объявлен_в_меню_телеграма(domain_env: object) -> None:
    """Пункт, о котором нигде не сказано, — то же самое, что его отсутствие."""
    bot, session = make_bot()

    await announce_commands(bot)

    объявлено = [c for c in session.calls if type(c).__name__ == "SetMyCommands"]
    assert объявлено, "команды в меню не объявлены вовсе"
    имена = [c.command for c in объявлено[0].commands]
    assert MCP_COMMAND in имена, "установки MCP в меню нет"


async def test_меню_называет_оба_пункта_словами_владельца(domain_env: object) -> None:
    """Названия названы владельцем (D087), поэтому проверяются, а не подразумеваются."""
    bot, session = make_bot()

    await announce_commands(bot)

    объявлено = [c for c in session.calls if type(c).__name__ == "SetMyCommands"]
    описания = [c.description for c in объявлено[0].commands]
    assert "Новая проверка" in описания, "пункт «Новая проверка» в меню не назван"
    assert "Установка MCP" in описания, "пункт «Установка MCP» в меню не назван"


async def test_спрятанных_команд_у_бота_нет(domain_env: object) -> None:
    """Каждая команда бота названа в меню — иначе о ней знает только тот, кто читал код.

    Состав сверяется целиком, а не «есть ли моя»: команда, добавленная роутером
    и забытая в меню, — это ровно та спрятанная функция, из-за которой показ
    записанного жил за кнопкой «Завершить» (T139). Список здесь придётся
    поправить руками — в этом и смысл: решение «показывать или нет» принимается,
    а не забывается.
    """
    bot, session = make_bot()

    await announce_commands(bot)

    объявлено = next(c for c in session.calls if type(c).__name__ == "SetMyCommands")
    имена = {c.command for c in объявлено.commands}
    assert имена == {"start", "records", "undo", "finish", "mcp", "version"}, (
        "состав меню разошёлся с командами бота"
    )


async def test_снятия_сданной_проверки_в_меню_нет() -> None:
    """Решение D086: снимает проверку управляющая компания через MCP, не аудитор.

    Проверяется не текст, а состав меню: вход, которого у бота нет, не должен
    появиться в нём «за компанию» при следующем пополнении списка.
    """
    имена = [name for name, _ in MENU_COMMANDS]
    for запрещённое in ("retract", "delete", "remove", "drop"):
        assert запрещённое not in имена, f"в меню появилось снятие проверки: /{запрещённое}"
