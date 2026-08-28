"""T012: атомарная запись состояния и блокировка на проверку.

Решение D007: состояние живёт в файле, базы нет. Прямое следствие — альбом из
нескольких кадров приходит в бот пачкой и обрабатывается параллельно, а
`save_state` писал поверх файла без временного файла и без блокировки. Цена
ошибки — потерянная запись или битый JSON посреди обхода точки.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conftest import Run, requires_data

pytestmark = requires_data

# Кадров в альбоме телеграма бывает до десяти. Одиночная гонка ловится не
# каждый раз — процессы стартуют вразнобой, — поэтому берём десять кадров и
# повторяем раунд несколько раз. Замер до починки: одиночный раунд из шести
# краснел примерно в трети прогонов, три раунда по десять — в каждом.
PARALLEL = 10
ROUNDS = 3


def state_of(workdir: Path) -> dict:
    return json.loads((workdir / "inspection.json").read_text(encoding="utf-8"))


def in_parallel(calls: list[Callable[[], Run]]) -> list[Run]:
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result() for f in [pool.submit(c) for c in calls]]


def test_параллельные_кадры_альбома_не_теряются(
    started: Callable[..., Run], workdir: Path
) -> None:
    """Шесть кадров одного нарушения, доснятых одновременно, — шесть фотографий."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    for r_no in range(ROUNDS):
        names = [f"r{r_no}p{i}.jpg" for i in range(PARALLEL)]
        results = in_parallel([lambda n=n: started("photo", "1", "--add", n) for n in names])
        assert all(r.code == 0 for r in results), [r.text for r in results if r.code]
        photos = state_of(workdir)["findings"][0]["photos"]
        missing = [n for n in names if n not in photos]
        assert not missing, f"раунд {r_no}: кадры альбома потеряны — {missing}"


def test_параллельные_записи_не_затирают_друг_друга(
    started: Callable[..., Run], workdir: Path
) -> None:
    """Одно и то же нарушение в разных зонах, зафиксированное одновременно."""
    zones = ["hot_kitchen", "cold_kitchen", "dough", "dining", "fridge", "freezer",
             "dry_storage", "facade", "dishwashing", "staff"]
    seen: list[int] = []
    for r_no in range(ROUNDS):
        results = in_parallel(
            [lambda z=z: started("add", "--qid", "PRD09", "--level", "D1", "--zone", z)
             for z in zones]
        )
        assert all(r.code == 0 for r in results), [r.text for r in results if r.code]
        st = state_of(workdir)
        assert sorted(f["zone"] for f in st["findings"]) == sorted(zones), (
            f"раунд {r_no}: часть записей потеряна — {[f['zone'] for f in st['findings']]}"
        )
        seen += [f["n"] for f in st["findings"]]
        for f in st["findings"]:
            started("drop", str(f["n"]))
    assert len(set(seen)) == len(seen), f"номера записей повторились: {sorted(seen)}"


def test_состояние_читается_целиком_во_время_записи(
    started: Callable[..., Run], workdir: Path
) -> None:
    """Читатель не должен ловить обрезанный JSON: запись идёт через os.replace."""
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    path = workdir / "inspection.json"
    broken: list[str] = []

    done = threading.Event()

    def reader() -> Run:
        while not done.is_set():
            raw = path.read_text(encoding="utf-8")
            try:
                json.loads(raw)
            except json.JSONDecodeError as e:
                broken.append(f"{e}: {raw[:120]!r}")
                return Run(0, "", "")
        return Run(0, "", "")

    def writers() -> Run:
        try:
            for r_no in range(ROUNDS):
                in_parallel([
                    lambda i=i, r_no=r_no: started("photo", "1", "--add", f"r{r_no}p{i}.jpg")
                    for i in range(PARALLEL)
                ])
        finally:
            done.set()
        return Run(0, "", "")

    in_parallel([reader, writers])
    assert not broken, f"состояние читалось битым: {broken[0]}"


def test_после_записи_не_остаётся_временных_файлов(
    started: Callable[..., Run], workdir: Path
) -> None:
    started("add", "--qid", "CLN05", "--level", "D1", "--zone", "hot_kitchen")
    leftovers = [p.name for p in workdir.iterdir() if p.suffix == ".tmp"]
    assert not leftovers, f"временные файлы записи остались на диске: {leftovers}"
