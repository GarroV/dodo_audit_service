"""Окружение блока: где лежит методика, где состояние, где движок.

Площадка — деталь реализации (решение D004), поэтому пути приходят переменными
окружения и нигде не зашиты. Значения по умолчанию не подставляются намеренно:
на пустом чек-листе проверка выглядит успешной, а состояние, записанное в
случайную папку, теряется молча.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .version import published

DATA_DIR_VAR = "AUDIT_DATA_DIR"
STATE_DIR_VAR = "STATE_DIR"

#: Файлы методики. Движок при нехватке файла тихо берёт его из своей копии
#: данных (`engine/../data`), то есть считает по смеси двух методик, — поэтому
#: полнота каталога проверяется до первого вызова.
REQUIRED_DATA_FILES = ("checklist.csv", "zones.csv", "scoring.json", "criteria.md")

#: Файлы методики, которых может не быть. В отпечаток версии они входят наравне
#: с обязательными (D050: любая правка методики — новая версия), а отсутствие
#: файла — законное состояние, а не отказ.
#:
#: `photo-cues.md` — карта слов. В отпечатке она с 04.09.2026 и по той же
#: причине, что и остальное: с D063 карта решает, какой пункт предлагается
#: без вызова модели, а с D064 такая запись сразу становится находкой в
#: отчёте партнёру. Пока карты в отпечатке не было, одна добавленная в неё
#: строка меняла результат записи, не меняя версии, — и две одинаково
#: помеченные проверки оказывались записаны по разным правилам.
#:
#: `route.csv` — порядок обхода (T061). Отдельным файлом, а не колонкой в
#: `checklist.csv` и `zones.csv`, потому что `engine/manage.py` перезаписывает
#: оба файла фиксированным списком колонок (`FIELDS`, `write_rows`): любая
#: правка методики через него молча стёрла бы весь маршрут.
OPTIONAL_DATA_FILES = ("route.csv", "photo-cues.md")

#: Всё, что образует методику, — в том порядке, в котором идёт в отпечаток версии.
DATA_FILES = REQUIRED_DATA_FILES + OPTIONAL_DATA_FILES

#: Форк методики: `manage.py` при любой правке чек-листа создаёт эту папку в
#: текущем рабочем каталоге, и дальше движок из неё считает по форку, а из
#: соседней — по оригиналу (`docs/04-engine.md`). При папке состояния на чат это
#: даёт разную методику в разных чатах.
FORK_DIR = "checklist_data"

#: Язык по умолчанию для необязательных параметров. Именно значение параметра:
#: любой вызов волен передать другой, в логике блока языка нет.
DEFAULT_LANG = "ru"

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """Разобранное окружение блока."""

    data_dir: Path
    state_dir: Path
    audit_script: Path


def _required_path(env: Mapping[str, str], name: str) -> Path:
    raw = (env.get(name) or "").strip()
    if not raw:
        raise ConfigError(
            f"Не задана переменная окружения {name}. "
            f"Без неё непонятно, где лежит методика и состояние проверок — "
            f"пример значений в .env.example"
        )
    # abspath, а не resolve(): каталог методики и состояние подкладывают
    # симлинками (и в разработке, и томом контейнера). Разворачивать их означало
    # бы показывать в отказах путь, которого человек у себя не увидит.
    return Path(os.path.abspath(os.path.expanduser(raw)))


def assert_no_checklist_fork(where: Path) -> None:
    """Отказать, если рядом лежит форк методики.

    Проверяется каталог, который станет рабочим для движка: `audit.py` ищет
    `checklist_data/` именно относительно текущей папки процесса.
    """
    fork = where / FORK_DIR
    if fork.is_dir():
        raise ConfigError(
            f"Рядом с рабочим каталогом найден форк чек-листа: {fork}. "
            f"Движок будет считать по нему, а не по {DATA_DIR_VAR}, и разные чаты "
            f"получат разную методику. Удалите папку {FORK_DIR} или запускайтесь в другом каталоге"
        )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Прочитать пути из окружения. Проверок содержимого здесь нет."""
    src = os.environ if env is None else env
    return Settings(
        data_dir=_required_path(src, DATA_DIR_VAR),
        state_dir=_required_path(src, STATE_DIR_VAR),
        audit_script=_REPO_ROOT / "engine" / "audit.py",
    )


def check_environment(env: Mapping[str, str] | None = None) -> Settings:
    """Проверить окружение целиком и вернуть его. Отказ — `ConfigError`.

    Зовётся на старте продукта и внутри каждой операции блока: проверка дешёвая
    (несколько обращений к файловой системе), а цена пропуска — отчёт, собранный
    по чужой или пустой методике.
    """
    settings = load_settings(env)
    if not settings.data_dir.is_dir():
        raise ConfigError(
            f"Каталог методики не найден: {settings.data_dir} "
            f"(переменная {DATA_DIR_VAR}). Это данные управляющей компании, "
            f"они лежат вне репозитория"
        )
    missing = [name for name in REQUIRED_DATA_FILES if not (settings.data_dir / name).is_file()]
    if missing:
        raise ConfigError(
            f"Каталог методики {settings.data_dir} неполный: не хватает "
            f"{', '.join(missing)}. Движок добрал бы недостающее из своей копии данных "
            f"и посчитал по смеси двух методик"
        )
    # Издание методики разбирается на старте, а не при первой проверке: узнать,
    # что набор подписан без даты, аудитор должен до выезда на точку, а не в поле.
    published(settings.data_dir)
    if not settings.audit_script.is_file():
        raise ConfigError(
            f"Движок не найден: {settings.audit_script}. Блок вызывает его подпроцессом, "
            f"считать оценку самостоятельно он не имеет права"
        )
    assert_no_checklist_fork(Path.cwd())
    assert_no_checklist_fork(settings.state_dir)
    return settings
