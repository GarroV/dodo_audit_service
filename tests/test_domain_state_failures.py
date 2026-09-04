"""Пути отказа состояния проверки: то, что не проверял ни один тест.

Найдено при сверке 03.09: в `src/domain/state.py` непокрытыми оставались ровно
пути отказа — таймаут блокировки, испорченный JSON не той формы, уборка
временного файла при сбое записи, отказ языка. Замер 61 дефекта, прорвавшегося
мимо тестов на прошлых прогонах, дал права 23%, а «проверку, которая не могла
упасть» — 19%; счастливый путь этот класс не видит по определению.

Таймаут блокировки здесь не абстрактный: это ровно то место, куда упрётся
продукт под нагрузкой (задача T101, решение D058) — две проверки, пишущие
состояние одновременно.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from src.domain.errors import DomainError, ValidationError
from src.domain.state import _clean_lang, _finding, _read_raw, _write_atomic, state_lock


def test_блокировка_отказывает_а_не_ждёт_вечно(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Занятое состояние даёт внятный отказ, а не зависший навсегда бот.

    Ожидание укорочено до полусекунды: смысл проверки в том, что путь отказа
    вообще достижим и называет причину, а не в том, сколько именно секунд
    продукт терпит затор.
    """
    monkeypatch.setattr("src.domain.state.LOCK_TIMEOUT_SEC", 0.5)
    path = tmp_path / "inspection.json"
    holder_took = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with state_lock(path):
            holder_took.set()
            release.wait(timeout=10)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert holder_took.wait(timeout=10), "первый держатель блокировки не стартовал"
        with pytest.raises(DomainError) as exc:
            with state_lock(path):
                pytest.fail("блокировка выдана дважды — состояние можно испортить")
        assert "занято другим процессом" in str(exc.value)
        assert "запись отменена" in str(exc.value)
    finally:
        release.set()
        holder.join(timeout=10)


def test_блокировка_освобождается_и_следующий_проходит(tmp_path: Path) -> None:
    """Отказ по таймауту не должен оставлять состояние заблокированным навсегда."""
    path = tmp_path / "inspection.json"
    with state_lock(path):
        pass
    with state_lock(path):
        pass


def test_состояние_не_той_формы_отклоняется(tmp_path: Path) -> None:
    """JSON разобрался, но это список, а не проверка — молча принимать нельзя."""
    path = tmp_path / "inspection.json"
    path.write_text(json.dumps([{"n": 1}]), encoding="utf-8")
    with pytest.raises(DomainError) as exc:
        _read_raw(path)
    assert "не похоже на проверку" in str(exc.value)


def test_сбой_записи_не_оставляет_временный_файл(tmp_path: Path) -> None:
    """Оборванная запись не должна засорять папку проверки обломками.

    Папка на чат живёт долго, и `.inspection-*.tmp` от каждого сбоя копился бы
    в ней незаметно: место занято, а глазами никто туда не смотрит.
    """
    path = tmp_path / "inspection.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    class Неписуемое:
        """Значение, на котором json.dump падает уже после создания файла."""

    with pytest.raises(TypeError):
        _write_atomic(path, {"meta": Неписуемое()})

    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".inspection-")]
    assert leftovers == [], f"временные файлы остались: {leftovers}"
    assert not path.exists(), "состояние создано из проваленной записи"


def test_язык_не_кодом_отклоняется() -> None:
    """Язык — параметр, но не любая строка: «русский» не код языка."""
    with pytest.raises(ValidationError) as exc:
        _clean_lang("русский", "язык интерфейса")
    assert "не похож на код языка" in str(exc.value)
    assert "язык интерфейса" in str(exc.value), "в отказе не названо поле — чинить вслепую"


def test_старая_форма_записи_кадра_читается() -> None:
    """Проверки, заведённые до перехода на список кадров, не должны терять фото."""
    f = _finding(
        {"n": 1, "qid": "CLN05", "level": "D1", "zone": "hot_kitchen", "photo": "AgAC123"},
        {},
    )
    assert f.photos == ["AgAC123"], "кадр старой формы потерян при чтении"
    assert f.source == "", "у записи, заведённой до D044, источника нет и выдумывать его нельзя"
