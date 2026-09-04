"""Стенд без базы — это законная настройка, а не сбой (T171).

Продукт поднят на боевом сервере с намеренно НЕ поднятой базой: бот проводит
проверку и выдаёт отчёт с письмом, а истории у него нет. Значит инструменты
чтения обязаны отвечать на это внятно.

Две вещи, и обе проверяются здесь общим утверждением по всему каталогу, а не
на одном инструменте:

1. **Пусто и «читать неоткуда» — не одно и то же.** Ответ «проверок не
   найдено» на стенде без базы агент перескажет человеку как факт: проверок у
   точки нет. Это худший из исходов — история цела, её просто негде взять.
2. **Отказ называет причину и переменную.** «Не удалось выполнить проверки
   (ConfigError)» отправляет человека искать поломку, которой нет.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_mcp_no_paths import ГОДНЫЕ

from src.mcp.catalogue import KIND_INSPECTIONS, TOOLS
from src.mcp.rpc import handle

ЧИТАЮЩИЕ = [spec for spec in TOOLS if spec.kind == KIND_INSPECTIONS]


@pytest.fixture(autouse=True)
def без_базы(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)


def _вызов(имя: str, аргументы: dict[str, Any]) -> dict[str, Any]:
    ответ = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": имя, "arguments": аргументы},
        },
        tenant="укашка",
    )
    assert ответ is not None
    return ответ["result"]


def test_каждый_читающий_инструмент_отвечает_отказом_а_не_пустой_выдачей() -> None:
    """Главное утверждение файла: инструмент, добавленный завтра, попадёт сюда
    сам — перебор идёт по каталогу, а не по списку имён."""
    assert ЧИТАЮЩИЕ, "читающие инструменты обязаны быть в каталоге"
    for spec in ЧИТАЮЩИЕ:
        результат = _вызов(spec.name, ГОДНЫЕ[spec.name])
        assert результат.get("isError") is True, spec.name
        текст = результат["content"][0]["text"]
        assert "not found" not in текст, spec.name
        assert "no inspection" not in текст, spec.name


def test_отказ_называет_переменную_и_говорит_что_настройка_законная() -> None:
    """Человеку надо понять, что чинить нечего: базу тут не поднимали."""
    for spec in ЧИТАЮЩИЕ:
        текст = _вызов(spec.name, ГОДНЫЕ[spec.name])["content"][0]["text"]
        assert "DATABASE_URL" in текст, spec.name
        assert "история" in текст.lower(), spec.name


def test_отказ_не_выглядит_поломкой_продукта() -> None:
    """Прежний текст был «Не удалось выполнить проверки (ConfigError)»:
    он одинаков и для не поднятой базы, и для упавшей, — а это разные события,
    и человек по нему шёл искать поломку, которой нет."""
    for spec in ЧИТАЮЩИЕ:
        текст = _вызов(spec.name, ГОДНЫЕ[spec.name])["content"][0]["text"]
        assert "ConfigError" not in текст, spec.name
