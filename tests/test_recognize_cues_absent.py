"""T157 (D068): карты кадров нет — это деградация, а не отказ.

Противоречие, которое закрывает этот файл, жило между двумя модулями. Методика
считает `photo-cues.md` **необязательным** файлом (`OPTIONAL_DATA_FILES` в
`src/domain/config.py`, и там прямо сказано: отсутствие файла — законное
состояние), поэтому стенд без карты поднимается штатно. А `recognize.cues` при
первом же обращении бросал отказ конфигурации. Файл получался необязательным на
старте и обязательным в работе, и всплывало это не при подъёме стенда, где
чинит человек с доступом к машине, а **в чате у аудитора на первом
комментарии** — ровно так умирало демо (#121).

Решение D068: быстрый путь просто не срабатывает, разбор идёт моделью — как при
любом другом несовпадении условий. Это прямо следует из D063 («не сошлось хотя
бы одно — обычный разбор моделью, без изменений»): карта ускоритель, а не
условие работы продукта.

**Почему тихое сужение перечня тут не появляется.** Опасение из разведки
(`docs/forge/research/recognize-probe.md`) — модель уверенно предлагает похожий
пункт, когда правильного кода в перечне нет, — относится к УСЕЧЁННОМУ перечню.
Карта перечень не режет, а только добавляет коды и переставляет их вперёд
(`shortlist.py`). Без карты в запрос уходит полный зональный перечень, то есть
то же самое, что и при комментарии, не задевшем ни одной строки карты. Ровно
это здесь и утверждается тестом про зональную базу.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from src.domain import list_items
from src.recognize.classify import classify
from src.recognize.client import ModelAnswer
from src.recognize.config import NO_CHAT
from src.recognize.cues import CUES_FILE, class_thresholds, load_cues
from src.recognize.fastpath import NO_CUE, fast_path
from src.recognize.manual import manual_candidates
from src.recognize.shortlist import MANUAL_ONLY, shortlist


@pytest.fixture
def без_карты(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Каталог методики, из которого убрана карта кадров.

    Копия, а не боевой каталог: карта лежит вне git и одна на все рабочие копии
    (D002), удалить её у себя означало бы удалить её у всех.
    """
    карта = data_copy / CUES_FILE
    assert карта.is_file(), "в копии методики нет карты кадров — проверять нечего"
    карта.unlink()
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


class _Recorder:
    """Подменяет `ask_model`: помнит вызов, отдаёт заранее заданный ответ."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> ModelAnswer:
        self.calls.append(kwargs)
        return ModelAnswer(payload={"candidates": []}, usage={})


# --- сам разбор карты --------------------------------------------------------


def test_нет_карты_нет_подсказок_и_нет_отказа(без_карты: Path) -> None:
    assert load_cues(chat_id=NO_CHAT) == ()


def test_нет_карты_пороги_классов_пустые_а_не_отказ(без_карты: Path) -> None:
    assert class_thresholds(chat_id=NO_CHAT) == ""


def test_отсутствие_карты_названо_в_журнале(
    без_карты: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Деградация молчаливой не бывает: чинит стенд человек, и он должен узнать.

    Отказ ушёл из чата аудитора — значит, единственное место, где видно, что
    продукт работает без ускорителя, это журнал.
    """
    with caplog.at_level(logging.WARNING, logger="src.recognize.cues"):
        load_cues(chat_id=NO_CHAT)

    assert caplog.records, "отсутствие карты кадров нигде не отмечено"
    сказано = "\n".join(r.getMessage() for r in caplog.records)
    assert CUES_FILE in сказано, f"в журнале не названо, какого файла нет: {сказано}"


# --- быстрый путь ------------------------------------------------------------


def test_быстрый_путь_без_карты_молчит_а_не_роняет_разбор(без_карты: Path) -> None:
    """D068: «не сошлось» — обычный ответ быстрого пути, и отсутствие карты тоже.

    Причина именно `NO_CUE`, а не отдельная «карты нет»: для замера и для бота
    это один и тот же случай — ни одна строка карты не произнесена целиком.
    """
    итог = fast_path("под конвейерной лентой печи нагар", "hot_kitchen", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == NO_CUE


# --- перечень для модели и для кнопок ----------------------------------------


def test_без_карты_в_запрос_идёт_полный_зональный_перечень(без_карты: Path) -> None:
    """Перечень не режется — он остаётся ровно тем, чем был бы без совпадений.

    Это и есть граница безопасности: опасно УСЕЧЁННЫЙ перечень, а не перечень
    без добавок карты.
    """
    зональные = {i.code for i in list_items(zone="hot_kitchen") if i.kind == "violation"}
    зональные -= set(MANUAL_ONLY)

    итог = shortlist("под конвейерной лентой печи нагар", zone_hint="hot_kitchen", chat_id=NO_CHAT)

    assert итог.cue_hits == (), "подсказкам взяться неоткуда — карты нет"
    assert зональные <= set(итог.codes)
    # Страховка от вырождения: если бы зоне досталась пара пунктов, включение
    # «зональные <= коды» выполнялось бы само собой и ничего не стерегло. Порог
    # выводится из методики, а не вписан числом: вписанное число — то самое,
    # что ломалось от чужой правки данных (T141).
    предлагаемые = {i.code for i in list_items() if i.kind == "violation"} - set(MANUAL_ONLY)
    assert len(зональные) > len(предлагаемые) // 2, (
        "перечень зоны выродился — проверка «карта не режет базу» стала бы пустой"
    )


def test_ручной_перечень_кнопками_без_карты_собирается(без_карты: Path) -> None:
    """Деградация T034 не должна падать вместе с ускорителем.

    До T157 было именно так: `manual_candidates` идёт через `shortlist`, а тот
    читал карту — то есть на стенде без карты не работал и запасной путь.
    """
    зональные = {i.code for i in list_items(zone="hot_kitchen") if i.kind == "violation"}

    итог = manual_candidates("hot_kitchen", chat_id=NO_CHAT)

    assert {c.code for c in итог} == зональные


def test_разбор_моделью_без_карты_доходит_до_модели(
    без_карты: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное утверждение задачи: разбор идёт дальше, а не обрывается отказом."""
    recorder = _Recorder()
    monkeypatch.setattr("src.recognize.classify.ask_model", recorder)

    итог = classify("под конвейерной лентой печи нагар", None, "hot_kitchen", chat_id=NO_CHAT)

    assert recorder.calls, "модель не позвана — разбор оборвался там, где обязан был продолжиться"
    assert итог.candidates == ()
