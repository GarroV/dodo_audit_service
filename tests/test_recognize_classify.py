"""T031/T032/T064: разбор комментария (и, по нажатию «Разобрать», кадра) в предложения.

`ask_model` подменяется — сеть здесь не участвует, реальный вызов проверен
вручную (см. журнал блока). Тесты закрепляют то, что контракт требует
буквально: код и зона либо из ответа модели, либо неопределены явно, кандидаты
не досочиняются сверх присланного моделью, `needs_human` поднимается ровно по
трём причинам (нет кандидатов, есть уточняющий вопрос, низкая уверенность или
неизвестная зона).

T064 — отдельный случай этого же `classify()`: комментарий пуст, кадр есть.
Отдельной функции для него нет: контракт `docs/forge/plan.md` даёт один вход
`classify(note, photo, zone_hint)`, кнопку «Разобрать» дожимает бот (T067) —
без нажатия он этот вызов просто не делает. Здесь проверяется то, что
происходит ПОСЛЕ решения бота позвать модель: кадр обязан уйти в запрос, а
не потеряться из-за пустой строки комментария.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.recognize.classify import classify, needs_photo
from src.recognize.client import ModelAnswer
from src.recognize.models import UNKNOWN_ZONE
from src.recognize.shortlist import shortlist


class _Recorder:
    """Подменяет `ask_model`: помнит последний вызов, отдаёт заданный ответ."""

    def __init__(self, payload: dict[str, Any], usage: dict[str, int] | None = None) -> None:
        self.payload = payload
        self.usage = usage or {}
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ModelAnswer:
        self.calls.append(kwargs)
        return ModelAnswer(payload=self.payload, usage=self.usage)

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]


def _patch(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    monkeypatch.setattr("src.recognize.classify.ask_model", recorder)


# --- needs_photo ------------------------------------------------------------


def test_кадр_нужен_только_когда_слов_нет_вовсе(domain_env: Path) -> None:
    """Решение владельца D081: есть комментарий — разбираем комментарий."""
    assert needs_photo("") is True
    assert needs_photo("   ") is True


def test_любой_комментарий_отменяет_кадр(domain_env: Path) -> None:
    """Ни число поднятых пунктов, ни выбор класса на это больше не влияют.

    До D081 кадр не отправлялся только тогда, когда карта слов поднимала один
    пункт с единственным классом. На сегодняшней карте таких комментариев нет
    ни одного: прогон старого правила по боевым `examples/` дал 17 отправок из
    17. Владелец развилку снял: «фото с комментм - обрабатываем коммент».
    """
    assert needs_photo("грязно") is False  # много пунктов — раньше кадр уходил
    assert needs_photo("печь грязная") is False  # один пункт с одним классом
    assert needs_photo("нет маркировки") is False  # один пункт, выбор класса


# --- classify: разбор ответа -------------------------------------------------


def test_запись_разбирается_в_кандидата(domain_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN05:D1",
                    "zone": "hot_kitchen",
                    "wording": "Под лентой печи нагар.",
                    "reason": "видно на кадре",
                    "confidence": 0.9,
                }
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь в нагаре", zone_hint="hot_kitchen")

    # Assert
    assert len(s.candidates) == 1
    c = s.candidates[0]
    assert (c.code, c.level, c.zone) == ("CLN05", "D1", "hot_kitchen")
    assert c.wording == "Под лентой печи нагар."
    assert c.confidence == 0.9
    assert s.needs_human is False, "вопроса нет, уверенность выше порога, зона известна"


def test_несколько_нарушений_в_одном_комментарии_дают_несколько_кандидатов(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: «грязный пол и мусорка переполнена» — правило 11
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN05:D1",
                    "zone": "hot_kitchen",
                    "wording": "a",
                    "reason": "",
                    "confidence": 0.9,
                },
                {
                    "item": "CLN06:D1",
                    "zone": "hot_kitchen",
                    "wording": "b",
                    "reason": "",
                    "confidence": 0.8,
                },
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("пол грязный и мусорка переполнена", zone_hint="hot_kitchen")

    # Assert
    assert [c.code for c in s.candidates] == ["CLN05", "CLN06"]


def test_код_NONE_не_становится_кандидатом(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    recorder = _Recorder({"records": [{"item": "NONE"}], "question": ""})
    _patch(monkeypatch, recorder)

    # Act
    s = classify("всё чисто", zone_hint="hot_kitchen")

    # Assert: пустой список — валидный ответ, а не ошибка разбора
    assert s.candidates == ()
    assert s.needs_human is True


def test_зона_UNKNOWN_подставляется_подсказкой(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: модель честно не увидела зону в словах аудитора
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN05:D1",
                    "zone": UNKNOWN_ZONE,
                    "wording": "x",
                    "reason": "",
                    "confidence": 0.9,
                }
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь грязная", zone_hint="hot_kitchen")

    # Assert: подсказка компенсирует, а не сама модель угадывает
    assert s.candidates[0].zone == "hot_kitchen"


def test_зона_UNKNOWN_без_подсказки_поднимает_needs_human(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN05:D1",
                    "zone": UNKNOWN_ZONE,
                    "wording": "x",
                    "reason": "",
                    "confidence": 0.9,
                }
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь грязная")

    # Assert
    assert s.candidates[0].zone == UNKNOWN_ZONE
    assert s.needs_human is True, "неизвестная зона — человек обязан её назвать"


def test_вопрос_модели_поднимает_needs_human_даже_с_кандидатом(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: правило 7 — не додумывать, спросить в question
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN03:D1",
                    "zone": "hot_kitchen",
                    "wording": "x",
                    "reason": "",
                    "confidence": 0.95,
                }
            ],
            "question": "Это сохранялось после уборки или рабочая грязь?",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("разводы на полу", zone_hint="hot_kitchen")

    # Assert
    assert s.needs_human is True
    assert s.question


def test_низкая_уверенность_поднимает_needs_human(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: порог 0.6 по умолчанию (docs/forge/research/recognize-probe.md — 0.55 у промаха)
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN05:D1",
                    "zone": "hot_kitchen",
                    "wording": "x",
                    "reason": "",
                    "confidence": 0.5,
                }
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь грязная", zone_hint="hot_kitchen")

    # Assert
    assert s.needs_human is True


def test_уверенность_вне_границ_обрезается(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: строгая схема не проверяет границы числа — их обрезает разбор
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "CLN05:D1",
                    "zone": "hot_kitchen",
                    "wording": "x",
                    "reason": "",
                    "confidence": 5,
                }
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь грязная", zone_hint="hot_kitchen")

    # Assert
    assert s.candidates[0].confidence == 1.0


def test_кандидаты_обрезаются_до_max_candidates(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: контракт блока — не больше пяти кнопок
    записи = [
        {
            "item": "CLN05:D1",
            "zone": "hot_kitchen",
            "wording": f"{i}",
            "reason": "",
            "confidence": 0.9,
        }
        for i in range(7)
    ]
    recorder = _Recorder({"records": записи, "question": ""})
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь грязная", zone_hint="hot_kitchen")

    # Assert
    assert len(s.candidates) == 5


def test_пустой_список_записей_это_валидный_ответ(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    recorder = _Recorder({"records": [], "question": ""})
    _patch(monkeypatch, recorder)

    # Act
    s = classify("ничего особенного", zone_hint="hot_kitchen")

    # Assert
    assert s.candidates == ()
    assert s.needs_human is True
    assert s.degraded is False, "пустой ответ модели — не деградация, деградация без модели"


def test_испорченная_запись_в_списке_молча_пропускается(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: строгая схема не даст такого в бою, но разбор не должен упасть
    recorder = _Recorder({"records": ["не словарь", 42], "question": ""})
    _patch(monkeypatch, recorder)

    # Act
    s = classify("печь грязная", zone_hint="hot_kitchen")

    # Assert
    assert s.candidates == ()


# --- classify: когда кадр уходит в запрос (T064 — «Разобрать» без комментария) ---


def test_кадр_без_комментария_уходит_в_модель(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: ровно то, что бот делает по кнопке «Разобрать» (D043, D046) —
    # комментария нет вовсе, есть только кадр
    recorder = _Recorder({"records": [], "question": ""})
    _patch(monkeypatch, recorder)

    # Act
    s = classify("", photo=b"jpeg-bytes", zone_hint="hot_kitchen")

    # Assert
    assert s.used_photo is True
    assert recorder.last["photo"] == b"jpeg-bytes"


def test_без_кадра_и_без_комментария_модель_всё_равно_зовут_без_фото(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: вырожденный случай — вызывающий код передал пустой вход.
    # `classify` не отказывается сам: решение «звать ли вообще» — у бота.
    recorder = _Recorder({"records": [], "question": "Опишите, что видите."})
    _patch(monkeypatch, recorder)

    # Act
    s = classify("", photo=None, zone_hint="hot_kitchen")

    # Assert
    assert s.used_photo is False
    assert recorder.last["photo"] is None


def test_комментарий_с_кадром_разбирается_по_комментарию(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: комментарий есть — значит разбирается он, а кадр в запрос не
    # уходит (D081). До этого решения кадр не отправлялся только при одном
    # поднятом пункте с единственным классом; здесь как раз такой случай —
    # «короб с мукой не закрыт» → ровно PRD12 с единственным классом D1.
    recorder = _Recorder(
        {
            "records": [
                {
                    "item": "PRD12:D1",
                    "zone": "hot_kitchen",
                    "wording": "x",
                    "reason": "",
                    "confidence": 0.9,
                }
            ],
            "question": "",
        }
    )
    _patch(monkeypatch, recorder)
    assert shortlist("короб с мукой не закрыт", "hot_kitchen").cue_hits == ("PRD12",)

    # Act
    s = classify("короб с мукой не закрыт", photo=b"jpeg-bytes", zone_hint="hot_kitchen")

    # Assert
    assert s.used_photo is False
    assert recorder.last["photo"] is None


def test_неоднозначный_комментарий_с_кадром_тоже_разбирается_по_комментарию(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Случай, из-за которого кадр уходил в модель в 16 боевых записях из 17.

    Слово поднимает несколько пунктов — раньше это и было основанием
    отправить картинку («поймать расхождение слов с изображением»). Владелец
    развилку снял решением D081: разбирается комментарий.
    """
    # Arrange
    recorder = _Recorder({"records": [], "question": ""})
    _patch(monkeypatch, recorder)
    assert len(shortlist("грязно", "hot_kitchen").cue_hits) != 1

    # Act
    s = classify("грязно", photo=b"jpeg-bytes", zone_hint="hot_kitchen")

    # Assert
    assert s.used_photo is False
    assert recorder.last["photo"] is None
