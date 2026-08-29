"""Где взять кадр, приложенный к записи, и что считать его пропажей.

Кадр хранится в записи ссылкой — в боте это идентификатор телеграма, при
запуске движка руками это путь к файлу. Идентификатор путём не является, и
проверить его существование в момент `attach_photo` невозможно; поэтому ссылка
превращается в файл здесь, на сборке отчёта, и промах ловится здесь же.

Резолвит ссылки блок, а не движок: движку уходит готовая карта «ссылка → файл».
Так правило «где лежит кадр» живёт в одном месте, а движок остаётся тем, чем
был, — разметкой отчёта. При запуске движка руками карты нет, и он читает
ссылку как путь, но продукт этой веткой не пользуется.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.domain.config import Settings
from src.domain.engine import chat_dir
from src.domain.state import read_state

#: Как получить файл кадра по идентификатору телеграма. Скачивает бот: токен
#: есть только у него. Блок отчёта спрашивает и проверяет ответ — пустота или
#: несуществующий файл считаются пропажей, а не поводом промолчать.
FetchPhoto = Callable[[str], "Path | str | None"]


@dataclass(frozen=True)
class PhotoPlan:
    """Что удалось найти и что потеряно."""

    #: Карта для движка: ссылка как она записана → абсолютный путь к файлу.
    mapping: dict[str, str]
    #: Пары «номер записи, ссылка» — по одной на каждую запись, потерявшую кадр.
    misses: list[tuple[int, str]]


def _as_file(value: Path | str | None) -> str | None:
    """Ответ резолвера — файл или ничего. Папка и пустота файлом не считаются."""
    if not value:
        return None
    path = Path(value)
    return str(path.resolve()) if path.is_file() else None


def _from_disk(ref: str, work: Path) -> str | None:
    """Ссылка-путь. Относительный — от папки проверки: так же его читает движок."""
    path = Path(ref)
    if not path.is_absolute():
        path = work / path
    return _as_file(path)


def resolve_photos(
    chat_id: int, settings: Settings, fetch_photo: FetchPhoto | None = None
) -> PhotoPlan:
    """Разложить ссылки всех записей проверки на найденные и потерянные.

    Одна и та же ссылка может висеть на двух записях — резолвится она один раз,
    а в потери попадает каждая запись отдельно: доказательство теряет каждая.
    """
    state = read_state(chat_id, settings)
    if state is None:
        # Отсутствие проверки — не задача этого модуля. Отказ с внятным текстом
        # поднимет вызов движка, и он там один на все команды блока.
        return PhotoPlan(mapping={}, misses=[])
    work = chat_dir(chat_id, settings)
    seen: dict[str, str | None] = {}
    mapping: dict[str, str] = {}
    misses: list[tuple[int, str]] = []
    for finding in state.findings:
        for ref in finding.photos:
            if ref not in seen:
                seen[ref] = (
                    _as_file(fetch_photo(ref)) if fetch_photo is not None else _from_disk(ref, work)
                )
            found = seen[ref]
            if found is None:
                misses.append((finding.n, ref))
            else:
                mapping[ref] = found
    return PhotoPlan(mapping=mapping, misses=misses)


def misses_text(misses: list[tuple[int, str]]) -> str:
    """Текст отказа: аудитор должен узнать, какую запись переснимать.

    Называется ссылка как она записана, а не путь, по которому её искали:
    аудитору нужен свой кадр, а не внутренности сборки.
    """
    parts = "; ".join(f"запись №{n} — «{ref}»" for n, ref in misses)
    сколько = "Кадр не найден" if len(misses) == 1 else "Кадры не найдены"
    return (
        f"{сколько}: {parts}. Приложите кадр заново или соберите отчёт без него — "
        f"на его месте будет видимая отметка, что фотография не приложена"
    )
