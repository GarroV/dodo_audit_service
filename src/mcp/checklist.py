"""Хранилище версий методики: правка через агента идёт в новую версию, а не поверх боевой.

До этой задачи весь MCP был только чтением, и это было его гарантией. Здесь
появляется запись — в методику управляющей компании, ту самую, по которой
считаются отчёты партнёрам. Поэтому свойств у хранилища ровно четыре, и все
четыре проверяются запуском, а не обещанием.

**Боевой набор не переписывается.** Правка берёт снимок нужной версии, правит
копию и кладёт РЯДОМ новую версию. Каталог, из которого читает движок, при
этом не меняется ни одним байтом (D049). Опубликовать новую версию — отдельное
действие, и это перестановка указателя, а не перезапись файлов.

**Версия называется по D050.** Идентификатор составной: имя набора, дата
издания и отпечаток данных (`imf-2026-09-03-3f5a91b2c7d0`). Считает его тот же
код, что штампует версию в каждую проверку (`src.domain.version.compose`), —
второй экземпляр этой формулы разошёлся бы с первым, и проверка ссылалась бы
на версию, которой в хранилище нет.

**Правит методику движок, а не этот модуль.** `engine/manage.py` вызывается
подпроцессом: в нём живут правила, которых здесь повторять нельзя — сохранение
колонок управляющей компании (T109), запрет молча раздать доли зон (T112),
отказ на неизвестную зону. Контракт `lint-imports` запрещает `src.mcp`
импортировать движок; подпроцесс — тот же приём, которым его зовёт `domain`.

**Проверка идёт до записи.** Кандидат принимается, только если движок сначала
согласился с методикой (`manage.py validate`), а затем посчитал по ней оценку
(`audit.py init` + `score`). Методику, которую движок считать откажется,
хранилище не примет: иначе агент положил бы набор, ломающий продукт.

Устройство каталога:

```
<хранилище>/versions/<версия>/   полный снимок методики
<хранилище>/current              указатель на действующую версию
<хранилище>/journal.jsonl        журнал: кто, что, когда и чем кончилось
```

Хранилище не блокируется на запись намеренно. Имя каталога версии выводится из
содержимого, поэтому две разные правки не могут занять одно имя, две одинаковые
получают явный отказ, а указатель переставляется одним `os.replace`.
"""

from __future__ import annotations

import csv
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from ..domain.config import DATA_FILES, REQUIRED_DATA_FILES
from ..domain.version import VERSION_FILE, compose, fingerprint, published
from ..recognize.cues import CUES_FILE as RECOGNIZE_CUES_FILE
from .errors import ChecklistError

#: Подкаталог со снимками версий. Отдельный уровень, чтобы указатель и журнал
#: не лежали среди версий и не притворялись одной из них.
VERSIONS_DIR = "versions"

#: Указатель на действующую версию — символическая ссылка. Ссылка, а не копия:
#: публикация обязана быть одним неделимым действием, иначе движок однажды
#: прочитает наполовину переписанную методику.
CURRENT_LINK = "current"

#: Журнал правок. Строка на событие, дописывается и не переписывается.
JOURNAL_FILE = "journal.jsonl"

#: Карта слов внутри версии методики. Имя берётся у того, кто её читает
#: (`src.recognize.cues.CUES_FILE`), а не пишется здесь второй раз: разошлись
#: бы, и правка легла бы в файл, которого продукт не открывает.
CUES_FILE = RECOGNIZE_CUES_FILE

#: Где собираются кандидаты. Внутри хранилища, чтобы принятая версия въезжала
#: на место переименованием, а не копированием через границу файловой системы.
TMP_DIR = ".tmp"

#: Чем заменяется путь кандидата в тексте отказа движка. Движок называет файл,
#: в котором проблема, — а это временная копия под правку: показать её агенту
#: значит показать путь, которого у человека нет и не будет.
CANDIDATE_LABEL = "<каталог новой версии>"

#: Хвост отказа, объясняющий, куда делся путь.
#:
#: Ответ инструмента уходит в модель, то есть за пределы машины, и абсолютный
#: путь в нём показывает устройство каталогов деплоя и имя пользователя, под
#: которым поднят сервер (T120, issue #96). Вырезать путь молча нельзя: тому,
#: кто держит сервер, чинить тогда нечего. Поэтому агенту достаётся причина
#: словами и именами переменных, а путь — логу процесса, который остаётся на
#: машине.
IN_LOG = "Какие именно каталоги — в логе сервера: он остаётся на машине"

#: Имя набора: строчные латинские буквы, цифры, дефис и подчёркивание. Имя
#: попадает и в идентификатор версии (а он уезжает в базу к каждой проверке), и
#: в имя каталога хранилища — знак пути в нём читал бы что угодно на машине.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

#: Имя версии как кусок пути. Шире имени набора: сюда входят дата и отпечаток.
VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")

#: Код пункта или зоны. Проверяется, потому что уходит в командную строку
#: движка позиционным аргументом: значение, начинающееся с дефиса, разбор
#: аргументов прочитал бы как флаг.
CODE_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,32}$")

#: Дата в конце имени набора — отказ: дату ставит хранилище, и вторая рядом с
#: ней сделала бы идентификатор нечитаемым.
DATE_TAIL = re.compile(r"\d{4}-\d{2}-\d{2}$")

#: Сколько ждём движок. Правка методики — это разбор пары CSV, секунды; всё,
#: что дольше, — зависший подпроцесс, держащий поток сервера.
ENGINE_TIMEOUT_SEC = 60

_REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGE_SCRIPT = _REPO_ROOT / "engine" / "manage.py"
AUDIT_SCRIPT = _REPO_ROOT / "engine" / "audit.py"


# --- лог процесса -------------------------------------------------------------


def _to_log(повод: str, **пути: Path) -> None:
    """Напечатать пути в лог процесса — рядом с отказом, но не внутри него.

    Печатается в `stderr`, тем же префиксом `[mcp]`, что и лог транспорта:
    журнал хранилища ведёт события методики, а это событие окружения, и
    случается оно ровно тогда, когда хранилища ещё может не быть.
    """
    подробности = ", ".join(f"{имя}={путь}" for имя, путь in пути.items())
    print(f"[mcp] {повод}: {подробности}", file=sys.stderr)


@dataclass(frozen=True)
class Store:
    """Куда пишем версии и что сегодня читает движок.

    `live` — каталог методики продукта (`AUDIT_DATA_DIR`). Хранилище его
    только читает: из него берётся нулевая версия, и по нему же проверяется,
    увидит ли движок публикацию вообще.
    """

    root: Path
    live: Path


@dataclass(frozen=True)
class VersionInfo:
    """Одна версия в хранилище — как она называется и когда издана."""

    version: str
    name: str | None
    day: str | None
    fingerprint: str
    current: bool


@dataclass(frozen=True)
class Outcome:
    """Чем кончилась правка.

    Отказ приходит именно так, а не пустотой: вызывающий обязан различать
    «версия записана» и «движок эту методику не принял».
    """

    accepted: bool
    status: str
    base_version: str
    version: str | None = None
    refusal: str | None = None
    engine: str | None = None


# --- проверки имён ------------------------------------------------------------


def _check_name(name: str) -> str:
    """Имя набора — или отказ с примером годного."""
    value = (name or "").strip()
    if DATE_TAIL.search(value):
        raise ChecklistError(
            f"Имя набора «{value}» кончается датой. Дату издания ставит хранилище само, "
            f"и вторая рядом с ней сделала бы идентификатор версии нечитаемым: "
            f"назовите набор без даты, например «imf»"
        )
    if not NAME_PATTERN.match(value):
        raise ChecklistError(
            f"Имя набора «{value}» не годится: ожидаются строчные латинские буквы, цифры, "
            f"дефис и подчёркивание, до 32 знаков (например «imf»). Имя попадает в "
            f"идентификатор версии, а он уезжает в базу к каждой посчитанной проверке"
        )
    return value


def _check_version(version: str) -> str:
    """Имя версии как кусок пути внутри хранилища — или отказ."""
    value = (version or "").strip()
    if not VERSION_PATTERN.match(value):
        raise ChecklistError(
            f"«{version}» не похоже на версию методики. Ожидается идентификатор вида "
            f"«imf-2026-09-03-3f5a91b2c7d0»; перечень версий отдаёт checklist_versions"
        )
    return value


def check_code(code: str) -> str:
    """Код пункта или зоны — или отказ.

    Проверяется до вызова движка: код уходит туда позиционным аргументом, и
    значение, начинающееся с дефиса, разбор аргументов прочитал бы как флаг —
    а отказ разбора аргументов ничего не объясняет тому, кто ошибся в коде.
    """
    value = (code or "").strip()
    if not CODE_PATTERN.match(value):
        raise ChecklistError(
            f"«{code}» не похоже на код: ожидаются латинские буквы, цифры и подчёркивание "
            f"(например «CLN05» или «fridge»). Сущности связываются кодами, а не "
            f"формулировками"
        )
    return value


# --- вызов движка -------------------------------------------------------------


def _run(
    script: Path, args: list[str], *, data_dir: Path, cwd: Path, state: Path | None
) -> tuple[int, str]:
    """Запустить скрипт движка над указанным каталогом методики.

    `CHECKLIST_DIR` — тот же рычаг, которым методику подкладывают движку в
    бою: `active_dir()` смотрит на него первым. Рабочий каталог нейтральный —
    иначе движок подобрал бы форк `checklist_data/`, случайно оказавшийся
    рядом с процессом сервера.
    """
    env = dict(os.environ)
    env["CHECKLIST_DIR"] = str(data_dir)
    # Кэш байткода в чужом каталоге движку не нужен, а рядом с методикой он
    # выглядел бы файлом методики.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if state is not None:
        env["INSPECTION_FILE"] = str(state)
    завершение = subprocess.run(  # noqa: S603 — список аргументов собран здесь, оболочки нет
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=ENGINE_TIMEOUT_SEC,
    )
    return завершение.returncode, (завершение.stdout + завершение.stderr)


def _argv(command: str, positional: str | None, options: Mapping[str, object]) -> list[str]:
    """Аргументы командной строки движка из имени команды и словаря опций.

    Значения передаются формой `--флаг=значение`, а не двумя элементами:
    формулировка пункта вполне может начинаться с дефиса, и разбор аргументов
    прочитал бы её как имя следующего флага.
    """
    argv = [command]
    if positional is not None:
        argv.append(check_code(positional))
    for flag, value in options.items():
        if value is None or value is False:
            continue
        if value is True:
            argv.append(f"--{flag}")
            continue
        argv.append(f"--{flag}={value}")
    return argv


def _clean(text: str, *paths: Path) -> str:
    """Убрать из текста движка пути с диска.

    Названные каталоги — это временная копия под правку и нейтральный каталог
    рядом с ней: путей, которых у человека нет и не будет, в ответе быть не
    должно. Корень продукта вычищается отдельно и до конца: движок падает без
    общего перехвата, а трейсбек печатает путь к своим файлам — то есть
    каталог, куда развёрнут продукт, вместе с именем пользователя. Отрезается
    ровно корень, поэтому «engine/manage.py» остаётся: по нему чинить можно, а
    устройство машины по нему не читается (T120).
    """
    out = text
    for path in paths:
        out = out.replace(str(path), CANDIDATE_LABEL)
    out = out.replace(f"{_REPO_ROOT}{os.sep}", "")
    return out.strip()


def _engine_accepts(candidate: Path, day: date) -> str | None:
    """`None`, если движок принимает эту методику; иначе его собственный отказ.

    Шагов три, и каждый ловит своё. `manage.py validate` показывает ВСЕ
    проблемы разом — дубли кодов, неизвестные зоны, пункт без критериев,
    несходящиеся доли; он для этого и сделан и на первой же не падает.
    `audit.py init` и `score` — это уже настоящий расчёт по методике: он
    спотыкается о то, чего `validate` не читает вовсе (ставки вычетов, матрица
    букв), и именно он решает, посчитается ли по такой методике проверка.

    Отказ возвращается словами движка. Пересказывать их здесь нельзя: правила
    живут в движке, и пересказ разошёлся бы с ними при первой же правке.
    """
    with tempfile.TemporaryDirectory(prefix="mcp-checklist-") as neutral:
        рядом = Path(neutral)
        состояние = рядом / "inspection.json"
        шаги = (
            (MANAGE_SCRIPT, ["validate"], None),
            (
                AUDIT_SCRIPT,
                ["init", "--unit", "проверка методики", "--date", day.isoformat()],
                состояние,
            ),
            (AUDIT_SCRIPT, ["score"], состояние),
        )
        for скрипт, аргументы, state in шаги:
            код, вывод = _run(скрипт, аргументы, data_dir=candidate, cwd=рядом, state=state)
            if код != 0:
                return _clean(вывод, candidate, рядом)
    return None


# --- журнал -------------------------------------------------------------------


def _journal(store: Store, record: dict[str, object]) -> None:
    """Дописать событие в журнал.

    Строка на событие и только дописывание: журнал переживёт и правку, и того,
    кто её сделал. Токена в нём нет — только код арендатора, которым тот
    представлен на сервере.
    """
    store.root.mkdir(parents=True, exist_ok=True)
    строка = json.dumps({"at": datetime.now(UTC).isoformat(), **record}, ensure_ascii=False)
    with (store.root / JOURNAL_FILE).open("a", encoding="utf-8") as f:
        f.write(строка + "\n")


def read_journal(store: Store) -> list[dict[str, object]]:
    """Журнал целиком, событиями по порядку. Нет журнала — пусто, а не отказ."""
    path = store.root / JOURNAL_FILE
    if not path.is_file():
        return []
    события: list[dict[str, object]] = []
    for строка in path.read_text(encoding="utf-8").splitlines():
        text = строка.strip()
        if text:
            события.append(json.loads(text))
    return события


# --- устройство хранилища -----------------------------------------------------


def _versions_root(store: Store) -> Path:
    return store.root / VERSIONS_DIR


def _link(store: Store) -> Path:
    return store.root / CURRENT_LINK


def _current_id(store: Store) -> str:
    """Куда смотрит указатель. Зовётся только когда указатель — ссылка."""
    return Path(os.readlink(_link(store))).name


def _bootstrap(store: Store) -> str:
    """Завести хранилище нулевой версией — снимком боевой методики.

    Хранилище начинается не с пустоты: первая запись в нём — та методика, по
    которой продукт считает сегодня. Иначе у первой же правки не было бы
    предшественника, и сравнивать её было бы не с чем.
    """
    live = store.live
    if not live.is_dir():
        _to_log("каталога методики нет", AUDIT_DATA_DIR=live)
        raise ChecklistError(
            f"Каталог методики, названный в AUDIT_DATA_DIR, на сервере не найден. Хранилище "
            f"версий начинается с той методики, по которой продукт считает сегодня, — без неё "
            f"начинать не с чего. {IN_LOG}"
        )
    нет = [name for name in REQUIRED_DATA_FILES if not (live / name).is_file()]
    if нет:
        _to_log("методика неполная", AUDIT_DATA_DIR=live)
        raise ChecklistError(
            f"Каталог методики, названный в AUDIT_DATA_DIR, неполный: не хватает "
            f"{', '.join(нет)}. Снимать версию с половины методики нельзя — движок добрал бы "
            f"остальное из своей копии данных. {IN_LOG}"
        )
    version = compose(live, DATA_FILES)
    _versions_root(store).mkdir(parents=True, exist_ok=True)
    цель = _versions_root(store) / version
    if not цель.is_dir():
        with _holder(store) as holder:
            снимок = holder / "data"
            shutil.copytree(live, снимок)
            os.replace(снимок, цель)
    _point_at(store, version)
    _journal(
        store,
        {
            "tenant": None,
            "tool": "bootstrap",
            "outcome": "accepted",
            "base_version": None,
            "version": version,
            "refusal": None,
            "note": f"снимок боевой методики {live}",
        },
    )
    return version


def _point_at(store: Store, version: str) -> None:
    """Перевести указатель на версию одним неделимым действием.

    Ссылка создаётся под временным именем и переименовывается на место:
    удалить и создать заново означало бы окно, в котором движок читает
    методику по несуществующему пути.
    """
    link = _link(store)
    if link.exists() and not link.is_symlink():
        _to_log("на месте указателя не ссылка", MCP_CHECKLIST_STORE=store.root)
        raise ChecklistError(
            f"На месте указателя действующей версии — файла «{CURRENT_LINK}» в хранилище "
            f"MCP_CHECKLIST_STORE — лежит не ссылка. Хранилище версий методики собрано не им: "
            f"уберите этот файл или укажите под хранилище другой каталог. {IN_LOG}"
        )
    # Имя временной ссылки уникально: одно и то же имя два раза не займут ни
    # два потока сервера, ни два процесса, ни брошенная ссылка после падения.
    временный = store.root / f".{CURRENT_LINK}.{os.getpid()}.{secrets.token_hex(6)}"
    os.symlink(os.path.join(VERSIONS_DIR, version), временный)
    os.replace(временный, link)


class _holder:
    """Временный каталог внутри хранилища — чтобы принятая версия въезжала переименованием."""

    def __init__(self, store: Store) -> None:
        self._tmp = store.root / TMP_DIR
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._path: Path | None = None

    def __enter__(self) -> Path:
        self._path = Path(tempfile.mkdtemp(dir=str(self._tmp)))
        return self._path

    def __exit__(self, *_: object) -> None:
        if self._path is not None:
            shutil.rmtree(self._path, ignore_errors=True)


def _ensure(store: Store) -> str:
    """Действующая версия. Хранилища ещё нет — оно заводится здесь."""
    if _link(store).is_symlink():
        return _current_id(store)
    return _bootstrap(store)


def tip_version(store: Store) -> str:
    """Версия, от которой правка отсчитывается по умолчанию, — последняя в журнале.

    Не действующая, а **последняя случившаяся**, и это важно. Правка не
    публикуется сама (D049), поэтому действующая версия остаётся прежней; взяв
    её за основу, вторая правка подряд потеряла бы первую молча — и опубликован
    оказался бы набор без неё. Журнал помнит порядок событий, поэтому основой
    служит последнее принятое: заведение хранилища, предыдущая правка или
    публикация, если ею только что откатились назад.
    """
    действующая = _ensure(store)
    for событие in reversed(read_journal(store)):
        if событие.get("outcome") == "accepted" and событие.get("version"):
            версия = str(событие["version"])
            if (_versions_root(store) / версия).is_dir():
                return версия
    return действующая


def _version_dir(store: Store, version: str) -> Path:
    каталог = _versions_root(store) / _check_version(version)
    if not каталог.is_dir():
        raise ChecklistError(
            f"Версии методики «{version}» в хранилище нет. Перечень версий отдаёт "
            f"checklist_versions"
        )
    return каталог


def current_version(store: Store) -> str:
    """Версия, на которую смотрит указатель хранилища."""
    return _ensure(store)


def versions(store: Store) -> list[VersionInfo]:
    """Все версии хранилища, свежие издания впереди. Старые не удаляются никогда (D050)."""
    действующая = _ensure(store)
    найденные: list[VersionInfo] = []
    for каталог in sorted(_versions_root(store).iterdir()):
        if not каталог.is_dir():
            continue
        издание = published(каталог)
        найденные.append(
            VersionInfo(
                version=каталог.name,
                name=издание[0] if издание else None,
                day=издание[1] if издание else None,
                fingerprint=каталог.name.rsplit("-", 1)[-1],
                current=каталог.name == действующая,
            )
        )
    return sorted(найденные, key=lambda v: (v.day or "", v.version), reverse=True)


# --- чтение методики ----------------------------------------------------------

#: Что читаем из версии: пункты чек-листа или зоны.
_FILES = {"items": "checklist.csv", "zones": "zones.csv"}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [
            {k: (v or "") for k, v in row.items() if k is not None}
            for row in csv.DictReader(f)
            if (row.get("id") or row.get("code") or "").strip()
        ]


def read_items(
    store: Store, *, version: str | None = None, what: str = "items"
) -> list[dict[str, str]]:
    """Строки методики как они лежат в файле, вместе с колонками управляющей компании.

    Ничего не выводится и не пересчитывается: это данные УК, и блок отдаёт их
    в том виде, в каком движок их читает.
    """
    каталог = _version_dir(store, _ensure(store) if version is None else version)
    return _rows(каталог / _FILES[what])


def read_item(store: Store, *, code: str, version: str | None = None) -> dict[str, str]:
    """Один пункт вместе с его критериями D1/D2/D3.

    Неизвестный код — отказ, а не пустая выдача: пустая читалась бы как «такого
    пункта в методике нет», хотя это может быть и опечатка в коде.
    """
    каталог = _version_dir(store, _ensure(store) if version is None else version)
    wanted = check_code(code).upper()
    for строка in _rows(каталог / "checklist.csv"):
        if строка.get("id", "").strip().upper() == wanted:
            return {**строка, "criteria": _criteria(каталог, wanted)}
    raise ChecklistError(
        f"Пункта «{code}» нет в методике версии {каталог.name}. Пункты связываются кодами, "
        f"а не формулировками: перечень отдаёт checklist_items"
    )


def _criteria(каталог: Path, code: str) -> str:
    """Критерии пункта из `criteria.md`. Нет раздела — пустая строка, а не отказ."""
    path = каталог / "criteria.md"
    if not path.is_file():
        return ""
    тело = path.read_text(encoding="utf-8")
    найдено = re.search(rf"(?:^|\n)## {re.escape(code)}\n(.*?)(?=\n## |\Z)", тело, re.S)
    return найдено.group(1).strip() if найдено else ""


# --- правка -------------------------------------------------------------------


def _resolve_name(version_name: str | None, base_dir: Path) -> str:
    """Имя набора для новой версии: названное вызывающим либо унаследованное.

    Набор, который никто не издавал, — это `local-<отпечаток>` без даты.
    Выдумывать за управляющую компанию имя здесь нельзя (это делало бы в
    отчёте издание, которого не было), а версия без даты противоречит D050, —
    поэтому первая правка обязана имя назвать.
    """
    if version_name is not None:
        return _check_name(version_name)
    издание = published(base_dir)
    if издание is not None:
        return издание[0]
    raise ChecklistError(
        "У методики нет имени набора: она никем не издана и живёт под одним отпечатком "
        "данных. Правка обязана дать версию с датой (решение D050), а дата без имени "
        "набора не идентификатор — назовите набор аргументом version_name, например «imf». "
        "Дальше имя подхватится само"
    )


def apply_change(
    store: Store,
    *,
    tenant: str,
    tool: str,
    command: str,
    options: Mapping[str, object],
    positional: str | None = None,
    base: str | None = None,
    version_name: str | None = None,
    note: str | None = None,
    today: date | None = None,
) -> Outcome:
    """Правка методики: снимок версии → правка копии → проверка движком → новая версия.

    Боевой каталог не открывается на запись ни на одном шаге. Кандидат живёт
    во временном каталоге хранилища и въезжает на место переименованием — то
    есть версия в хранилище либо есть целиком, либо её нет вовсе.
    """

    def правит_движок(кандидат: Path, holder: Path) -> tuple[str | None, str]:
        код, вывод = _run(
            MANAGE_SCRIPT,
            _argv(command, positional, options),
            data_dir=кандидат,
            cwd=holder,
            state=None,
        )
        return (_clean(вывод, кандидат, holder) if код != 0 else None), вывод

    return apply_edit(
        store,
        tenant=tenant,
        tool=tool,
        mutate=правит_движок,
        base=base,
        version_name=version_name,
        note=note,
        today=today,
    )


def apply_edit(
    store: Store,
    *,
    tenant: str,
    tool: str,
    mutate: Callable[[Path, Path], tuple[str | None, str]],
    base: str | None = None,
    version_name: str | None = None,
    note: str | None = None,
    today: date | None = None,
) -> Outcome:
    """Общий ход любой правки методики: снимок → правка копии → проверка → версия.

    `mutate` правит КОПИЮ и возвращает пару «отказ или `None`, что сказал
    правивший». Правит копию либо движок (`apply_change` — чек-лист и зоны,
    правила там), либо этот блок (`photo_cues` — карта слов, которую движок не
    читает вовсе и правил для неё не держит). Всё остальное — снимок, отпечаток,
    проверка движком, запрет пустой правки, въезд переименованием и журнал —
    одно на обе правки, потому что это свойства ХРАНИЛИЩА, а не того, кто
    правит. Второй экземпляр этого хода разошёлся бы с первым.
    """
    day = today or date.today()
    base_version = tip_version(store) if base is None else base
    base_dir = _version_dir(store, base_version)
    name = _resolve_name(version_name, base_dir)
    прежний_отпечаток = fingerprint(base_dir, DATA_FILES)

    with _holder(store) as holder:
        кандидат = holder / "data"
        shutil.copytree(base_dir, кандидат)
        отказ_правки, вывод = mutate(кандидат, holder)
        if отказ_правки is not None:
            return _refused(
                store,
                tenant=tenant,
                tool=tool,
                base_version=base_version,
                note=note,
                refusal=отказ_правки,
            )
        (кандидат / VERSION_FILE).write_text(f"{name} {day.isoformat()}\n", encoding="utf-8")
        отказ = _engine_accepts(кандидат, day)
        if отказ is not None:
            return _refused(
                store,
                tenant=tenant,
                tool=tool,
                base_version=base_version,
                note=note,
                refusal=отказ,
                engine=_clean(вывод, кандидат, holder),
            )
        if fingerprint(кандидат, DATA_FILES) == прежний_отпечаток:
            raise ChecklistError(
                f"Правка ничего не изменила в методике: отпечаток данных остался прежним "
                f"({прежний_отпечаток}). Новая версия с тем же содержимым записала бы в "
                f"журнал правку, которой не было"
            )
        version = compose(кандидат, DATA_FILES)
        цель = _versions_root(store) / _check_version(version)
        if цель.exists():
            raise ChecklistError(
                f"Версия {version} в хранилище уже есть: ровно эта правка от этого же набора "
                f"и этой же датой уже сделана. Отпечаток считается по данным, поэтому две "
                f"одинаковые правки — это одна версия, а не две"
            )
        цель.parent.mkdir(parents=True, exist_ok=True)
        os.replace(кандидат, цель)

    сказано = _clean(вывод, кандидат, holder)
    _journal(
        store,
        {
            "tenant": tenant,
            "tool": tool,
            "outcome": "accepted",
            "base_version": base_version,
            "version": version,
            "refusal": None,
            "note": note,
        },
    )
    return Outcome(
        accepted=True,
        status=(
            f"new checklist version {version} stored, validated by the audit engine; "
            f"it is not published yet — the engine still reads {base_version}"
        ),
        base_version=base_version,
        version=version,
        engine=сказано,
    )


def _refused(
    store: Store,
    *,
    tenant: str,
    tool: str,
    base_version: str,
    note: str | None,
    refusal: str,
    engine: str | None = None,
) -> Outcome:
    """Отклонённая правка: версии нет, но событие записано.

    Отклонённая правка — тоже событие: без неё непонятно, почему методика не
    менялась, хотя её пытались менять.
    """
    _journal(
        store,
        {
            "tenant": tenant,
            "tool": tool,
            "outcome": "refused",
            "base_version": base_version,
            "version": None,
            "refusal": refusal,
            "note": note,
        },
    )
    return Outcome(
        accepted=False,
        status="the change was refused, no new version was stored",
        base_version=base_version,
        refusal=refusal,
        engine=engine,
    )


def publish(store: Store, *, tenant: str, version: str) -> dict[str, object]:
    """Сделать версию действующей: перестановка указателя, а не перезапись методики.

    Отказ, если движок читает не хранилище. Публикация, которой движок не
    увидит, — молчаливый сбой худшего вида: агент говорит «опубликовано», а
    проверки продолжают считаться по прежней методике.
    """
    прежняя = _ensure(store)
    цель = _version_dir(store, version)
    если_читает = os.path.realpath(store.live)
    если_указатель = os.path.realpath(_link(store))
    if если_читает != если_указатель:
        _to_log(
            "движок читает методику не из хранилища версий",
            AUDIT_DATA_DIR=store.live,
            MCP_CHECKLIST_STORE=store.root,
        )
        raise ChecklistError(
            f"Движок читает методику не из хранилища версий: AUDIT_DATA_DIR указывает на "
            f"обычный каталог, а не на указатель «{CURRENT_LINK}» внутри хранилища "
            f"MCP_CHECKLIST_STORE. Публикация переставляет этот указатель — движок её не "
            f"увидит, и проверки продолжат считаться по прежней методике. Чтобы публикация "
            f"работала, AUDIT_DATA_DIR должен указывать на сам указатель «{CURRENT_LINK}» "
            f"внутри хранилища. {IN_LOG}"
        )
    _point_at(store, цель.name)
    _journal(
        store,
        {
            "tenant": tenant,
            "tool": "publish_checklist_version",
            "outcome": "accepted",
            "base_version": прежняя,
            "version": цель.name,
            "refusal": None,
            "note": None,
        },
    )
    return {
        "published": цель.name,
        "previous": прежняя,
        "status": (
            f"checklist version {цель.name} is now the one the audit engine reads; "
            f"inspections already scored stay on their own version and are not recalculated"
        ),
    }
