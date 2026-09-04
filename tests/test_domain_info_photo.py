"""T179: кадр информационного поля доезжает до движка и читается обратно.

Половина цепочки, живущая в блоке `domain`. Движок с T172 умеет держать поле
парой «текст + кадры» (`audit.py info --photo`), но `set_info` о кадрах не знал,
и передать их было нечем: бот в движок напрямую не ходит.

Вторая половина — чтение. Ссылку на кадр надо не только записать, но и увидеть
в проверке: по ней блок `report` строит карту «ссылка → файл», а без карты
идентификатор телеграма уедет в движок путём и напечатается красной отметкой
«фотография не приложена». Поэтому здесь же проверяется `Inspection.info`.

Проверки, заведённые до T172, хранят поле строкой. Отказываться читать их
нельзя: это обычный JSON на диске, начатую тогда проверку нечем было бы
завершить.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain import get_state, set_info, start_inspection
from src.domain.config import check_environment
from src.domain.engine import state_file
from src.domain.errors import EngineError, ValidationError

CHAT = 793_000_179


def _начатая() -> None:
    start_inspection(
        CHAT,
        unit="Проверка кадра поля",
        kind="planned",
        report_lang="ru",
        date="2026-09-04",
        auditor="Тест",
    )


def _файл() -> Path:
    return state_file(CHAT, check_environment())


def _поле(code: str) -> object:
    raw = json.loads(_файл().read_text(encoding="utf-8"))
    return dict(raw.get("info") or {}).get(code)


def test_кадр_поля_записывается_рядом_с_текстом(domain_env: Path) -> None:
    """То, ради чего задача заведена: кадр обязан доехать до состояния."""
    _начатая()
    set_info(CHAT, "INF01", "витрина собрана как надо", photos=["AgACfoto1"])

    assert _поле("INF01") == {
        "text": "витрина собрана как надо",
        "photos": ["AgACfoto1"],
    }


def test_поле_читается_проверкой_вместе_с_кадром(domain_env: Path) -> None:
    """Записать мало: по этой ссылке блок `report` строит карту кадров."""
    _начатая()
    set_info(CHAT, "INF01", "витрина собрана", photos=["AgACfoto1", "AgACfoto2"])

    state = get_state(CHAT)
    assert state is not None
    поле = state.info["INF01"]
    assert поле.code == "INF01"
    assert поле.text == "витрина собрана"
    assert поле.photos == ["AgACfoto1", "AgACfoto2"]


def test_поле_старой_проверки_строкой_читается(domain_env: Path) -> None:
    """До T172 поле хранилось строкой — такую проверку нельзя объявить битой."""
    _начатая()
    path = _файл()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["info"] = {"INF06": "старое поле строкой"}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    state = get_state(CHAT)
    assert state is not None
    assert state.info["INF06"].text == "старое поле строкой"
    assert state.info["INF06"].photos == []


def test_поле_без_кадра_остаётся_полем_без_кадра(domain_env: Path) -> None:
    """Встречное утверждение: кадры не появляются сами."""
    _начатая()
    set_info(CHAT, "INF01", "без кадра")

    state = get_state(CHAT)
    assert state is not None
    assert state.info["INF01"].photos == []


def test_правка_расшифровки_кадр_не_уносит(domain_env: Path) -> None:
    """Голос правят повторной записью поля (D069) — кадр к правке отношения не имеет."""
    _начатая()
    set_info(CHAT, "INF01", "услышанное", photos=["AgACfoto1"])
    set_info(CHAT, "INF01", "поправленное словами")

    state = get_state(CHAT)
    assert state is not None
    assert state.info["INF01"].text == "поправленное словами"
    assert state.info["INF01"].photos == ["AgACfoto1"], "правка текста унесла кадр"


def test_запятая_в_ссылке_на_кадр_отклоняется(domain_env: Path) -> None:
    """Движок режет список кадров по запятой — один кадр стал бы двумя пропавшими."""
    _начатая()
    with pytest.raises(ValidationError) as exc:
        set_info(CHAT, "INF01", "текст", photos=["AgAC123,AgAC456"])

    assert "запятая" in str(exc.value)
    assert _поле("INF01") is None, "поле записано при отказе"


def test_пустая_ссылка_на_кадр_отклоняется(domain_env: Path) -> None:
    """Пустая ссылка молча превратилась бы в поле без кадра."""
    _начатая()
    with pytest.raises(ValidationError) as exc:
        set_info(CHAT, "INF01", "текст", photos=[""])

    assert "INF01" in str(exc.value)
    assert _поле("INF01") is None, "поле записано при отказе"


def test_успех_движка_без_кадра_это_отказ(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тихая потеря кадра и есть дефект, ради которого задача заведена.

    Молчаливый успех здесь опаснее падения: бот сказал бы «кадр приложен», а
    партнёр получил бы поле без снимка — и узнали бы об этом уже после отчёта.
    """
    _начатая()
    monkeypatch.setattr("src.domain.info.run_audit", lambda *a, **k: "ок")

    with pytest.raises(EngineError) as exc:
        set_info(CHAT, "INF01", "текст", photos=["AgACfoto1"])

    assert "AgACfoto1" in str(exc.value), "в отказе не названо, какой кадр не доехал"
