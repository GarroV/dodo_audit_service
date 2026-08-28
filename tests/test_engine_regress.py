"""Регрессионный якорь конституции: цифры на боевых данных не меняются.

То же, что делает `make regress`, но внутри `make check` — чтобы расхождение
ловилось обычным прогоном тестов, а не только отдельной командой.
"""

from __future__ import annotations

import json

import pytest
from conftest import AUDIT, EXAMPLES, requires_data, requires_examples, run_engine

pytestmark = [requires_data, requires_examples]

ANCHORS = [
    ("belgrade-1", 97.5, "A", {"D1": 5, "D2": 0, "D3": 0}),
    ("belgrade-2", 97.0, "A", {"D1": 6, "D2": 0, "D3": 0}),
]


@pytest.mark.parametrize(("name", "pct", "grade", "counts"), ANCHORS)
def test_боевая_проверка_считается_как_прежде(
    name: str, pct: float, grade: str, counts: dict[str, int]
) -> None:
    d = EXAMPLES / name
    r = run_engine(AUDIT, "score", "--json", cwd=d, state=d / "inspection.json")
    assert r.code == 0, r.text
    res = json.loads(r.out)
    assert res["pct"] == pct, f"{name}: процент разошёлся — это регрессия, а не уточнение"
    assert res["grade"] == grade, f"{name}: буква оценки разошлась"
    assert {k: res["counts"][k] for k in counts} == counts, f"{name}: разбивка по классам разошлась"
