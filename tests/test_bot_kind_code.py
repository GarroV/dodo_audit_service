"""Вид проверки связывается кодом, а не формулировкой (T152, задача #123).

Два жёстких правила проекта нарушались тут разом.

**Сущности связываются кодами, никогда формулировками.** Слово («Плановая»)
жило как данные: писалось в проверку, уезжало в колонку базы и переводилось
обратно сопоставлением строк с молчаливым возвратом исходника. Переименование
вида проверки — хоть в интерфейсе, хоть в данных — молча ломало перевод: сверка
переставала находить строку, и партнёр получал письмо со словом на чужом языке,
без единой ошибки по дороге.

**Язык — параметр, никогда не константа.** Третий язык требовал правки кода:
таблица `TYPE_EN` в движке знает ровно одно направление, русское → английское.

Проверено запуском до правки: мастер с английским отчётом клал в проверку
английское слово, и `Inspection.kind` отдавал его же — то есть за одним видом
проверки было закреплено два разных английских слова, одно в интерфейсе
(«Planned»), другое в переводе движка («Scheduled»).

Здесь проверяется граница: в проверке лежит КОД, а слово подставляется по
языку — и по языку отчёта в шапке для движка, и по языку интерфейса в чате.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from bot_harness import AUDITOR_ID, CHAT_ID, callback_query, feed, make_bot, text_message

from src.bot.app import build_dispatcher
from src.bot.config import UI_LANG_VAR, BotSettings
from src.bot.keyboards import NEW_INSPECTION_CALLBACK
from src.domain import check_environment, get_state, start_inspection
from src.domain.engine import state_file
from src.domain.errors import ValidationError
from src.domain.kinds import INSPECTION_KINDS, kind_title

#: Код вида проверки, которым идут все случаи ниже. Именно код, а не слово:
#: слово в тесте о кодах было бы той же ошибкой, которую тест ловит.
PLANNED = "planned"


def settings() -> BotSettings:
    return BotSettings(
        token="unused-in-tests",
        allowed_ids=frozenset({AUDITOR_ID}),
        mode="polling",
        auditor_names={},
    )


async def walk_wizard(dp: Any, bot: Any, *, report_lang: str) -> None:
    """Мастер целиком: вход → название → вид проверки → язык отчёта."""
    await feed(dp, bot, text_message("/start"))
    await feed(dp, bot, callback_query(NEW_INSPECTION_CALLBACK))
    await feed(dp, bot, text_message("Demo Pizzeria #1"))
    await feed(dp, bot, callback_query(f"start:kind:{PLANNED}"))
    await feed(dp, bot, callback_query(f"start:lang:{report_lang}"))


def meta_of() -> dict[str, Any]:
    """Шапка проверки так, как её прочитает движок, — из самого файла состояния."""
    raw: dict[str, Any] = json.loads(state_file(CHAT_ID, check_environment()).read_text("utf-8"))
    meta: dict[str, Any] = raw["meta"]
    return meta


# --- сама проверка хранит код ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("report_lang", ["ru", "en"])
async def test_inspection_keeps_the_kind_as_a_code_whatever_the_report_language(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch, report_lang: str
) -> None:
    """В проверке лежит код вида, а не слово, и он один на оба языка отчёта.

    Это и есть связывание кодом: значение, по которому вид проверки узнаётся,
    от языка не зависит вовсе. До правки здесь лежали два разных слова.
    """
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, _ = make_bot()
    dp = build_dispatcher(settings())

    await walk_wizard(dp, bot, report_lang=report_lang)

    inspection = get_state(CHAT_ID)
    assert inspection is not None
    assert inspection.kind == PLANNED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report_lang", "word"),
    [("ru", "Плановая"), ("en", "Planned")],
)
async def test_engine_header_gets_the_word_on_the_report_language(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch, report_lang: str, word: str
) -> None:
    """Слово подставляется при печати и ровно на том языке, на котором печатают.

    Шапку `meta` читает движок и печатает партнёру; сопоставлять там строки
    больше не с чем — вид уже стоит на языке отчёта (`meta.lang`).
    """
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, _ = make_bot()
    dp = build_dispatcher(settings())

    await walk_wizard(dp, bot, report_lang=report_lang)

    meta = meta_of()
    assert meta["lang"] == report_lang
    assert meta["type"] == word


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("report_lang", "word"),
    [("ru", "Плановая"), ("en", "Planned")],
)
async def test_the_auditor_is_told_the_word_not_the_code(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch, report_lang: str, word: str
) -> None:
    """В чате аудитор читает слово: код в проверке — не повод показывать код.

    Язык тут язык начатой проверки: мастер задаёт его одним вопросом на все три
    поля, и подтверждение старта аудитор читает уже внутри проверки.
    """
    monkeypatch.setenv(UI_LANG_VAR, "en")
    bot, session = make_bot()
    dp = build_dispatcher(settings())

    await walk_wizard(dp, bot, report_lang=report_lang)

    started = session.last_text
    assert word in started
    assert PLANNED not in started


# --- формулировку вместо кода не принимаем -----------------------------------


def test_starting_an_inspection_with_a_word_instead_of_a_code_is_refused(
    domain_env: Path,
) -> None:
    """«Плановая» видом проверки больше не является — это перевод кода.

    Отказ, а не молчаливая запись: молчаливая и была причиной, по которой
    формулировка расползлась в состояние, базу и отчёт.
    """
    with pytest.raises(ValidationError) as отказ:
        start_inspection(CHAT_ID, "Белград-1", "Плановая", "ru")
    assert "Плановая" in str(отказ.value)
    assert PLANNED in str(отказ.value)
    # Проверка не должна оказаться начатой наполовину: отказ идёт ДО движка.
    assert not state_file(CHAT_ID, check_environment()).exists()


def test_unknown_kind_code_is_refused_by_the_table_not_by_a_key_error(
    domain_env: Path,
) -> None:
    with pytest.raises(ValidationError):
        kind_title("no-such-kind", "ru")


def test_unknown_language_is_refused_rather_than_falling_back_to_russian() -> None:
    """Молчаливый откат поставил бы русское слово в шапку чужого отчёта."""
    with pytest.raises(ValidationError):
        kind_title(PLANNED, "sr")


def test_every_kind_is_translated_into_every_language_of_the_table() -> None:
    """Третий язык — строка в таблице, а не правка кода.

    Полнота таблицы проверяется здесь, потому что дырка в ней видна только в
    тот момент, когда отчёт уже собирается партнёру.
    """
    langs = {lang for titles in INSPECTION_KINDS.values() for lang in titles}
    for code, titles in INSPECTION_KINDS.items():
        assert set(titles) == langs, code
        for lang in langs:
            assert kind_title(code, lang).strip()
