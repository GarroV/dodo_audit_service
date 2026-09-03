"""Дорогие вызовы домена не должны идти в цикле событий бота (T101, T107).

Движок вызывается подпроцессом, и это десятки миллисекунд на вызов. Пока такой
вызов идёт в цикле событий, бот не обслуживает НИКОГО — ни других аудиторов, ни
таймеры альбомов. Замер T101: одно подтверждение записи стоило 47 мс
(`add_finding` 27 + `score` 26), и очередь росла линейно — двадцать аудиторов,
секунда последнему.

Проверка здесь статическая, и это сказано прямо. Поведенческий тест на
отзывчивость цикла событий пришлось бы строить вокруг подменённого движка с
искусственной задержкой — он доказывал бы, что задержка не блокирует, а не то,
что её нет в бою. Инвариант же формулируется точно: в обработчиках эти функции
зовутся только через `asyncio.to_thread`. Регрессию он ловит — снятие любой
обёртки роняет тест адресно.

`get_state` в список не входит намеренно: это чтение файла, 0.1 мс, обёртка
стоила бы дороже самой операции.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTERS = Path(__file__).resolve().parents[1] / "src" / "bot" / "routers"

#: Функции домена, которые ходят в движок подпроцессом. Замерено (T101):
#: add_finding 27 мс, attach_photo 24 мс, score 26 мс.
BLOCKING = frozenset(
    {"add_finding", "attach_photo", "edit_finding", "drop_finding", "score", "start_inspection"}
)


def _called_name(node: ast.Call) -> str | None:
    """Имя вызываемого: `domain.score(...)` → `score`, `score(...)` → `score`."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _direct_blocking_calls(source: str) -> list[tuple[str, int]]:
    """Вызовы дорогих функций, сделанные НЕ через `asyncio.to_thread`."""
    tree = ast.parse(source)
    # Имена, переданные в to_thread аргументом: `to_thread(domain.score, ...)`.
    # Это как раз правильная форма, и вызовом она не является.
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in BLOCKING:
            found.append((name, node.lineno))
    return found


@pytest.mark.parametrize("path", sorted(ROUTERS.glob("*.py")), ids=lambda p: p.name)
def test_дорогие_вызовы_домена_только_через_поток(path: Path) -> None:
    direct = _direct_blocking_calls(path.read_text(encoding="utf-8"))
    assert direct == [], (
        f"{path.name}: движок вызывается прямо в цикле событий — "
        f"{', '.join(f'{n} (строка {ln})' for n, ln in direct)}. "
        "Пока такой вызов идёт, бот не обслуживает никого: оберните в asyncio.to_thread"
    )
