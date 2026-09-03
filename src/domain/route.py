"""Маршрут обхода: в каком порядке аудитор идёт по зонам и по пунктам внутри них.

Задача T061. Порядок — данные, а не код: методику правит человек управляющей
компании, и перестановка зоны не должна стоить релиза.

Файл `route.csv` в каталоге методики, три колонки:

```csv
entity,code,order
zone,facade,10
zone,dining,20
item,CLN05,10
```

* `entity` — `zone` (зона) или `item` (пункт чек-листа); строка, начинающаяся с
  `#`, — пометка человека и пропускается;
* `code` — код зоны или пункта. Кодом, никогда формулировкой: формулировки
  переводятся и правятся (`docs/02-domain.md`);
* `order` — целое число. Шаг в десять оставлен намеренно: вставить зону между
  двумя соседними можно, не перенумеровывая файл.

Файла нет — порядок остаётся тем, в котором строки лежат в методике. Что в файле
не упомянуто, идёт следом за упомянутым, сохраняя порядок методики: маршрут,
описанный наполовину, не должен прятать остальное.

Отдельным файлом, а не колонкой в `checklist.csv` и `zones.csv`, потому что
`engine/manage.py` перезаписывает оба файла фиксированным списком колонок
(`FIELDS`, `write_rows`) — правка методики через него стёрла бы весь маршрут
молча, а тихая потеря данных на этом проекте уже случалась дважды.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from .errors import ConfigError

ROUTE_FILE = "route.csv"

ZONE = "zone"
ITEM = "item"
ENTITIES = (ZONE, ITEM)

T = TypeVar("T")


@dataclass(frozen=True)
class Route:
    """Позиции по кодам: отдельно зоны, отдельно пункты. Пусто — маршрута нет."""

    zones: dict[str, int] = field(default_factory=dict)
    items: dict[str, int] = field(default_factory=dict)


def _refuse(path: Path, why: str) -> ConfigError:
    return ConfigError(
        f"Маршрут обхода {path} ({ROUTE_FILE}) не читается: {why}. "
        f"Нужны колонки entity,code,order — entity это {' или '.join(ENTITIES)}, "
        f"code это код зоны или пункта, order — целое число"
    )


def load(data_dir: Path) -> Route:
    """Прочитать маршрут. Файла нет — пустой маршрут, это законное состояние.

    Разбор строгий: непонятная строка — отказ, а не пропуск. Пропущенная строка
    означала бы маршрут, который человек написал, а система применила наполовину;
    заметить это можно было бы только на обходе точки.
    """
    path = data_dir / ROUTE_FILE
    if not path.is_file():
        return Route()
    zones: dict[str, int] = {}
    items: dict[str, int] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            entity = (row.get("entity") or "").strip()
            if not entity or entity.startswith("#"):
                continue
            if entity not in ENTITIES:
                raise _refuse(path, f"вид записи «{entity}» не из {ENTITIES}")
            code = (row.get("code") or "").strip()
            if not code:
                raise _refuse(path, f"у записи «{entity}» пустой код")
            raw = (row.get("order") or "").strip()
            try:
                position = int(raw)
            except ValueError:
                raise _refuse(path, f"позиция «{raw}» у «{code}» не целое число") from None
            where = zones if entity == ZONE else items
            if code in where:
                raise _refuse(path, f"код «{code}» ({entity}) назван в маршруте дважды")
            where[code] = position
    return Route(zones=zones, items=items)


def arrange(
    values: Sequence[T], order: dict[str, int], code_of: Callable[[T], str], *, what: str
) -> list[T]:
    """Выстроить по маршруту. Неупомянутое идёт следом, сохраняя порядок методики.

    Код, которого в методике нет, — отказ: опечатка в маршруте иначе просто не
    сработает, и разбираться придётся уже на точке.
    """
    known = {code_of(v) for v in values}
    unknown = sorted(set(order) - known)
    if unknown:
        raise ConfigError(
            f"В маршруте обхода ({ROUTE_FILE}) названы коды, которых нет в методике "
            f"({what}): {', '.join(unknown)}. Маршрут по ним ничего не переставит"
        )
    return [
        value
        for _, value in sorted(
            enumerate(values),
            key=lambda pair: (
                0 if code_of(pair[1]) in order else 1,
                order.get(code_of(pair[1]), 0),
                pair[0],
            ),
        )
    ]
