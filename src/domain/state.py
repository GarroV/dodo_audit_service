"""Состояние проверки: чтение, старт и собственные поля блока.

Состояние — файл `inspection.json` в папке на чат (решение D007). Открывает его
только этот блок; остальные ходят через функции отсюда, чтобы переезд на базу
был заменой одного слоя, а не переписыванием бота.

Часть полей ведёт движок (шапка и записи), часть — блок: языки интерфейса и
речи, версия методики, арендатор. Свои поля лежат в том же файле под ключом
`domain`: проверка должна быть одним объектом, иначе половина её сведений
потеряется при первом же переносе. Движок чужие ключи не трогает — он читает
файл целиком и целиком же пишет обратно.
"""

from __future__ import annotations

# fcntl есть только в POSIX. Продукт работает в linux-контейнере (решение D003),
# разработка — на macOS; отдельной ветки под Windows у блока нет намеренно.
import fcntl
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checklist import checklist_version
from .config import DEFAULT_LANG, Settings, check_environment
from .engine import option, run_audit, state_file
from .errors import (
    ChecklistVersionMismatch,
    DomainError,
    InspectionNotStarted,
    ValidationError,
)
from .kinds import kind_title
from .models import SOURCES, TEXT_LANGS, Finding, Inspection

logger = logging.getLogger(__name__)

#: Ключ со сведениями, которых у движка нет. `schema` — чтобы будущий переезд
#: состояния (в том числе в базу) видел, какой формы данные ему достались.
DOMAIN_KEY = "domain"
SCHEMA = 1

#: Источники записей внутри блока `domain`: номер записи → значение из `SOURCES`
#: (решение D044). Отдельным словарём, а не полем внутри записи движка: записи
#: ведёт движок, и дописывать в его структуры свои ключи — способ однажды их
#: потерять. Здесь же они переживают слив в базу вместе со всей проверкой, тогда
#: как заметки бота обнуляются на старте следующей проверки того же чата.
SOURCES_KEY = "sources"

#: След перестановок отметки версии: список записей `{from, to, at}`. Лежит в
#: самой проверке, а не в журнале процесса, потому что отвечать «по какой
#: методике это считали» придётся через месяцы — и отвечать по файлу проверки,
#: который к тому времени уже уехал в базу, а не по логам стенда.
HISTORY_KEY = "version_history"

#: Арендатор по умолчанию. Функций мультиарендности в MVP нет (решение D005),
#: но поле есть с первого дня: задним числом его в готовые проверки не вписать.
DEFAULT_TENANT = "default"

LANG_CODE = re.compile(r"^[a-z]{2}$")

#: Ожидание блокировки состояния. Столько же ждёт движок — разные значения
#: означали бы, что при заторе первым сдаётся тот, кто просто медленнее.
LOCK_TIMEOUT_SEC = 30.0


def _clean_lang(value: str, field: str) -> str:
    """Язык — параметр, но не любая строка: в состояние идёт код вида `ru`."""
    lang = (value or "").strip().lower()
    if not LANG_CODE.match(lang):
        raise ValidationError(
            f"Язык «{value}» для поля «{field}» не похож на код языка: нужен код из двух букв, "
            f"например ru или en"
        )
    return lang


@contextmanager
def state_lock(path: Path) -> Iterator[None]:
    """Та же блокировка, что берёт движок: файл `<состояние>.lock`, `flock`.

    Протокол приходится повторять, а не переиспользовать: движок вызывается
    подпроцессом и своих функций наружу не отдаёт. Разойтись эти две реализации
    не могут — блокировка опознаётся по имени файла, а не по коду.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.parent / (path.name + ".lock")
    with lock_path.open("a+") as fh:
        deadline = time.time() + LOCK_TIMEOUT_SEC
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > deadline:
                    raise DomainError(
                        f"Состояние {path} занято другим процессом дольше "
                        f"{LOCK_TIMEOUT_SEC:g} с — запись отменена"
                    ) from None
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _write_atomic(path: Path, raw: Mapping[str, Any]) -> None:
    """Временный файл рядом, fsync, `os.replace` — как пишет движок.

    Запись поверх файла оставляла обрезанный JSON, когда альбом приходил
    несколькими кадрами разом.
    """
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".inspection-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_raw(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DomainError(
            f"Состояние проверки испорчено и прочитать его нельзя: {path} ({exc}). "
            f"Файл не тронут — разбирать руками"
        ) from exc
    if not isinstance(data, dict):
        raise DomainError(f"Состояние проверки не похоже на проверку: {path}")
    return data


def patch_domain_block(path: Path, values: Mapping[str, Any]) -> None:
    """Дописать поля блока в состояние, не трогая того, что ведёт движок."""
    with state_lock(path):
        raw = _read_raw(path)
        block = dict(raw.get(DOMAIN_KEY) or {})
        block.update(values)
        raw[DOMAIN_KEY] = block
        _write_atomic(path, raw)


def read_sources(raw: Mapping[str, Any], path: Path) -> dict[int, str]:
    """Источники записей из блока `domain`. Непонятное значение — отказ.

    Прочитанное «неизвестно что» уехало бы в базу как источник записи и стало бы
    там неотличимо от настоящего: раз значение нельзя объяснить, лучше отказать.
    """
    block: Mapping[str, Any] = raw.get(DOMAIN_KEY) or {}
    result: dict[int, str] = {}
    for key, value in dict(block.get(SOURCES_KEY) or {}).items():
        try:
            n = int(key)
        except (TypeError, ValueError):
            raise DomainError(
                f"Номер записи «{key}» в источниках {path} не похож на число"
            ) from None
        if value not in SOURCES:
            raise DomainError(f"Источник «{value}» записи #{n} в {path} не из {SOURCES}")
        result[n] = str(value)
    return result


def remember_source(path: Path, n: int, source: str) -> None:
    """Записать источник записи №`n`. Пустой источник не пишется: он и так пуст."""
    if not source:
        return
    with state_lock(path):
        raw = _read_raw(path)
        block = dict(raw.get(DOMAIN_KEY) or {})
        sources = dict(block.get(SOURCES_KEY) or {})
        sources[str(n)] = source
        block[SOURCES_KEY] = sources
        raw[DOMAIN_KEY] = block
        _write_atomic(path, raw)


def forget_source(path: Path, n: int) -> None:
    """Запись удалена — источник больше не о чём. Неизвестный номер не отказ."""
    with state_lock(path):
        raw = _read_raw(path)
        block = dict(raw.get(DOMAIN_KEY) or {})
        sources = dict(block.get(SOURCES_KEY) or {})
        if sources.pop(str(n), None) is None:
            return
        block[SOURCES_KEY] = sources
        raw[DOMAIN_KEY] = block
        _write_atomic(path, raw)


def _finding(raw: Mapping[str, Any], sources: Mapping[int, str]) -> Finding:
    photos = [p for p in (raw.get("photos") or []) if p]
    if not photos and raw.get("photo"):
        photos = [str(raw["photo"])]  # старая форма записи: одно фото строкой
    return Finding(
        n=int(raw["n"]),
        code=str(raw.get("qid") or ""),
        level=str(raw.get("level") or ""),
        zone=str(raw.get("zone") or ""),
        text=str(raw.get("evidence") or ""),
        comment=str(raw.get("comment") or ""),
        photos=[str(p) for p in photos],
        zone_unusual=bool(raw.get("zone_unusual")),
        source=sources.get(int(raw["n"]), ""),
    )


def _inspection(chat_id: int, raw: Mapping[str, Any], path: Path) -> Inspection:
    meta: Mapping[str, Any] = raw.get("meta") or {}
    block: Mapping[str, Any] = raw.get(DOMAIN_KEY) or {}
    sources = read_sources(raw, path)
    return Inspection(
        chat_id=chat_id,
        unit=str(meta.get("unit") or ""),
        # Вид проверки — КОД, и лежит он в блоке, а не в шапке движка (T152).
        # В шапке (`meta.type`) стоит перевод этого кода на язык отчёта: её
        # печатает движок партнёру, и читать оттуда обратно нельзя — там уже
        # формулировка, а формулировки переводятся и правятся, коды нет.
        kind=str(block.get("kind") or ""),
        date=str(meta.get("date") or ""),
        # Язык отчёта хранится там, откуда его берёт сборка отчёта, — в шапке
        # движка. Второй копии у блока нет намеренно: разошлись бы.
        report_lang=str(meta.get("lang") or DEFAULT_LANG),
        ui_lang=str(block.get("ui_lang") or DEFAULT_LANG),
        speech_lang=str(block.get("speech_lang") or DEFAULT_LANG),
        checklist_version=str(block.get("checklist_version") or ""),
        tenant=str(block.get("tenant") or DEFAULT_TENANT),
        city=str(meta.get("city") or ""),
        partner=str(meta.get("partner") or ""),
        contact=str(meta.get("contact") or ""),
        auditor=str(meta.get("auditor") or ""),
        findings=[_finding(f, sources) for f in (raw.get("findings") or [])],
    )


def read_state(chat_id: int, settings: Settings) -> Inspection | None:
    path = state_file(chat_id, settings)
    if not path.is_file():
        return None
    return _inspection(chat_id, _read_raw(path), path)


def get_state(chat_id: int) -> Inspection | None:
    """Проверка этого чата или `None`, если она не начата."""
    return read_state(chat_id, check_environment())


def start_inspection(
    chat_id: int,
    unit: str,
    kind: str,
    report_lang: str,
    *,
    ui_lang: str = DEFAULT_LANG,
    speech_lang: str = DEFAULT_LANG,
    date: str | None = None,
    city: str = "",
    partner: str = "",
    contact: str = "",
    auditor: str = "",
    tenant: str = DEFAULT_TENANT,
) -> Inspection:
    """Начать проверку в чате.

    Существующее состояние движок затирает целиком и молча — спрашивать
    аудитора «продолжить или начать заново» обязан бот (задача T052), поэтому
    здесь такой защиты нет. Дату по умолчанию (сегодня) ставит движок.
    """
    settings = check_environment()
    # Вид проверки приходит КОДОМ и кодом же остаётся в проверке (T152). В
    # шапку движка уходит перевод: её он печатает партнёру, и подставить слово
    # больше негде — состояние он читает сам. Перевод берётся по языку ОТЧЁТА,
    # то есть по тому же `meta.lang`, с которым движок эту шапку и напечатает,
    # поэтому сопоставлять там строки не с чем и незачем.
    #
    # Отказ на неизвестном коде идёт ДО движка: иначе проверка окажется
    # начатой, а связать её вид будет не с чем.
    #
    # Язык отчёта проверяется здесь же и первым, хотя его проверяет и движок:
    # без слова на этом языке шапку не заполнить, а отказ «вид проверки не
    # заведён на языке sr» объясняет человеку не то, что он сделал не так.
    lang = report_lang.strip().lower()
    if lang not in TEXT_LANGS:
        raise ValidationError(
            f"Язык отчёта «{report_lang}» в методике не заведён. Доступны: {', '.join(TEXT_LANGS)}"
        )
    # Вид проверки уезжает движку КОДОМ, а не словом (T177): слово, записанное
    # в шапку при заведении, невозможно перевести при печати на другом языке —
    # именно так партнёр получал в русском письме английское слово. Перевод
    # делает движок, по тому языку, на котором печатает.
    #
    # Язык всё равно проверяется здесь и первым: без заведённого языка отказ
    # придёт от движка и объяснит человеку не то, что он сделал не так.
    kind_title(kind, lang)
    args = [
        "init",
        option("unit", unit),
        option("kind", kind),
        option("lang", lang),
        option("city", city),
        option("partner", partner),
        option("contact", contact),
        option("auditor", auditor),
    ]
    if date is not None:
        args.append(option("date", date))
    # Свои языки проверяем до вызова: иначе проверка окажется начатой, а поля
    # блока — нет, и состояние останется наполовину заполненным.
    block = {
        "schema": SCHEMA,
        "chat_id": chat_id,
        "tenant": tenant,
        "kind": kind,
        "checklist_version": checklist_version(),
        "ui_lang": _clean_lang(ui_lang, "язык интерфейса"),
        "speech_lang": _clean_lang(speech_lang, "язык речи аудитора"),
    }
    run_audit(args, chat_id=chat_id, settings=settings, create=True)
    patch_domain_block(state_file(chat_id, settings), block)
    started = read_state(chat_id, settings)
    if started is None:
        raise DomainError(
            f"Движок отчитался об успехе, но состояния нет: {state_file(chat_id, settings)}"
        )
    return started


def assert_checklist_version(chat_id: int, settings: Settings) -> None:
    """Отказать, если проверку считают не по той методике, которой она помечена.

    Отметка ставится на старте, а считают потом. Между этими моментами методику
    успевают издать заново: публикация версии (D049) переставляет указатель в
    каталоге, откуда движок читает данные, и делает это под живой проверкой.
    Дальше движок честно считает по новому, а в проверке остаётся прежняя
    отметка — и `push_inspection` уносит эту пару в базу, где запись выглядит
    сравнимой с соседними, не будучи ею.

    Пустая отметка — не расхождение: так помечены проверки, заведённые до
    версионирования, и отказать по ним значило бы сломать их задним числом.

    Проверки нет — молчим: об этом отказывает движок (`InspectionNotStarted`),
    и подменять его отказ своим здесь нечем.
    """
    state = read_state(chat_id, settings)
    if state is None or not state.checklist_version:
        return
    current = checklist_version()
    if state.checklist_version == current:
        return
    raise ChecklistVersionMismatch(
        f"Проверка начата по методике {state.checklist_version}, а движок сейчас читает "
        f"{current}: методику переиздали, пока проверка шла. Считать в таком виде нельзя — "
        f"оценка вышла бы по новой методике под старой отметкой, и в базе такая запись "
        f"выглядела бы сравнимой с остальными. Пересчитать по прежней методике здесь нечем: "
        f"её данных на диске больше нет. Решает человек: перевести проверку на действующую "
        f"методику (sync_checklist_version) — перевод останется в проверке следом — или "
        f"вернуть прежнюю версию в AUDIT_DATA_DIR и досчитать по ней",
        recorded=state.checklist_version,
        current=current,
    )


def sync_checklist_version(chat_id: int) -> Inspection:
    """Перевести проверку на действующую методику, оставив след в ней самой.

    Это второй выход из расхождения версий, и он намеренно отдельный вызов, а не
    молчаливое поведение подсчёта: перевод меняет то, чем проверка станет
    измеряться, и такое решение принимает человек. След (`domain.version_history`)
    хранится в проверке, а не в логах стенда: отвечать «по какой методике это
    посчитали» придётся тогда, когда логов уже не будет, а проверка будет.

    Версия та же — переставлять нечего, и следа не появляется: пустая запись в
    истории означала бы перевод, которого не было.
    """
    settings = check_environment()
    path = state_file(chat_id, settings)
    if not path.is_file():
        raise InspectionNotStarted(
            f"В этом чате проверка не начата — нет {path}. Переводить нечего"
        )
    current = checklist_version()
    with state_lock(path):
        raw = _read_raw(path)
        block = dict(raw.get(DOMAIN_KEY) or {})
        recorded = str(block.get("checklist_version") or "")
        if recorded != current:
            history = [dict(item) for item in (block.get(HISTORY_KEY) or [])]
            history.append(
                {
                    "from": recorded,
                    "to": current,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
            )
            block["checklist_version"] = current
            block[HISTORY_KEY] = history
            raw[DOMAIN_KEY] = block
            _write_atomic(path, raw)
            logger.warning(
                "проверка чата %s переведена с методики %s на %s", chat_id, recorded or "—", current
            )
    state = read_state(chat_id, settings)
    if state is None:
        raise DomainError(f"Отметка версии переставлена, но состояния нет: {path}")
    return state
