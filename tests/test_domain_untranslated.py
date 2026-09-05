"""Методика, написанная не на языке отчёта, печатается не молча (T186, #153).

**Находка.** В боевой методике у трёх пунктов английская колонка процесса
заполнена русским словом — дословно тем же, что и в русской колонке. Английский
отчёт печатает эту колонку как есть (`engine/report.py`, `process_en`), и
партнёр получает документ, в котором одна строка внезапно не на его языке.
Ошибка при этом не выдумана продуктом: он честно печатает то, что стоит в
данных.

**Чего этот файл НЕ делает.** Он не проверяет боевую методику и не считает в ней
дефекты. Данные правит управляющая компания (D002), и тест, падающий от их
правки, красил бы нашу сборку чужой работой — ровно то, от чего уходили в T141.
Поэтому дефект здесь вносится в КОПИЮ синтетической методики, а проверяется
поведение продукта.

**Почему не отказ.** Отказ на сборке отчёта означает, что аудитор уезжает с
точки без документа из-за одного непереведённого слова в справочном поле.
Ошибка не его, исправить он её не может, а партнёру нужен отчёт. Поэтому
выбрана пометка: документ собирается и отдаётся, а рядом с ним аудитор читает,
какие записи напечатаны на чужом языке и что правит это управляющая компания.
Молчания при этом не остаётся — а в журнал стенда уходит то же самое для того,
кто понесёт правку в УК.

**Почему ищется письменность, а не «ru == en».** Совпадение колонок само по
себе законно: «Wi-Fi» и «Dodo IS» одинаковы на обоих языках, а весь демо-набор
англоязычен целиком, и там колонки совпадают в каждой строке. Кириллица же в
английской колонке не бывает правильной никогда. Проверка односторонняя по той
же причине: латиница в русской колонке — обычное дело, и правило «русское поле
обязано быть кириллицей» отсекало бы марки и термины.
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest
from bot_harness import (
    AUDITOR_ID,
    CHAT_ID,
    build_report,
    feed,
    make_bot,
    text_message,
)
from conftest import TEST_DATA

from src.bot.app import build_dispatcher
from src.bot.config import BotSettings
from src.bot.texts import t
from src.domain import add_finding, start_inspection
from src.domain.errors import ConfigError
from src.domain.translation import is_foreign, untranslated

SETTINGS = BotSettings(token="unused-in-tests", allowed_ids=frozenset({AUDITOR_ID}), mode="polling")

#: Пункт, которому в копии методики портится английская колонка процесса.
#: Класс у него единственный (`D2`), зона `cold_kitchen` — запись кладётся
#: прямо, без разбора: проверяется выдача отчёта, а не путь к записи.
BROKEN_CODE = "PRD02"
BROKEN_LEVEL = "D2"
BROKEN_ZONE = "cold_kitchen"

#: Русское слово, которое встанет в английскую колонку. Взято не из методики
#: управляющей компании, а придумано здесь: боевые формулировки в репозиторий
#: не переносятся (`tests/test_methodology_leak.py`).
RUSSIAN_IN_ENGLISH = "Заготовки"


def _rewrite(path: Path, change: dict[str, dict[str, str]]) -> None:
    """Переписать CSV, поменяв поля указанных строк. Порядок колонок сохраняется."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    key = fields[0]
    for row in rows:
        row.update(change.get(row[key], {}))
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def untranslated_data(tmp_path: Path) -> Path:
    """Копия синтетической методики, где английская колонка процесса — русская."""
    data = tmp_path / "untranslated-data"
    shutil.copytree(TEST_DATA, data)
    _rewrite(data / "checklist.csv", {BROKEN_CODE: {"process_en": RUSSIAN_IN_ENGLISH}})
    return data


@pytest.fixture
def untranslated_env(
    untranslated_data: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Окружение блока на этой испорченной копии."""
    state = tmp_path / "state"
    monkeypatch.setenv("AUDIT_DATA_DIR", str(untranslated_data))
    monkeypatch.setenv("STATE_DIR", str(state))
    monkeypatch.chdir(tmp_path)
    return state


# --- сам детектор ------------------------------------------------------------


def test_кириллица_в_английском_поле_считается_чужим_языком() -> None:
    assert is_foreign(RUSSIAN_IN_ENGLISH, "en")


def test_латиница_в_русском_поле_дефектом_не_считается() -> None:
    """«Wi-Fi» в русской колонке — норма, и правило не имеет права на неё падать."""
    assert not is_foreign("Wi-Fi", "ru")
    assert not is_foreign("Dodo IS", "ru")


def test_совпадение_русской_и_английской_колонок_само_по_себе_не_дефект() -> None:
    """Иначе весь англоязычный демо-набор оказался бы сплошным дефектом."""
    assert not is_foreign("Kitchen Operations", "en")


def test_чистая_методика_находок_не_даёт(domain_env: object) -> None:
    assert untranslated("en") == ()
    assert untranslated("ru") == ()


def test_непереведённая_колонка_процесса_находится(untranslated_env: Path) -> None:
    found = untranslated("en")
    assert [(f.code, f.field) for f in found] == [(BROKEN_CODE, "process")], (
        f"дефект английской колонки не найден: {found}"
    )
    assert found[0].text == RUSSIAN_IN_ENGLISH


def test_на_русском_отчёте_то_же_поле_дефектом_не_является(untranslated_env: Path) -> None:
    """Русский отчёт печатает русскую колонку — печатать нечего и предупреждать не о чем."""
    assert untranslated("ru") == ()


def test_проверка_сужается_до_записей_проверки(untranslated_env: Path) -> None:
    """Аудитору называют то, что уйдёт в ЕГО отчёт, а не весь каталог методики."""
    assert untranslated("en", codes=[BROKEN_CODE])
    assert untranslated("en", codes=["PRD01"]) == ()


def test_непереведённое_название_зоны_тоже_находится(
    untranslated_data: Path, untranslated_env: Path
) -> None:
    """Название зоны печатается и в отчёте, и в письме — оно из того же теста."""
    _rewrite(untranslated_data / "zones.csv", {"dough": {"name_en": "Мучной цех"}})
    found = untranslated("en")
    assert ("dough", "title") in [(f.code, f.field) for f in found], found


# --- что видит аудитор -------------------------------------------------------


@pytest.mark.asyncio
async def test_аудитор_предупреждён_и_отчёт_всё_равно_отдан(untranslated_env: Path) -> None:
    """Главное требование задачи: не молча — и не ценой документа на точке."""
    start_inspection(CHAT_ID, "Демо", "planned", "en")
    add_finding(CHAT_ID, BROKEN_CODE, BROKEN_LEVEL, BROKEN_ZONE, "Наблюдение аудитора")

    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/finish"))
    await build_report(dp, bot)

    assert session.documents, "отчёт аудитору не отдан — предупреждение стоило ему документа"
    sent = session.documents[-1].document
    report = Path(str(getattr(sent, "path", sent)))
    assert untranslated_env in report.parents, f"отчёт собран мимо состояния проверки: {report}"
    # Ищется само предупреждение по началу текста, а не код где-нибудь в
    # переписке: код пункта бот и так называет в списке зафиксированного, и
    # проверка «код где-то встретился» оставалась зелёной с выключенным
    # детектором — проверено порчей.
    head = t("finish.untranslated", "ru", lang="?", codes="?").split("(")[0]
    warned = [text for text in session.texts if text.startswith(head)]
    assert warned, f"аудитор не предупреждён, что отчёт напечатает чужой язык: {session.texts[-3:]}"
    assert BROKEN_CODE in warned[0], (
        f"предупреждение не называет код, с которым идти в управляющую компанию: {warned[0]}"
    )


@pytest.mark.asyncio
async def test_на_русском_отчёте_предупреждения_нет(untranslated_env: Path) -> None:
    """Ложное предупреждение хуже отсутствующего: его перестают читать."""
    start_inspection(CHAT_ID, "Демо", "planned", "ru")
    add_finding(CHAT_ID, BROKEN_CODE, BROKEN_LEVEL, BROKEN_ZONE, "Наблюдение аудитора")

    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/finish"))
    await build_report(dp, bot)

    assert session.documents, "отчёт не собрался — тест проверяет не то, что задуман"
    # Сверяется НАЧАЛО текста, а не текст целиком: сравнение с целиком
    # подставленным сообщением зелено и тогда, когда предупреждение показано
    # с другим языком или другими кодами, — то есть ровно на той порче, ради
    # которой этот тест и написан (проверено порчей).
    head = t("finish.untranslated", "ru", lang="?", codes="?").split("(")[0]
    shown = [text for text in session.texts if text.startswith(head)]
    assert not shown, f"предупреждение показано там, где печатать нечего: {shown}"


@pytest.mark.asyncio
async def test_отказ_самой_проверки_не_роняет_сдачу(
    untranslated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """За предупреждением стоят письмо партнёру и слив в историю (T123).

    Проверка языка методики читает диск и вправе отказать. Уронить этим уже
    начатую сдачу нельзя: отчёт у аудитора в руках, а письма он бы не получил.
    """
    start_inspection(CHAT_ID, "Демо", "planned", "en")
    add_finding(CHAT_ID, BROKEN_CODE, BROKEN_LEVEL, BROKEN_ZONE, "Наблюдение аудитора")

    def сломано(*_args: object, **_kw: object) -> tuple[object, ...]:
        raise ConfigError("методика исчезла между сборкой отчёта и проверкой языка")

    monkeypatch.setattr("src.bot.routers.finish.untranslated", сломано)

    bot, session = make_bot()
    dp = build_dispatcher(SETTINGS)
    await feed(dp, bot, text_message("/finish"))
    await build_report(dp, bot)

    assert session.documents, "отчёт не отдан — отказ проверки языка уронил сдачу"
    letter = t("finish.letter", "ru", letter="")
    assert [text for text in session.texts if text.startswith(letter.strip())], (
        f"письмо партнёру не дошло: разговор оборвался на проверке языка: {session.texts[-2:]}"
    )
