"""Заметки бота рядом с проверкой: то, чего нет ни у движка, ни в отчёте.

Две вещи, которые бот обязан помнить о проверке, а движок никогда не узнает:

* все кадры, присланные за проверку (задача T068) — в конце проверки нужно
  показать те, что не попали ни в одну запись, иначе они бесследно исчезают;
* последняя названная зона (решение D048) — подставляется догадкой к
  следующему кадру;
* сколько записей было в проверке, когда бот отдал по ней отчёт (задача
  T153) — по этому и видно, что проверка сдана;
* каким сообщением бот показал каждую запись (задача T204) — аудитор правит
  запись ОТВЕТОМ на это сообщение, и без карты «сообщение → запись» ответ
  адресовал бы последнюю запись вместо той, о которой человек говорит;
* из какого сообщения САМОГО АУДИТОРА выросла каждая запись (задача T205) —
  кадр он присылает ответом на свои же слова, и попасть этот кадр обязан в ту
  же запись.

Источник записи (решение D044) здесь когда-то тоже лежал и уехал отсюда
задачей T108: он оказался нужен не одному боту, а всем — в базу источник
доезжает вместе с записью. Теперь его хранит сама проверка
(`domain.add_finding(..., source=...)`, `Finding.source`), и заметки бота о
нём ничего не знают. Второй копии здесь заводить нельзя: разошедшись, две
копии спорили бы о том, за какую формулировку отвечает аудитор.

Заметки обязаны пережить перезапуск бота, поэтому лежат файлом `bot.json` в
той же папке проверки, что и `inspection.json` (см. `src/report/photos.py` —
блок `report` берёт папку проверки тем же способом). Файл заводит и читает
только этот модуль; движок про него не знает и не тронет.

Испорченный файл здесь — не повод молча начать с пустого места: так из
проверки бесследно исчез бы список кадров, а задача T068 существует ровно
затем, чтобы кадры не терялись молча. Поэтому любая нечитаемая или неполная
форма поднимает `BotNotesError`, а не подменяется пустыми заметками.

По той же причине заметки пишутся под блокировкой (T155). Каждое изменение
здесь — «прочитать, дополнить, положить обратно», и два писателя внахлёст
читают одну основу: положивший вторым стирает чужое дополнение. Молча — то
есть теми самыми кадрами, которые T068 обязана не терять (замерено: три
процесса по двадцать кадров дали 36 записанных из 60). Раньше на этом месте
стояло допущение «двух писателей здесь не бывает, обновления идут строго
последовательно»; его снимают перекрытие при перезапуске и вторая копия
сервиса, а файл проверки в этой же папке от того же случая защищён.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Container, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.domain import check_environment
from src.domain.engine import chat_dir
from src.domain.errors import DomainError
from src.domain.state import state_lock

from .errors import BotNotesError

NOTES_FILE_NAME = "bot.json"
#: Четвёртое издание формата: добавлен `origins` — карта «сообщение АУДИТОРА →
#: запись» (T205). Третье добавляло `records` — карту «сообщение бота → запись»
#: (T204). Заметки прежних изданий читаются как прежде: отсутствующий ключ
#: `handed_over_findings` означает «отчёт по этой проверке не отдавался»,
#: отсутствующий `records` — «правку ответом на сообщение адресовать нечем», а
#: отсутствующий `origins` — «кадр ответом на свои слова адресовать нечем».
#: Последние два означают ровно то, что происходило с проверками, начатыми до
#: этих задач: ответ на их сообщения работает как раньше, связыванием
#: комментария с кадром.
SCHEMA = 4

#: «Отчёт по этой проверке не отдавался». Не `0`: ноль записей — законная
#: сданная проверка (чистая точка), и спутать эти два состояния значило бы
#: снова назвать сданную проверку незавершённой.
NEVER_HANDED_OVER = -1


@dataclass(frozen=True)
class SeenFrame:
    """Кадр, который аудитор прислал в чат, — независимо от того, стал ли он записью."""

    message_id: int
    file_id: str


@dataclass(frozen=True)
class RecordMessage:
    """Сообщение и номер записи, о которой оно говорит (T204, T205).

    Связаны они номерами, а не текстом: формулировка записи правится и
    переводится, номер сообщения — нет. Одна запись живёт в нескольких
    сообщениях (фиксация, потом каждая правка), и это не дубль, а норма:
    аудитор отвечает на то, которое видит перед собой.

    Формой пара одинакова у обеих карт заметок, а вот смысл у них разный, и
    поэтому карты две, а не одна с признаком. `records` — сообщения БОТА, и
    ответ на такое сообщение правит запись (D081). `origins` — сообщения самого
    АУДИТОРА, из которых запись выросла, и ответ на такое сообщение кадром
    добавляет кадр в ту же запись. Сложи их в одну карту — и ответ словами на
    своё же голосовое стал бы правкой записи, тогда как правку владелец описал
    ровно как ответ на сообщение бота.
    """

    message_id: int
    n: int


@dataclass(frozen=True)
class Notes:
    """Заметки одной проверки: все присланные кадры и последняя названная зона."""

    #: Все присланные кадры в порядке прихода. Не только те, что стали записью.
    frames: tuple[SeenFrame, ...]
    #: Последняя названная зона; пустая строка — её не было или её забыли.
    zone: str
    #: Сообщения бота, которыми показаны записи (T204), в порядке отправки.
    records: tuple[RecordMessage, ...] = ()
    #: Сообщения аудитора, из которых выросли записи (T205), в порядке прихода.
    #: По ним кадр, присланный ответом на свои же слова, попадает в ту запись,
    #: о которой эти слова были.
    origins: tuple[RecordMessage, ...] = ()
    #: Сколько записей было в проверке, когда бот отдал аудитору отчёт.
    #: `NEVER_HANDED_OVER` — отчёт не отдавался.
    #:
    #: Хранится число, а не «да/нет»: по нему видно, сколько записей уехало в
    #: отданный отчёт. Сверкой с делом оно было до T201 — тогда дописать в
    #: сданную проверку было можно, и расхождение числа означало устаревший
    #: отчёт. Решение D080 дописывание запретило, и признаком сдачи снова стал
    #: сам факт (`handed_over`).
    handed_over_findings: int = NEVER_HANDED_OVER


def notes_path(chat_id: int) -> Path:
    """Файл заметок этого чата: та же папка проверки, что и у `inspection.json`."""
    return chat_dir(chat_id, check_environment()) / NOTES_FILE_NAME


def _frames_from_raw(raw: Any, path: Path) -> tuple[SeenFrame, ...]:
    if raw is None:
        return ()
    try:
        return tuple(
            SeenFrame(message_id=int(item["message_id"]), file_id=str(item["file_id"]))
            for item in raw
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise BotNotesError(f"Список кадров в заметках {path} не похож на список кадров") from exc


def _messages_from_raw(raw: Any, path: Path, what: str) -> tuple[RecordMessage, ...]:
    """Карта «сообщение → запись» из файла. Непонятная форма — отказ.

    По тому же правилу, что и список кадров: молчаливое «начнём с пустой»
    отняло бы у аудитора правку ответом на все записи проверки разом, а узнал
    бы он об этом ровно в тот момент, когда правка понадобилась.

    Разбор один на обе карты (`records` и `origins`): формой они одинаковы, и
    вторая копия разбора отстала бы от первой на первой же правке. Расходится
    только `what` — то, что читает чинящий в тексте отказа.
    """
    if raw is None:
        return ()
    try:
        return tuple(
            RecordMessage(message_id=int(item["message_id"]), n=int(item["n"])) for item in raw
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise BotNotesError(f"{what} в заметках {path} не похожа на карту") from exc


def _handed_over_from_raw(raw: Any, path: Path) -> int:
    """Признак сдачи из файла. Непонятное значение — отказ, а не «не сдавалась».

    Молчаливое умолчание здесь вернуло бы ровно ту неправду, ради которой
    задача T153 и заведена: сданная проверка снова назвалась бы незавершённой.
    """
    if raw is None:
        return NEVER_HANDED_OVER
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BotNotesError(
            f"Признак сдачи в заметках {path} не похож на число записей: {raw!r}"
        ) from exc


def _decode(path: Path) -> dict[str, Any]:
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BotNotesError(f"Заметки бота испорчены и не читаются: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise BotNotesError(f"Заметки бота не похожи на заметки — не объект: {path}")
    return data


def _parse(raw: dict[str, Any], path: Path) -> Notes:
    # Ключ `sources` заметок, написанных до T108, просто не читается: проверки,
    # начатые тогда, ещё в работе, и отказ на незнакомом ключе означал бы, что
    # после обновления бота их стало нечем завершить.
    return Notes(
        frames=_frames_from_raw(raw.get("frames"), path),
        zone=str(raw.get("zone") or ""),
        records=_messages_from_raw(raw.get("records"), path, "Карта сообщений о записях"),
        origins=_messages_from_raw(raw.get("origins"), path, "Карта сообщений аудитора"),
        handed_over_findings=_handed_over_from_raw(raw.get("handed_over_findings"), path),
    )


def read(chat_id: int) -> Notes:
    """Заметки чата. Файла нет — пустые, а не отказ: до первой записи это норма."""
    path = notes_path(chat_id)
    if not path.is_file():
        return Notes(frames=(), zone="")
    return _parse(_decode(path), path)


def handed_over(chat_id: int) -> bool:
    """Отдан ли аудитору отчёт по проверке этого чата.

    До T201 здесь сверялось ещё и число записей: дописанная после сдачи запись
    в отданный отчёт не попадала, и проверка считалась снова в работе. Решение
    владельца D080 эту развилку убрало — «отчеты мы не правим», — и дописывать
    в сданную проверку больше нельзя ничем (`src/bot/sealed.py`). Числу теперь
    не с чем расходиться, а сверка с ним была бы дырой в запрете: заметки,
    пережившие правку старой версией бота, показали бы сданную проверку
    незапечатанной.

    Само число (`Notes.handed_over_findings`) остаётся на месте: по нему видно,
    сколько записей ушло в отданный отчёт.
    """
    return read(chat_id).handed_over_findings != NEVER_HANDED_OVER


def _write(chat_id: int, notes: Notes) -> None:
    """Временный файл рядом, `os.replace` — та же схема, что у движка (`domain/state.py`).

    Зовётся только из-под `_change`: `os.replace` спасает читателя от
    обрезанного файла, но не спасает от потерянного дополнения — для этого
    нужна блокировка вокруг всей тройки «прочитать, дополнить, записать».
    """
    path = notes_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {
        "schema": SCHEMA,
        "zone": notes.zone,
        "frames": [{"message_id": f.message_id, "file_id": f.file_id} for f in notes.frames],
        "records": [{"message_id": r.message_id, "n": r.n} for r in notes.records],
        "origins": [{"message_id": r.message_id, "n": r.n} for r in notes.origins],
        "handed_over_findings": notes.handed_over_findings,
    }
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".bot-notes-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextmanager
def _lock(chat_id: int) -> Iterator[None]:
    """Замок заметок: `bot.json.lock` рядом, тем же `flock`, что и у проверки.

    Берётся `domain.state.state_lock` — второй экземпляр протокола разошёлся бы
    с первым, а опознаётся блокировка по имени файла, а не по коду. Отказ
    приводится к своему типу: чей это контракт, читатель кода должен видеть по
    типу исключения.
    """
    try:
        with state_lock(notes_path(chat_id)):
            yield
    except DomainError as exc:
        raise BotNotesError(f"Заметки бота заняты другим процессом: {notes_path(chat_id)}") from exc


def _change(chat_id: int, изменение: Callable[[Notes], Notes | None]) -> None:
    """Прочитать, дополнить и записать — целиком под замком.

    Именно тройка целиком, а не одна запись: основа, прочитанная до чужой
    записи, дополняется и кладётся поверх неё, и чужое дополнение исчезает
    молча. `None` от `изменение` означает «писать нечего».
    """
    with _lock(chat_id):
        новые = изменение(read(chat_id))
        if новые is not None:
            _write(chat_id, новые)


def reset(chat_id: int) -> None:
    """Начата новая проверка — заметки с нуля. Файла не было — тоже нормальный исход.

    Тоже под замком: снятие файла в обход его позволило бы писателю, уже
    прочитавшему старую основу, положить её обратно — и кадры прошлой проверки
    воскресли бы в новой.
    """
    with _lock(chat_id):
        notes_path(chat_id).unlink(missing_ok=True)


def remember_frames(chat_id: int, frames: Iterable[SeenFrame]) -> None:
    """Дописать кадры в конец списка. Уже известный `file_id` не дублируется.

    Дедуп идёт и против уже сохранённых кадров, и внутри самого добавляемого
    пакета — иначе повтор `file_id` в одном вызове проскочил бы мимо проверки.
    """

    def дополнить(notes: Notes) -> Notes | None:
        seen = {f.file_id for f in notes.frames}
        additions: list[SeenFrame] = []
        for frame in frames:
            if frame.file_id in seen:
                continue
            seen.add(frame.file_id)
            additions.append(frame)
        if not additions:
            return None
        return replace(notes, frames=notes.frames + tuple(additions))

    _change(chat_id, дополнить)


def remember_zone(chat_id: int, zone: str) -> None:
    """Запомнить последнюю названную зону. Пустая строка стирает память о ней."""
    _change(chat_id, lambda notes: replace(notes, zone=zone))


def remember_record(chat_id: int, message_id: int, n: int) -> None:
    """Запомнить, каким сообщением показана запись (T204).

    Повтор того же сообщения не дублируется: показ пересобирается при каждой
    правке, и одна и та же пара пришла бы сюда столько раз, сколько аудитор
    правил запись.
    """

    def дополнить(notes: Notes) -> Notes | None:
        добавляемое = RecordMessage(message_id=message_id, n=n)
        if добавляемое in notes.records:
            return None
        return replace(notes, records=(*notes.records, добавляемое))

    _change(chat_id, дополнить)


def remember_origin(chat_id: int, message_id: int, n: int) -> None:
    """Запомнить, из какого сообщения АУДИТОРА выросла запись (T205).

    Ответом на это сообщение аудитор присылает кадр, и кадр уходит в ту же
    запись, а не заводит новую очередь ожидания. Повтор той же пары не
    дублируется по той же причине, что и у `remember_record`: правка записи
    приходит из нового сообщения, а сама запись остаётся прежней.
    """

    def дополнить(notes: Notes) -> Notes | None:
        добавляемое = RecordMessage(message_id=message_id, n=n)
        if добавляемое in notes.origins:
            return None
        return replace(notes, origins=(*notes.origins, добавляемое))

    _change(chat_id, дополнить)


def _last_for(messages: tuple[RecordMessage, ...], message_id: int) -> int | None:
    """Запись, о которой говорит это сообщение, — или ничего.

    Ищется ПОСЛЕДНЯЯ пара: номер сообщения телеграм не переиспользует, поэтому
    пара здесь ровно одна, — но порядок задан явно, чтобы поведение не зависело
    от порядка записи в файл.
    """
    for known in reversed(messages):
        if known.message_id == message_id:
            return known.n
    return None


def record_of(chat_id: int, message_id: int) -> int | None:
    """Запись, о которой говорит это сообщение бота, — или ничего.

    «Ничего» — обычный ответ, а не отказ: аудитор отвечает и на кадры, и на
    служебные сообщения бота, и такой ответ обязан работать как раньше.
    """
    return _last_for(read(chat_id).records, message_id)


def origin_of(chat_id: int, message_id: int) -> int | None:
    """Запись, выросшая из этого сообщения аудитора, — или ничего (T205).

    «Ничего» — обычный ответ: ответить кадром можно на что угодно, и такой
    ответ обязан работать как раньше — обычным кадром с вопросом «Разобрать?».
    """
    return _last_for(read(chat_id).origins, message_id)


def mark_handed_over(chat_id: int, findings: int) -> None:
    """Запомнить, что отчёт по этой проверке отдан аудитору (T153).

    Зовётся ПОСЛЕ того, как PDF действительно ушёл в чат, а не по нажатию
    кнопки: сданной проверку делает отданный отчёт, а не намерение его собрать.
    """
    _change(chat_id, lambda notes: replace(notes, handed_over_findings=findings))


def unclaimed(chat_id: int, used: Container[str]) -> tuple[SeenFrame, ...]:
    """Кадры, чьего `file_id` нет в `used`, — в порядке прихода."""
    notes = read(chat_id)
    return tuple(f for f in notes.frames if f.file_id not in used)
