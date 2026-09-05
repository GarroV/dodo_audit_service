"""T148: отметка версии методики сверяется с той, которой проверку считают.

Отметку ставили при старте и больше не трогали. Методику между стартом и
подсчётом успевают издать заново (D049 строит хранилище версий ровно затем,
чтобы публикация стала рутиной), а сценарий «прерванная проверка продолжается»
есть в самой спеке. Результат: движок считает по новой методике, отметка в
проверке остаётся старой, `push_inspection` уносит эту пару в базу — и запись
выглядит сравнимой с соседними, не будучи ею.

Решение блока: при подсчёте версия сверяется, расхождение — отказ
(`ChecklistVersionMismatch`), а не тихий пересчёт. Посчитать по новой методике
под старой отметкой запрещает D033. Выход из тупика есть, но только явный:
`sync_checklist_version` переставляет отметку и оставляет след в самой проверке.

**С T169 этот отказ — уже не общий случай, а остаток.** Издание, при котором
проверку начали, снимается снимком рядом с состоянием, и обычная проверка
считается по нему (`tests/test_domain_edition_snapshot.py`). Отказ остаётся там,
где снимка взять негде: проверки, заведённые до T169, и полка снимков, не
пережившая переезд. Поэтому тесты этого файла снимок УБИРАЮТ — иначе они
проверяли бы отказ, которому больше неоткуда взяться.

Ни одной формулировки методики здесь нет намеренно (T146): пункт, зона и класс
берутся из данных на ходу, а правка методики делается припиской в критерии.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.domain import (
    add_finding,
    checklist_version,
    edition,
    get_state,
    list_items,
    score,
    start_inspection,
    sync_checklist_version,
)
from src.domain.config import check_environment
from src.domain.errors import ChecklistVersionMismatch
from src.domain.state import DOMAIN_KEY, HISTORY_KEY

CHAT = 4148


@pytest.fixture
def методика(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Своя копия методики: тест её правит, боевой каталог не трогает."""
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def штрафной_пункт(data_dir: Path) -> tuple[str, str, str]:
    """Пункт, зона и класс с ненулевым вычетом — всё вычитано из методики."""
    ставки = json.loads((data_dir / "scoring.json").read_text(encoding="utf-8"))
    штрафные = {
        уровень for уровень, ставка in (ставки.get("penalty") or {}).items() if float(ставка) > 0
    }
    for пункт in list_items():
        зоны = [z for z in пункт.zones if z != "*"]
        классы = [c for c in пункт.levels if c in штрафные]
        if зоны and классы:
            return пункт.code, зоны[0], классы[0]
    raise AssertionError("в методике не нашлось пункта с зоной и штрафным классом")


def издать_заново(data_dir: Path) -> None:
    """Правка методики, меняющая отпечаток версии: приписка в критериях.

    Ставки не трогаются намеренно: расхождение версий обязано ловиться само по
    себе, а не потому, что заодно поехали цифры.
    """
    криterii = data_dir / "criteria.md"
    криterii.write_text(
        криterii.read_text(encoding="utf-8") + "\n<!-- издание теста -->\n", encoding="utf-8"
    )


def потерять_снимок(chat_id: int = CHAT) -> None:
    """Проверка без снимка своего издания — та, что заведена до T169.

    Ровно это положение отказ и стережёт: издание другое, а взять его негде.
    Убирается именно снимок, а не отметка: отметка обязана остаться, иначе
    сверять будет нечего.
    """
    состояние = get_state(chat_id)
    assert состояние is not None
    снимок = edition.shelf(check_environment()) / состояние.checklist_version
    shutil.rmtree(снимок)


def начать(методика: Path) -> tuple[str, str, str]:
    """Начатая проверка с одной штрафной записью. Возвращает пункт, зону, класс."""
    start_inspection(CHAT, unit="Тестовая", kind="planned", report_lang="ru")
    код, зона, класс = штрафной_пункт(методика)
    add_finding(CHAT, код, класс, зона, "формулировка теста")
    return код, зона, класс


def test_подсчёт_по_переизданной_методике_отказ(методика: Path) -> None:
    """Считать по чужой методике под старой отметкой нельзя (D033)."""
    начать(методика)
    отметка = get_state(CHAT).checklist_version  # type: ignore[union-attr]
    потерять_снимок()
    издать_заново(методика)

    with pytest.raises(ChecklistVersionMismatch) as отказ:
        score(CHAT)

    assert отказ.value.recorded == отметка, "отказ не назвал версию, которой помечена проверка"
    assert отказ.value.current == checklist_version(), "отказ не назвал версию на диске"
    assert отказ.value.recorded != отказ.value.current
    assert отметка in str(отказ.value) and отказ.value.current in str(отказ.value), (
        "в тексте отказа должны стоять обе версии — иначе человеку нечего сверять"
    )


def test_со_снимком_издания_отказа_нет_вовсе(методика: Path) -> None:
    """Граница отказа: он остался ровно там, где издание взять негде (T169).

    Тот же опыт, что и выше, но снимок на месте — и подсчёт идёт молча. Без
    этого теста соседние выглядели бы проверкой отказа, которого на самом деле
    больше не бывает.
    """
    начать(методика)
    издать_заново(методика)

    итог = score(CHAT)

    assert итог.grade, "проверка со снимком своего издания перестала считаться"


def test_отметка_после_отказа_не_переставлена_сама(методика: Path) -> None:
    """Отказ ничего не чинит молча: проверка остаётся помеченной как была."""
    начать(методика)
    отметка = get_state(CHAT).checklist_version  # type: ignore[union-attr]
    потерять_снимок()
    издать_заново(методика)

    with pytest.raises(ChecklistVersionMismatch):
        score(CHAT)

    assert get_state(CHAT).checklist_version == отметка  # type: ignore[union-attr]


def test_методика_на_месте_считается_как_прежде(методика: Path) -> None:
    """Сверка не мешает обычной работе: та же методика — обычный подсчёт."""
    начать(методика)

    итог = score(CHAT)

    assert итог.grade, "оценка не посчиталась на неизменной методике"


def test_проверка_без_отметки_версии_считается(методика: Path) -> None:
    """Проверки, заведённые до версионирования, читаются по-прежнему (DoD блока).

    Сверять там нечего: пустая отметка не означает «другая методика», она
    означает «версию никто не записывал». Отказать здесь — сломать старые
    проверки задним числом, чего D033 как раз и не велит.
    """
    начать(методика)
    файл = методика.parent / "state" / f"chat_{CHAT}" / "inspection.json"
    сырое = json.loads(файл.read_text(encoding="utf-8"))
    сырое[DOMAIN_KEY].pop("checklist_version")
    файл.write_text(json.dumps(сырое, ensure_ascii=False), encoding="utf-8")
    издать_заново(методика)

    итог = score(CHAT)

    assert итог.grade, "проверка без отметки версии перестала считаться"


def test_перестановка_отметки_явная_и_со_следом(методика: Path) -> None:
    """Перезапись отметки — отдельный вызов, и она оставляет след в проверке."""
    начать(методика)
    было = get_state(CHAT).checklist_version  # type: ignore[union-attr]
    издать_заново(методика)
    стало = checklist_version()

    проверка = sync_checklist_version(CHAT)

    assert проверка.checklist_version == стало
    файл = методика.parent / "state" / f"chat_{CHAT}" / "inspection.json"
    след = json.loads(файл.read_text(encoding="utf-8"))[DOMAIN_KEY][HISTORY_KEY]
    assert [(з["from"], з["to"]) for з in след] == [(было, стало)], "след перестановки не записан"
    assert след[0]["at"], "у следа нет времени — по нему нельзя ничего восстановить"


def test_после_перестановки_отметки_подсчёт_идёт(методика: Path) -> None:
    начать(методика)
    издать_заново(методика)
    sync_checklist_version(CHAT)

    итог = score(CHAT)

    assert итог.grade, "после явной перестановки отметки подсчёт так и не пошёл"


def test_перестановка_считает_уже_по_новой_методике(методика: Path) -> None:
    """Перестановка отметки — не косметика: цифра приходит от новых ставок."""
    _, _, класс = начать(методика)
    было = score(CHAT).pct
    потерять_снимок()

    ставки_файл = методика / "scoring.json"
    ставки = json.loads(ставки_файл.read_text(encoding="utf-8"))
    ставки["penalty"][класс] = float(ставки["penalty"][класс]) * 2
    ставки_файл.write_text(json.dumps(ставки, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ChecklistVersionMismatch):
        score(CHAT)
    sync_checklist_version(CHAT)

    assert score(CHAT).pct < было, "вычет не удвоился — считали по старой методике"


def test_перестановка_без_расхождения_следа_не_плодит(методика: Path) -> None:
    """Версия та же — переставлять нечего, и запись в след не появляется."""
    начать(методика)

    sync_checklist_version(CHAT)

    файл = методика.parent / "state" / f"chat_{CHAT}" / "inspection.json"
    блок = json.loads(файл.read_text(encoding="utf-8"))[DOMAIN_KEY]
    assert HISTORY_KEY not in блок, "перестановка на ту же версию оставила пустой след"


def test_расхождение_ловится_и_по_имени_издания(методика: Path) -> None:
    """Отпечаток — не единственная часть версии: имя и дата тоже сверяются."""
    начать(методика)
    потерять_снимок()
    (методика / "checklist_version.txt").write_text("проверка-теста 2026-09-04\n", encoding="utf-8")

    with pytest.raises(ChecklistVersionMismatch):
        score(CHAT)
