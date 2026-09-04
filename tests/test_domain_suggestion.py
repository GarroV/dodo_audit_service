"""T181 (D077): предложение системы живёт рядом с записью, а не теряется.

Владелец, дословно: «при несостыковках, или если пользователь добавит что-то в
духе "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА" то мы долполняем наш список
терминов». Пополняет список человек — управляющая компания (D077 запрещает
автоматическое пополнение прямо). Но пополнять нечего, пока промах не сохранён,
а до этой задачи сигнал терялся целиком: ни предложенного пункта, ни
уверенности, ни того, что аудитор в предложении поменял.

Что здесь защищается — три вещи.

**Предложение хранится рядом с записью и переживает перечитывание состояния.**
Тем же приёмом, что источник записи (D044, T108): отдельный блок в файле
проверки, привязанный к номеру записи. Второго способа не заводится намеренно.

**«Что аудитор поправил» отдельно НЕ хранится.** Это сравнение предложенной
тройки с итоговой, обе лежат в одной строке базы. Третья копия разъехалась бы с
парой, из которой считается, и разъехалась бы молча.

**Непонятное значение — отказ, а не запись.** Тот же довод, что у
`read_sources`: прочитанное «неизвестно что» уехало бы в базу и стало бы там
неотличимо от настоящего предложения модели, а по этой выборке управляющая
компания правит боевой список слов.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import requires_data

from src.domain import (
    SOURCE_COMMENT,
    Suggestion,
    add_finding,
    drop_finding,
    edit_finding,
    get_state,
    start_inspection,
)
from src.domain.config import check_environment
from src.domain.errors import DomainError, ValidationError
from src.domain.state import state_file

pytestmark = requires_data

CHAT = 5150


def начата() -> None:
    start_inspection(CHAT, "Белград 2", "planned", "ru", date="2026-09-04", auditor="Гарро")


def файл() -> Path:
    return state_file(CHAT, check_environment())


def блок() -> dict:
    return json.loads(файл().read_text(encoding="utf-8"))["domain"]


# --- предложение доезжает до записи и переживает перечитывание ---------------


def test_предложение_читается_обратно_вместе_с_записью(domain_env: Path) -> None:
    """Четыре поля — те самые, которые слив берёт с записи по имени."""
    начата()
    add_finding(
        CHAT,
        "CLN05",
        "D1",
        "hot_kitchen",
        "нагар на подине печи",
        source=SOURCE_COMMENT,
        suggested=Suggestion(code="CLN02", level="D2", zone="dining", confidence=0.42),
    )

    state = get_state(CHAT)
    assert state is not None
    (запись,) = state.findings
    assert запись.suggested_code == "CLN02"
    assert запись.suggested_level == "D2"
    assert запись.suggested_zone == "dining"
    assert запись.suggested_confidence == pytest.approx(0.42)


def test_без_предложения_поля_пусты_а_не_повторяют_запись(domain_env: Path) -> None:
    """Ручная запись — это «модель не предлагала ничего», а не «предложила то же».

    Слив различает их: пусто становится `NULL`, и в выборке для управляющей
    компании такая строка не выглядит попаданием модели.
    """
    начата()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_COMMENT)

    state = get_state(CHAT)
    assert state is not None
    (запись,) = state.findings
    assert (запись.suggested_code, запись.suggested_level, запись.suggested_zone) == ("", "", "")
    assert запись.suggested_confidence is None


def test_уверенности_может_не_быть_а_пункт_остаётся(domain_env: Path) -> None:
    """Сверка со списком слов уверенности не считает, и ноль тут был бы ложью.

    «Система ни в чём не уверена» — осмысленное утверждение, и подставить его
    вместо «уверенность не измерялась» значит соврать по всей выборке.
    """
    начата()
    add_finding(
        CHAT,
        "CLN05",
        "D1",
        "hot_kitchen",
        "нагар",
        source=SOURCE_COMMENT,
        suggested=Suggestion(code="CLN05", level="D1", zone="hot_kitchen"),
    )

    state = get_state(CHAT)
    assert state is not None
    assert state.findings[0].suggested_code == "CLN05"
    assert state.findings[0].suggested_confidence is None


# --- правка аудитора видна сравнением, а не третьей копией -------------------


def test_правка_зоны_не_трогает_предложение(domain_env: Path) -> None:
    """Главный случай задачи: аудитор поправил — расхождение стало видно.

    Предложение обязано остаться прежним: именно разница между ним и итоговой
    записью и есть сигнал для пополнения списка терминов.
    """
    начата()
    add_finding(
        CHAT,
        "CLN05",
        "D1",
        "hot_kitchen",
        "нагар",
        source=SOURCE_COMMENT,
        suggested=Suggestion(code="CLN05", level="D1", zone="hot_kitchen", confidence=0.8),
    )
    edit_finding(CHAT, 1, zone="dining")

    state = get_state(CHAT)
    assert state is not None
    (запись,) = state.findings
    assert запись.zone == "dining", "правка зоны не применилась — сравнивать нечего"
    assert запись.suggested_zone == "hot_kitchen", "правка переписала предложение системы"


def test_удалённая_запись_уносит_своё_предложение(domain_env: Path) -> None:
    """Иначе следующая запись с тем же номером унаследует чужое предложение."""
    начата()
    add_finding(
        CHAT,
        "CLN05",
        "D1",
        "hot_kitchen",
        "нагар",
        source=SOURCE_COMMENT,
        suggested=Suggestion(code="CLN02", level="D2", zone="dining"),
    )
    drop_finding(CHAT, 1)
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_COMMENT)

    state = get_state(CHAT)
    assert state is not None
    (запись,) = state.findings
    assert запись.suggested_code == "", "новая запись получила предложение удалённой"
    assert "1" not in блок().get("suggestions", {})


# --- непонятное значение не записывается и не читается -----------------------


def test_предложение_без_пункта_не_принимается(domain_env: Path) -> None:
    """Предложение без кода — это не предложение, а три четверти пустой строки.

    Слив кладёт пустое в `NULL`, и такая запись выглядела бы в базе как «модель
    промолчала, но зачем-то назвала зону».
    """
    начата()
    with pytest.raises(ValidationError, match="пункт"):
        add_finding(
            CHAT,
            "CLN05",
            "D1",
            "hot_kitchen",
            "нагар",
            suggested=Suggestion(code="", level="D1", zone="hot_kitchen"),
        )
    state = get_state(CHAT)
    assert state is not None
    assert state.findings == [], "запись легла, хотя предложение к ней не разобрано"


def test_чужая_шкала_уверенности_не_принимается(domain_env: Path) -> None:
    """Проценты вместо доли обесценили бы порог отбора на всей выборке разом."""
    начата()
    with pytest.raises(ValidationError, match="доля"):
        add_finding(
            CHAT,
            "CLN05",
            "D1",
            "hot_kitchen",
            "нагар",
            suggested=Suggestion(code="CLN05", level="D1", zone="hot_kitchen", confidence=80),
        )
    state = get_state(CHAT)
    assert state is not None
    assert state.findings == [], "запись легла с уверенностью в чужой шкале"


def test_испорченное_предложение_в_файле_читается_отказом(domain_env: Path) -> None:
    """Не разобрали — отказ, а не молчаливая подстановка пустоты.

    Пустота означает «модель не предлагала ничего», и выдать за неё непрочитанное
    значит спрятать промах ровно там, где его собирались искать.
    """
    начата()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_COMMENT)
    raw = json.loads(файл().read_text(encoding="utf-8"))
    raw["domain"]["suggestions"] = {
        "1": {"code": "CLN02", "level": "D2", "zone": "dining", "confidence": "почти уверен"}
    }
    файл().write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DomainError, match="Уверенность"):
        get_state(CHAT)


def test_предложение_не_похожее_на_предложение_читается_отказом(domain_env: Path) -> None:
    """Строка вместо тройки полей — чужая структура, и молча пропускать её нельзя."""
    начата()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_COMMENT)
    raw = json.loads(файл().read_text(encoding="utf-8"))
    raw["domain"]["suggestions"] = {"1": "CLN02"}
    файл().write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DomainError, match="предложение"):
        get_state(CHAT)


def test_старая_проверка_без_блока_предложений_читается_как_прежде(domain_env: Path) -> None:
    """Проверки, начатые до задачи, обязаны читаться: незнакомого ключа там нет.

    Отказ означал бы, что начатую вчера проверку стало нечем завершить, — тот
    же довод, по которому переезд источника записи (T108) не ломал старые.
    """
    начата()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_COMMENT)
    raw = json.loads(файл().read_text(encoding="utf-8"))
    raw["domain"].pop("suggestions", None)
    файл().write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    state = get_state(CHAT)
    assert state is not None
    assert state.findings[0].suggested_code == ""
    assert state.findings[0].source == SOURCE_COMMENT
