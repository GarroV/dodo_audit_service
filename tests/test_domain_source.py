"""T065: источник записи — со слов аудитора или распознан по кадру (D044).

За формулировку, сказанную аудитором, отвечает аудитор; за догадку по картинке
не отвечает никто, и перед отправкой партнёру это должно быть видно. Значит
источник обязан жить в самой проверке, а не рядом с ней: заметки бота
обнуляются на старте следующей проверки того же чата, и через месяц ответить,
чья это была формулировка, стало бы нечем.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain import (
    SOURCE_COMMENT,
    SOURCE_PHOTO,
    add_finding,
    check_environment,
    drop_finding,
    edit_finding,
    get_state,
    start_inspection,
)
from src.domain.errors import DomainError, ValidationError
from src.domain.state import DOMAIN_KEY, SOURCES_KEY, state_file

CHAT = 65


def начать() -> None:
    start_inspection(CHAT, unit="Белград-1", kind="Плановая", report_lang="ru")


def сырое() -> dict[str, object]:
    return json.loads(state_file(CHAT, check_environment()).read_text(encoding="utf-8"))


def записать(сырьё: dict[str, object]) -> None:
    state_file(CHAT, check_environment()).write_text(
        json.dumps(сырьё, ensure_ascii=False), encoding="utf-8"
    )


def запись(n: int) -> object:
    состояние = get_state(CHAT)
    assert состояние is not None, "проверка исчезла из состояния"
    return состояние.finding(n)


def test_распознанная_по_кадру_запись_помнит_это(domain_env: Path) -> None:
    начать()

    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар на печи", source=SOURCE_PHOTO)

    найдено = запись(1)
    assert найдено is not None
    assert найдено.source == SOURCE_PHOTO


def test_источник_со_слов_аудитора_отличим_от_догадки(domain_env: Path) -> None:
    начать()

    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_COMMENT)
    add_finding(CHAT, "CLN05", "D1", "cold_kitchen", "нагар", source=SOURCE_PHOTO)

    assert [запись(1).source, запись(2).source] == [SOURCE_COMMENT, SOURCE_PHOTO]  # type: ignore[union-attr]


def test_запись_без_источника_остаётся_без_него(domain_env: Path) -> None:
    """Проверки, заведённые до D044, не должны выглядеть как слова аудитора."""
    начать()

    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар")

    assert запись(1).source == ""  # type: ignore[union-attr]


def test_источник_лежит_в_проверке_а_не_рядом_с_ней(domain_env: Path) -> None:
    """Проверка — один объект: при сливе в базу источник обязан уехать вместе с ней."""
    начать()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_PHOTO)

    сырьё = сырое()

    assert сырьё[DOMAIN_KEY][SOURCES_KEY] == {"1": SOURCE_PHOTO}  # type: ignore[index]


def test_источник_не_попадает_в_отчёт_партнёру(domain_env: Path) -> None:
    """Источник — внутренняя кухня: в полях движка, которые печатаются, его нет."""
    начать()

    add_finding(
        CHAT, "CLN05", "D1", "hot_kitchen", "нагар", comment="сказал вслух", source=SOURCE_PHOTO
    )

    у_движка = сырое()["findings"][0]  # type: ignore[index]
    assert "source" not in у_движка, "источник дописан в запись движка — он печатается партнёру"
    assert SOURCE_PHOTO not in у_движка.values()
    assert у_движка["comment"] == "сказал вслух", "комментарий аудитора трогать нельзя"


def test_чужой_источник_отвергается_до_фиксации(domain_env: Path) -> None:
    """Отказ до вызова движка: иначе запись есть, а источник неизвестен."""
    начать()

    with pytest.raises(ValidationError) as отказ:
        add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source="догадка")

    assert "догадка" in str(отказ.value)
    состояние = get_state(CHAT)
    assert состояние is not None
    assert состояние.findings == [], "запись с непонятным источником всё-таки завелась"


def test_удаление_записи_забывает_источник(domain_env: Path) -> None:
    начать()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_PHOTO)

    drop_finding(CHAT, 1)

    assert сырое()[DOMAIN_KEY].get(SOURCES_KEY) == {}  # type: ignore[union-attr]


def test_правка_записи_источник_не_теряет(domain_env: Path) -> None:
    """Аудитор поправил зону — запись осталась той же, и догадкой она быть не перестала."""
    начать()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_PHOTO)

    edit_finding(CHAT, 1, zone="cold_kitchen")

    assert запись(1).source == SOURCE_PHOTO  # type: ignore[union-attr]


def test_источник_не_правится_как_обычное_поле(domain_env: Path) -> None:
    """Источник ставится при фиксации и живёт с записью: переписать его нельзя."""
    начать()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_PHOTO)

    with pytest.raises(ValidationError):
        edit_finding(CHAT, 1, source=SOURCE_COMMENT)


def test_непонятный_источник_в_состоянии_отказывает(domain_env: Path) -> None:
    """Прочитанное «неизвестно что» уехало бы в базу как источник записи."""
    начать()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "нагар", source=SOURCE_PHOTO)
    сырьё = сырое()
    сырьё[DOMAIN_KEY][SOURCES_KEY]["1"] = "нейронка"  # type: ignore[index]
    записать(сырьё)

    with pytest.raises(DomainError) as отказ:
        get_state(CHAT)

    assert "нейронка" in str(отказ.value)
