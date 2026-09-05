"""T031/T032: правила фиксации зашиты в промпт целиком, не выборочно.

`docs/03-recording-rules.md` — источник правды. `prompt.py` держит текст
правил константой, а не читает документ в рантайме (см. докстринг модуля —
причина в том, чтобы `docs/` не становился обязательной частью образа бота).
Это ловушка: правило можно поправить в документе и забыть перенести в код,
или наоборот. Тест разбирает сам документ и падает при любом расхождении —
номер, заголовок, число правил.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.recognize.config import NO_CHAT
from src.recognize.models import NONE_CODE, UNKNOWN_ZONE
from src.recognize.prompt import RECORDING_RULES, instructions, question_text, rules_text

RULES_DOC = Path(__file__).resolve().parent.parent / "docs" / "03-recording-rules.md"

_HEADING = re.compile(r"^## (\d+)\. (.+)$", re.MULTILINE)


def _doc_rules() -> list[tuple[int, str]]:
    """Номера и заголовки правил из документа, в порядке появления."""
    return [(int(n), title.strip()) for n, title in _HEADING.findall(RULES_DOC.read_text("utf-8"))]


def test_документ_на_месте() -> None:
    assert RULES_DOC.is_file(), f"нет {RULES_DOC} — сверять правила не с чем"


def test_число_и_номера_правил_совпадают_с_документом() -> None:
    doc_numbers = [n for n, _ in _doc_rules()]
    code_numbers = [n for n, _, _ in RECORDING_RULES]

    assert code_numbers == doc_numbers


def test_заголовки_правил_совпадают_с_документом_дословно() -> None:
    doc = dict(_doc_rules())
    code = {n: title for n, title, _ in RECORDING_RULES}

    assert code == doc


def test_ни_одно_правило_не_пустое() -> None:
    for n, title, body in RECORDING_RULES:
        assert title.strip(), f"правило {n} без заголовка"
        assert body.strip(), f"правило {n} без текста"


def test_rules_text_перечисляет_каждое_правило_по_номеру() -> None:
    text = rules_text()

    for n, title, _ in RECORDING_RULES:
        assert f"{n}. {title}" in text


def test_запрещённые_обороты_масштаба_названы_текстом() -> None:
    # Правило 2 — модель должна прочитать запрет как список конкретных фраз,
    # а не как абстрактное «не додумывай масштаб»
    text = rules_text()

    for phrase in (
        "на протяжении нескольких метров",
        "в нескольких местах",
        "по всей ширине",
        "в течение всей смены",
    ):
        assert phrase in text


def test_боевые_ошибки_остаются_в_тексте_правил() -> None:
    # Случай, а не абстрактный запрет, удерживает модель от повторения —
    # так объясняет сам модуль. Если пример исчез при правке, тест это ловит
    text = rules_text()

    assert "на протяжении нескольких метров" in text  # правило 2, ошибка отчёта
    assert "коррозией" in text  # правило 3, ошибка повторилась дважды
    assert "Нареканий нет" in text  # правило 4


def test_instructions_содержит_правила_и_пороги_классов() -> None:
    text = instructions("PRD09 D1 порог из набора тестов")

    assert "ПРАВИЛА ФИКСАЦИИ" in text
    assert "PRD09 D1 порог из набора тестов" in text
    assert rules_text() in text


def test_question_text_без_кадра_говорит_что_кадра_нет() -> None:
    text = question_text("грязно", [], [], None, "ru", with_photo=False, chat_id=NO_CHAT)

    assert "Кадра нет" in text
    assert "грязно" in text


def test_question_text_с_кадром_предупреждает_не_добавлять_из_него_масштаб() -> None:
    text = question_text("грязно", [], [], None, "ru", with_photo=True, chat_id=NO_CHAT)

    assert "приложен кадр" in text
    assert "не добавляй из кадра масштаб" in text


def test_question_text_пустой_комментарий_помечен_явно() -> None:
    # T064: разбор кадра без комментария — модель должна увидеть, что слов
    # аудитора нет вовсе, а не пустую строку без объяснения
    text = question_text("", [], [], "hot_kitchen", "ru", with_photo=True, chat_id=NO_CHAT)

    assert "(пусто)" in text


def test_question_text_кадр_без_комментария_просит_короткие_пункты() -> None:
    # T064/D043: разбор по кнопке «Разобрать» — тон другой, чем при разборе
    # слов аудитора, и требование к формулировке другое: коротко, пунктами,
    # без воды, чтобы аудитор успел подтвердить в моменте
    text = question_text("", [], [], "hot_kitchen", "ru", with_photo=True, chat_id=NO_CHAT)

    assert "нажал «Разобрать»" in text
    assert "коротко" in text
    assert "отдельными записями" in text


def test_question_text_кадр_с_комментарием_не_путается_с_кадром_без_него() -> None:
    # Ветки не должны пересекаться текстом: «нашёл сам» — только когда
    # комментария действительно нет
    без_комментария = question_text(
        "", [], [], "hot_kitchen", "ru", with_photo=True, chat_id=NO_CHAT
    )
    с_комментарием = question_text(
        "печь грязная", [], [], "hot_kitchen", "ru", with_photo=True, chat_id=NO_CHAT
    )

    assert "нажал «Разобрать»" not in с_комментарием
    assert "приложен кадр" not in без_комментария


def test_question_text_подсказка_зоны_и_её_отсутствие() -> None:
    с_подсказкой = question_text(
        "грязно", [], [], "hot_kitchen", "ru", with_photo=False, chat_id=NO_CHAT
    )
    без_подсказки = question_text("грязно", [], [], None, "ru", with_photo=False, chat_id=NO_CHAT)

    assert "hot_kitchen" in с_подсказкой
    assert UNKNOWN_ZONE in без_подсказки


def test_question_text_вариант_none_всегда_в_перечне(domain_env: Path) -> None:
    text = question_text(
        "грязно", ["CLN05:D1"], [], "hot_kitchen", "ru", with_photo=False, chat_id=NO_CHAT
    )

    assert f"{NONE_CODE} — ни один пункт перечня не подходит" in text
