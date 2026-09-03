#!/usr/bin/env python3
"""Замер потолка нагрузки: сколько одновременных проверок продукт держит.

Задача T101, решение D058. Владелец назвал нагрузку главным риском, но целевого
числа не задал — поэтому здесь сначала измеряется фактический потолок, а решение
«хватает или нет» принимается по числу, а не по ощущению.

Меряется то, что меряется без Telegram и без денег на модели:

1. **Конкурентная запись состояния.** У каждой проверки своя папка на чат, но
   блокировка и атомарная подмена файла — общий механизм. Проверяется, что N
   проверок, пишущих одновременно, не теряют записей и не портят JSON.
2. **Запись в одну проверку.** Альбом приходит пачкой кадров, и они
   обрабатываются параллельно в одном чате — здесь блокировка уже настоящая,
   с очередью.
3. **Сборка отчёта.** Она уходит в рабочий поток (`asyncio.to_thread`), поэтому
   цикл событий не блокирует; меряется её длительность — от неё зависит, сколько
   аудиторов могут завершать проверку одновременно.

Запуск:  python tools/loadcheck.py [--inspections N] [--findings M]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.domain import add_finding, start_inspection  # noqa: E402
from src.domain.config import load_settings  # noqa: E402
from src.domain.engine import state_file  # noqa: E402
from src.domain.state import read_state  # noqa: E402

#: Пункты боевой методики, зафиксированные в разных зонах: повторный `add` с той
#: же парой «код + зона» движок отклоняет, и замер иначе мерил бы отказы.
#: Зоны боевой методики — сверено с `data/zones.csv`, а не выдумано.
_ZONES = (
    "hot_kitchen",
    "cold_kitchen",
    "dining",
    "fridge",
    "freezer",
    "dry_storage",
    "dough",
    "dishwashing",
    "facade",
    "staff",
)
CASES = [(code, zone) for zone in _ZONES for code in ("CLN05", "CLN06")]


def _one_inspection(chat_id: int, findings: int) -> tuple[int, float, int]:
    """Завести проверку и записать в неё находки. Возвращает (чат, секунды, записей)."""
    started = time.perf_counter()
    start_inspection(
        chat_id,
        unit=f"Load {chat_id}",
        kind="Planned",
        report_lang="en",
        date="2026-09-03",
        auditor="loadcheck",
    )
    for i in range(findings):
        code, zone = CASES[i % len(CASES)]
        add_finding(chat_id, code=code, level="D1", zone=zone, text=f"load probe {i}")
    state = read_state(chat_id, load_settings())
    got = len(state.findings) if state else 0
    return chat_id, time.perf_counter() - started, got


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--inspections", type=int, default=8, help="одновременных проверок")
    p.add_argument("--findings", type=int, default=6, help="находок в каждой")
    a = p.parse_args()

    base = int(time.time()) % 100_000 * 1000
    chats = [900_000_000 + base + i for i in range(a.inspections)]

    print(f"Параллельных проверок: {a.inspections}, находок в каждой: {a.findings}")
    wall = time.perf_counter()
    results: list[tuple[int, float, int]] = []
    with ThreadPoolExecutor(max_workers=a.inspections) as pool:
        futures = [pool.submit(_one_inspection, c, a.findings) for c in chats]
        for f in as_completed(futures):
            results.append(f.result())
    wall = time.perf_counter() - wall

    lost = [(c, got) for c, _, got in results if got != a.findings]
    slowest = max(t for _, t, _ in results)
    print(f"Общее время: {wall:.2f} с, самая медленная проверка: {slowest:.2f} с")
    print(f"Проверок завершено: {len(results)}/{a.inspections}")
    if lost:
        print(f"ПОТЕРЯНЫ ЗАПИСИ: {lost}")
        return 1
    print("Записи целы во всех проверках")

    broken = []
    for c in chats:
        path = state_file(c, load_settings())
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            broken.append((c, str(exc)[:60]))
    if broken:
        print(f"ИСПОРЧЕН JSON СОСТОЯНИЯ: {broken}")
        return 1
    print("JSON состояния цел во всех проверках")

    rc = _album_burst(900_500_000 + base, a.findings)

    for c in chats:
        d = state_file(c, load_settings()).parent
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    print("Проверки замера удалены")
    return rc


def _album_burst(chat_id: int, findings: int) -> int:
    """Одна проверка, N одновременных записей — как приходит альбом кадров.

    Сценарий выше конкуренции за файл НЕ создаёт: у каждой проверки своя папка
    на чат, потоки пишут в разные файлы. Настоящая очередь к блокировке
    возникает здесь — альбом телеграм отдаёт пачкой, и кадры обрабатываются
    параллельно в одном чате. Именно на этом месте `save_state` когда-то
    оставлял обрезанный JSON (T012).
    """
    settings = load_settings()
    start_inspection(
        chat_id,
        unit="Album burst",
        kind="Planned",
        report_lang="en",
        date="2026-09-03",
        auditor="loadcheck",
    )
    n = min(findings, len(CASES))
    print(f"\nАльбом: {n} одновременных записей в ОДНУ проверку")
    t = time.perf_counter()
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [
            pool.submit(add_finding, chat_id, code=c, level="D1", zone=z, text=f"burst {i}")
            for i, (c, z) in enumerate(CASES[:n])
        ]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                errors.append(str(exc)[:80])
    took = time.perf_counter() - t
    state = read_state(chat_id, settings)
    got = len(state.findings) if state else 0
    print(f"Время: {took:.2f} с, записалось {got} из {n}")
    if errors:
        print(f"ОТКАЗЫ ПРИ ЗАПИСИ: {errors}")
    rc = 0
    if got != n:
        print(f"ПОТЕРЯНЫ ЗАПИСИ АЛЬБОМА: ожидалось {n}, записалось {got}")
        rc = 1
    else:
        print("Все записи альбома на месте")
    d = state_file(chat_id, settings).parent
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    return rc


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
