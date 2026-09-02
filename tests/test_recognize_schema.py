"""T031: строгая схема ответа — код и класс приходят перечислением.

Контракт блока требует, чтобы код был «строго из переданного перечня —
гарантируется схемой ответа, а не проверкой постфактум», а класс — «только из
разрешённых для этого пункта». Здесь проверяется именно это свойство схемы:
любое значение поля `item`, которое схема допускает, разбирается в пару
«код из перечня, класс из методики».
"""

from __future__ import annotations

import pytest
from conftest import requires_data

from src.domain import allowed_levels
from src.recognize.errors import RecognizeConfigError
from src.recognize.models import NONE_CODE, UNKNOWN_ZONE
from src.recognize.schema import NONE_PICK, picks_for, response_schema, split_pick
from src.recognize.shortlist import shortlist

pytestmark = requires_data


def test_на_каждый_допустимый_класс_своё_значение(domain_env: object) -> None:
    # Arrange: PRD09 — пункт с выбором класса, CLN05 — с единственным
    codes = ("PRD09", "CLN05")

    # Act
    picks = picks_for(codes)

    # Assert
    assert picks == ("PRD09:D1", "PRD09:D2", "PRD09:D3", "CLN05:D1", NONE_PICK)


def test_отказ_всегда_последний_и_единственный(domain_env: object) -> None:
    # Act
    picks = picks_for(shortlist("нагар", "hot_kitchen").codes)

    # Assert
    assert picks[-1] == NONE_PICK
    assert picks.count(NONE_PICK) == 1


def test_порядок_кодов_сохраняется(domain_env: object) -> None:
    # Arrange: карта кадров поднимает CLN05 в начало перечня
    picked = shortlist("нагар под конвейерной лентой печи", "hot_kitchen")

    # Act
    picks = picks_for(picked.codes)

    # Assert
    assert picks[0].startswith(picked.codes[0])
    assert [p.split(":")[0] for p in picks[:-1]] == [
        c for c in picked.codes for _ in allowed_levels(c)
    ]


def test_пункт_без_классов_это_отказ_а_не_пропуск(domain_env: object) -> None:
    # Arrange: INF01 заведён как служебный, допустимых классов у него нет

    # Act / Assert
    with pytest.raises(RecognizeConfigError, match="INF01"):
        picks_for(("CLN05", "INF01"))


def test_разбор_значения_на_код_и_класс(domain_env: object) -> None:
    # Act / Assert
    assert split_pick("PRD09:D2") == ("PRD09", "D2")
    assert split_pick(NONE_PICK) == (NONE_CODE, "")


def test_любое_допустимое_значение_разбирается_в_пару_из_методики(domain_env: object) -> None:
    # Arrange
    picks = picks_for(shortlist("грязь", "dining").codes)

    # Act / Assert: свойство, ради которого схема и построена
    for pick in picks:
        if pick == NONE_PICK:
            continue
        code, level = split_pick(pick)
        assert level in allowed_levels(code), f"{pick} даёт класс вне методики"


def test_схема_перечисляет_ровно_переданный_перечень(domain_env: object) -> None:
    # Arrange
    picks = picks_for(("CLN05", "PRD09"))

    # Act
    schema = response_schema(picks, ["hot_kitchen", "dining"])

    # Assert
    item = schema["properties"]["records"]["items"]["properties"]["item"]
    assert item["enum"] == list(picks)
    zone = schema["properties"]["records"]["items"]["properties"]["zone"]
    assert zone["enum"] == ["hot_kitchen", "dining", UNKNOWN_ZONE]


def test_схема_годится_для_строгого_режима(domain_env: object) -> None:
    # Arrange
    schema = response_schema(picks_for(("CLN05",)), ["hot_kitchen"])

    # Act
    record = schema["properties"]["records"]["items"]

    # Assert: строгий режим требует запрета лишних полей и полного `required`
    assert schema["additionalProperties"] is False
    assert record["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert set(record["required"]) == set(record["properties"])


def test_в_схеме_нет_ключей_которые_строгий_режим_молча_игнорирует(domain_env: object) -> None:
    # Arrange
    schema = response_schema(picks_for(("PRD09",)), ["dough"])

    # Act
    def keys(node: object) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        if isinstance(node, list):
            return {k for v in node for k in keys(v)}
        return set()

    # Assert: заявленная, но неподдерживаемая проверка хуже отсутствующей —
    # она выглядит существующей. Число кандидатов и границы уверенности
    # проверяет разбор ответа.
    assert not keys(schema) & {"maxItems", "minItems", "minimum", "maximum", "pattern"}
