"""T126: бот не немеет ни на одной форме порчи состояния (задача #101).

Задача была закрыта преждевременно. Тогда проверялось, что глобальный
обработчик **подключён**: снимали регистрацию — семь тестов краснели. Но
подключённый обработчик и работающий обработчик — разные вещи, и защита
оказалась уже задачи: `on_unexpected_error` (`src/bot/app.py`) вычислял язык
внутри собственного `try`, а `chat_ui_lang` (`src/bot/lang.py`) ловил только
`DomainError`. Стоило порче прийти не через `json.JSONDecodeError` — правами на
файл, двоичным мусором, полем `findings` не того типа, — и последний рубеж
умирал сам, записывая в журнал, что не смог сказать аудитору о сбое.

Поэтому здесь перебираются **формы порчи**, а не факт регистрации: каждая форма
приходит в бота своей дверью, и тест обязан краснеть, если хотя бы одна из них
снова доводит до молчания. Восемь форм × восемь входов — вся решётка целиком.

Две формы порчи не бросают ничего вовсе, и это хуже молчания: чужая структура
JSON принималась за настоящую проверку (`/start` показывал «незавершённую» с
пустыми полями), а каталог на месте файла читался как «проверка не начата».
Обе теряли содержимое при следующей «Новой проверке» — про них отдельные тесты
ниже, потому что там проверяется не молчание, а неправда.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from aiogram.types import CallbackQuery, Message
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    Calls,
    feed,
    make_bot,
    photo_message,
    stub_classify,
    stub_transcribe,
    suggestion,
    text_message,
    voice_message,
)
from bot_harness import callback_query as callback

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import start_inspection
from src.domain.config import check_environment
from src.domain.engine import state_file

pytestmark = [pytest.mark.asyncio]

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Под root права ничего не запрещают: `chmod 000` читается как обычный файл, и
#: форма порчи перестаёт быть порчей. Молча зеленеть на этом нельзя.
не_под_рутом = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="под root chmod 000 не мешает чтению — форма порчи не воспроизводится",
)


def _подменить(файл: Path, **поля: object) -> None:
    """Испортить одно поле настоящего состояния, не трогая остального."""
    raw = json.loads(файл.read_text(encoding="utf-8"))
    raw.update(поля)
    файл.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def _каталог_вместо_файла(файл: Path) -> None:
    """Так состояние выглядит после неудачного монтирования тома."""
    файл.unlink()
    файл.mkdir()


#: Формы порчи: как именно ломается `inspection.json`. Все восемь взяты с
#: разбора задачи, и каждая приходит в код своей дверью — `JSONDecodeError`,
#: `UnicodeDecodeError`, `TypeError`, `PermissionError`, и две беззвучные.
ФОРМЫ: dict[str, Callable[[Path], None]] = {
    # Обрыв записи на точке: файл создан, содержимого в нём нет.
    "пустой_файл": lambda f: f.write_text("", encoding="utf-8"),
    # Тот же обрыв, но посередине: JSON начат и не закончен.
    "обрезанный_json": lambda f: f.write_text(f.read_text(encoding="utf-8")[:60], encoding="utf-8"),
    # Валидный JSON, но не объект.
    "список_вместо_объекта": lambda f: f.write_text("[1, 2, 3]", encoding="utf-8"),
    # Не текст вообще: `read_text` падает раньше разбора JSON.
    "двоичный_мусор": lambda f: f.write_bytes(bytes(range(256))),
    # Структура похожа на проверку, но записи — не список.
    "находки_не_список": lambda f: _подменить(f, findings=5),
    # Файл цел, читать его нечем.
    "нет_прав_на_чтение": lambda f: f.chmod(0o000),
    # Чужой JSON на месте проверки: полей проверки в нём нет ни одного.
    "чужая_структура": lambda f: f.write_text('{"hello": "world"}', encoding="utf-8"),
    # На месте файла — каталог.
    "каталог_вместо_файла": _каталог_вместо_файла,
}

ПОРЧА_ПРАВАМИ = "нет_прав_на_чтение"


def формы() -> list[pytest.param]:  # type: ignore[valid-type]
    """Все формы порчи параметрами, с пропуском там, где порча не воспроизводится."""
    return [
        pytest.param(имя, id=имя, marks=[не_под_рутом] if имя == ПОРЧА_ПРАВАМИ else [])
        for имя in ФОРМЫ
    ]


#: Восемь входов бота. Каждый читает состояние — своим путём и в своём роутере,
#: поэтому проверять их поодиночке нельзя: до T126 немыми были все сразу.
ВХОДЫ: dict[str, Callable[[], Message | CallbackQuery]] = {
    "/start": lambda: text_message("/start"),
    "/finish": lambda: text_message("/finish"),
    "/undo": lambda: text_message("/undo"),
    "кадр": lambda: photo_message("frame-1"),
    "голос": lambda: voice_message("voice-1"),
    "текст": lambda: text_message("печь грязная"),
    "правка": lambda: callback("edit:1:zone"),
    "сборка": lambda: callback("fin:build"),
}


@pytest.fixture
def испортить(domain_env: Path) -> Iterator[Callable[[str], Path]]:
    """Начать настоящую проверку и испортить её файл названной формой порчи.

    Проверка начинается по-настоящему (движком), а не подкладывается литералом:
    порча обязана быть порчей боевого состояния, иначе тест меряет собственную
    выдумку. Права возвращаются на выходе — иначе временный каталог прогона
    остаётся неудаляемым.
    """
    испорченные: list[Path] = []

    def сделать(форма: str) -> Path:
        # Прошлая порча убирается целиком: каталог на месте файла не даст
        # движку начать следующую проверку, и тест мерил бы неудачу оснастки.
        прежний = state_file(CHAT_ID, check_environment())
        if прежний.is_dir():
            shutil.rmtree(прежний)
        elif прежний.exists():
            прежний.chmod(0o644)
            прежний.unlink()
        start_inspection(CHAT_ID, "Белград 2", "planned", "ru", date="2026-08-21", auditor="Гарро")
        файл = state_file(CHAT_ID, check_environment())
        ФОРМЫ[форма](файл)
        испорченные.append(файл)
        return файл

    yield сделать

    for файл in испорченные:
        if файл.is_file():
            файл.chmod(0o644)


def заглушить_модель(monkeypatch: pytest.MonkeyPatch) -> tuple[Calls, Calls]:
    """Ни один вход этого набора не имеет права дойти до модели.

    До правки часть форм порчи не бросала ничего, и разговор шёл дальше как на
    здоровом состоянии — прямиком в разбор. Заглушки держат прогон
    герметичным: сети в тестах нет, а списки вызовов заодно показывают, что
    на испорченном состоянии до модели дело не доходит.
    """
    return (
        stub_classify(monkeypatch, suggestion()),
        stub_transcribe(monkeypatch, "печь грязная"),
    )


#: Что бот вправе сказать на испорченном состоянии. Два текста, а не один:
#: `/start` — это выход из тупика, и у него свой ответ с кнопкой «Новая
#: проверка»; всем остальным входам отвечает последний рубеж.
ДОПУСТИМЫЕ = ("start.state_broken", "error.unexpected")


@pytest.mark.parametrize("форма", формы())
async def test_на_испорченном_состоянии_отвечает_каждый_вход(
    испортить: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch, форма: str
) -> None:
    """Аудитор получает ответ всегда — какой бы дверью ни пришла порча.

    Собирается вся решётка сразу, и провалы копятся списком: одна форма порчи,
    прошедшая мимо защиты на одном входе, — это уже немой бот на точке, и
    увидеть надо все такие клетки, а не первую попавшуюся.
    """
    ожидаемые = {t(ключ, "ru") for ключ in ДОПУСТИМЫЕ}
    провалы: list[str] = []

    for имя, собрать in ВХОДЫ.items():
        испортить(форма)
        заглушить_модель(monkeypatch)
        bot, session = make_bot()

        await feed(build_dispatcher(SETTINGS), bot, собрать())

        if not session.texts:
            провалы.append(f"{имя}: бот промолчал")
        elif session.last_text not in ожидаемые:
            провалы.append(f"{имя}: сказано «{session.last_text}»")

    assert not провалы, f"порча «{форма}» — {'; '.join(провалы)}"


@pytest.mark.parametrize("форма", формы())
async def test_старт_на_любой_форме_порчи_даёт_выход(
    испортить: Callable[[str], Path], форма: str
) -> None:
    """`/start` — единственный выход из тупика, и он обязан быть на каждой форме.

    Ответить хоть что-нибудь тут мало: «Что-то пошло не так» без кнопки
    оставляет аудитора там же, где он стоял. Поэтому проверяется именно выход —
    названное вслух повреждение и кнопка «Новая проверка» под ним.
    """
    испортить(форма)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert session.last_text == t("start.state_broken", "ru")
    assert session.keyboard_data() == ["start:new"], "выход назван словами, но нажать нечего"


@pytest.mark.parametrize("форма", формы())
async def test_испорченное_состояние_не_переписывается_ни_одной_формой(
    испортить: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch, форма: str
) -> None:
    """Содержимое не теряется молча: до решения человека файл не трогают (T052).

    Каталог вместо файла проверяется тем же правилом: он обязан остаться
    каталогом, а не превратиться в новую пустую проверку.
    """
    файл = испортить(форма)
    было = файл.read_bytes() if файл.is_file() and форма != ПОРЧА_ПРАВАМИ else None
    заглушить_модель(monkeypatch)
    bot, _ = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    if форма == "каталог_вместо_файла":
        assert файл.is_dir(), "каталог на месте состояния подменён файлом"
        return
    if было is not None:
        assert файл.read_bytes() == было, "бот переписал повреждённое состояние сам"


async def test_чужая_структура_не_выдаётся_за_незавершённую_проверку(
    испортить: Callable[[str], Path],
) -> None:
    """`{"hello":"world"}` — не проверка, и звать её незавершённой нельзя.

    Так выглядел провал: `/start` показывал «незавершённая проверка» с пустой
    точкой, пустой датой и нулём записей — приглашение нажать «Начать новую» и
    затереть то, что в файле лежит. Аудитор при этом не знает, что затирает.
    """
    испортить("чужая_структура")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert session.last_text == t("start.state_broken", "ru")
    assert session.keyboard_data() == ["start:new"], "выход назван словами, но нажать нечего"


async def test_каталог_на_месте_состояния_не_читается_как_непочатая_проверка(
    испортить: Callable[[str], Path],
) -> None:
    """Каталог вместо файла — поломка, а не чистый лист.

    `read_state` (`src/domain/state.py`) проверяет `path.is_file()` и на
    каталоге возвращает `None`, то есть «проверка не начата». Бот здоровался и
    предлагал начать новую — с той же молчаливой потерей того, что лежит рядом.
    """
    испортить("каталог_вместо_файла")
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/start"))

    assert session.last_text != t("start.greeting", "ru"), "поломка выдана за непочатую проверку"
    assert session.last_text == t("start.state_broken", "ru")


@pytest.mark.parametrize("форма", формы())
async def test_разбор_порчи_уходит_в_журнал_а_не_в_чат(
    испортить: Callable[[str], Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    форма: str,
) -> None:
    """Аудитору — причина и выход, тому, кто чинит, — путь к файлу и стек."""
    файл = испортить(форма)
    заглушить_модель(monkeypatch)
    bot, session = make_bot()

    with caplog.at_level("ERROR"):
        await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    сказанное = "\n".join(session.texts)
    assert сказанное, "на `/finish` бот не ответил ничего"
    assert str(файл) not in сказанное, "путь к файлу проверки ушёл аудитору в чат"
    assert "Traceback" not in сказанное, "стек показан аудитору как есть"
    assert str(файл) in caplog.text, "разбор не записан в журнал — чинить будет нечем"


@не_под_рутом
async def test_язык_чата_не_падает_на_нечитаемом_файле(испортить: Callable[[str], Path]) -> None:
    """`chat_ui_lang` — фундамент последнего рубежа, и упасть ему нельзя.

    Именно здесь всё и ломалось: обработчик сбоя первым делом спрашивал язык, а
    язык лежит в самой проверке. `PermissionError` — не `DomainError`, защита
    его не ловила, и обработчик умирал на выборе языка, которым собирался
    сказать о сбое.
    """
    from src.bot.lang import chat_langs, chat_ui_lang

    испортить(ПОРЧА_ПРАВАМИ)

    assert chat_ui_lang(CHAT_ID) == "ru"
    assert chat_langs(CHAT_ID) == ("ru", "ru")


async def test_последний_рубеж_отвечает_даже_если_язык_не_выбрался(
    испортить: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """За `on_unexpected_error` обработчика нет — своя защита ему обязательна.

    Провал первой попытки T126 был устроен именно так: обработчик спрашивал
    язык, падал на этом сам и писал в журнал, что не смог сказать аудитору о
    сбое. Здесь выбор языка ломается нарочно — ответ обязан прийти всё равно,
    на языке стенда.
    """
    испортить("обрезанный_json")

    def упасть(chat_id: int) -> str:
        raise RuntimeError("язык не выбрался")

    monkeypatch.setattr("src.bot.app.chat_ui_lang", упасть)
    bot, session = make_bot()

    await feed(build_dispatcher(SETTINGS), bot, text_message("/finish"))

    assert session.last_text == t("error.unexpected", "ru")


async def test_негодный_язык_стенда_не_делает_бота_немым(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Опечатка в `BOT_UI_LANG` — тоже способ онеметь, и он закрыт.

    Отказ на неизвестном языке стенда законный и приходит на старте бота
    (T131). Но переменную можно подменить и после старта — перезапуском
    контейнера с чужим окружением, — и тогда `ui_lang_or_default` отказывал бы
    на каждом ответе, включая ответ о сбое. Язык чата обязан устоять и здесь.
    """
    from src.bot.lang import chat_langs, chat_ui_lang

    monkeypatch.setenv("BOT_UI_LANG", "эльфийский")

    assert chat_ui_lang(CHAT_ID) == "ru"
    assert chat_langs(CHAT_ID) == ("ru", "ru")
