#!/usr/bin/env python3
"""T202: цена решения D081 «кадр с комментарием разбирается по комментарию».

До D081 `needs_photo` смотрело на карту слов: кадр не отправлялся в модель
ровно тогда, когда карта поднимала один пункт с единственным допустимым
классом (см. историю в `classify.py::needs_photo`). На сегодняшней карте это
не случалось НИ РАЗУ: прогон старого правила по семнадцати боевым записям дал
17 отправок кадра из 17 (05.09.2026). Единственная запись с одним поднятым
пунктом уезжала с кадром по второй причине — у пункта больше одного
допустимого класса. Владелец от этой развилки отказался: «фото с комментм - обрабатываем
коммент», и `needs_photo` теперь смотрит только на то, пуст ли комментарий.
Замер ниже кладёт цену этого решения в числа, а не оставляет её мнением: один
и тот же вход прогоняется через ДВА плеча —

* **A «как было»** — комментарий и кадр, старое правило решает, нужен ли кадр;
* **B «как стало»** — только комментарий, кадра в запросе нет вовсе.

**Корпус.** Свой загрузчик по `examples/*/inspection.json` (симлинк на боевые
данные, сами данные вне git — решение D002, пустой корпус на чужой машине
законный исход). Он не переиспользует `tools.fastpath_measure.load_records`:
тому загрузчику неоткуда взять путь к кадру — он меряет только текст, а этому
кадр нужен как раз для плеча A. Зона-подсказка при этом ПЕРЕИСПОЛЬЗУЕТСЯ
(`tools.fastpath_measure.hints_bot`) — свою копию правила «слова, иначе память
проверки» здесь заводить нельзя, она разошлась бы с ботом молча.

**Плечо A строит СТАРОЕ правило заново**, а не зовёт продукт: `needs_photo`
после D081 приняло другую сигнатуру (без `cue_hits`), и просто вызвать функцию
продукта для этого плеча уже нельзя. `_old_needs_photo` — снятая копия,
намеренно застывшая: переносить её обратно в `classify.py` нельзя, это и есть
измеряемое старое поведение, а не альтернатива новому.

**Кадр в плече A подключается ПОДМЕНОЙ `classify.needs_photo`**, а не веткой
кода снаружи. Причина этого решения — в первую очередь честность замера:
`classify()` решает `use_photo = photo is not None and needs_photo(note)`
внутри себя, и если note не пуст (а он не пуст почти всегда), ТЕКУЩЕЕ правило
вернёт `False` независимо от того, что мы передадим аргументом `photo`, — то
есть без подмены плечо A всегда выродилось бы в плечо B. Подробности
безопасности самой подмены (это разделяемая память процесса, не аргумент) —
в докстринге `_forced_needs_photo`.

**Отказ модели** (`RecognizeError`) — измеримый исход, а не сбой прогона:
строка с пустым ответом и текстом ошибки, остальные записи считаются дальше.
**Но отказ и промах — не одно и то же**, и отчёт обязан их различать вслух
(T224): в строке с отказом запроса не было вовсе, поэтому все три мерки
попадания у неё нулевые, а токенов ноль. Молча смешать это с настоящим
промахом значит напечатать «код верный 0 %» и «разница входных токенов +0» на
прогоне, где не состоялось ни одного вызова, — числа выглядят замером, не
будучи им. Поэтому при любом отказе печатается предупреждение перед таблицами,
а вывод про цену кадра снимает своё «это и есть цена»; когда не удался ни один
вызов, код возврата 1, а не 0.

Ни одного обращения к сети вне `classify()`: загрузчик корпуса и старое
правило читают только локальные файлы методики и `examples/`.

**Про ключ модели — точно, а не «его тут нет».** Сам инструмент ключ ниоткуда
не берёт и никуда не кладёт: в его структурах данных `OPENAI_API_KEY` не
появляется. Но текст отказа провайдера цитируется как есть (`LegOutcome.error`
→ таблица и `--out`), а провайдер в ответе 401 повторяет ключ ЗАМАСКИРОВАННЫМ
(«Incorrect API key provided: sk-stub-**********-key», проверено 05.09.2026).
Это не пригодный к использованию секрет, но и не «ключа тут не бывает»:
`--out` — местный файл для разбора, а не то, что кладут в публичный
репозиторий. Резать текст отказа своим выражением здесь не стали намеренно:
форма ключа — знание о провайдере, а оно по решению D010 живёт в одном месте
(`src/recognize/client.py`), и копия этого знания в замере разошлась бы с ним
молча.

Запуск:
    python tools/comment_only_measure.py [--root PATH] [--out PATH] [--limit N]

Окружение: `AUDIT_DATA_DIR` инструмент подставляет сам (методика репозитория),
`STATE_DIR` обязан задать запускающий — его требует любое обращение к методике
(`src/domain/config.py`). Не задан — внятный отказ и код 2, а не трассировка.

Коды возврата: 0 — прогон прошёл, 1 — не удался ни один вызов модели,
2 — боевых данных нет или окружение не настроено.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.domain.errors import DomainError  # noqa: E402
from src.recognize import classify as classify_module  # noqa: E402
from src.recognize.classify import classify  # noqa: E402
from src.recognize.errors import RecognizeError  # noqa: E402
from src.recognize.schema import picks_for  # noqa: E402
from src.recognize.shortlist import shortlist  # noqa: E402
from tools.fastpath_measure import (  # noqa: E402
    FROM_MEMORY,
    FROM_NOWHERE,
    FROM_WORDS,
    Hint,
    hints_bot,
)

#: Сколько запросов идёт одновременно — как в `tools/bench_run.py`. Разовый
#: замер на 17 записях, не боевая нагрузка.
CONCURRENCY = 6


@dataclass(frozen=True)
class Record:
    """Одна боевая запись: что подтвердил аудитор, и путь к её первому кадру.

    Поля `code/zone/note/source` намеренно совпадают по имени с
    `tools.fastpath_measure.Record` — этого достаточно, чтобы `hints_bot`
    приняла записи отсюда без переходника: она читает объекты по атрибутам, а
    не по типу.
    """

    code: str
    level: str
    zone: str
    note: str
    photo: Path
    source: str


@dataclass(frozen=True)
class Corpus:
    """Что нашёл загрузчик: записи с кадром и то, что пришлось пропустить."""

    records: tuple[Record, ...]
    #: Записи без единой фотографии — их быть не должно, но молча терять
    #: запись нельзя (задание требует явной строки в отчёте).
    skipped: tuple[str, ...]


def load_corpus(root: Path) -> Corpus:
    """Боевые записи `examples/*/inspection.json`, в порядке файла и папки.

    Порядок сохраняется целиком: `hints_bot` моделирует память бота о зоне
    предыдущей записи той же проверки (D048), и перемешанные записи значили
    бы другой обход точки.
    """
    records: list[Record] = []
    skipped: list[str] = []
    for path in sorted(root.glob("examples/*/inspection.json")):
        source = path.parent.name
        data = json.loads(path.read_text(encoding="utf-8"))
        for finding in data.get("findings", []):
            photos = finding.get("photos") or []
            if not photos:
                skipped.append(
                    f"{source}/{finding.get('qid', '?')}: пропущена — фотографий нет "
                    f"(такого быть не должно, но молча терять запись нельзя)"
                )
                continue
            records.append(
                Record(
                    code=finding["qid"],
                    level=finding["level"],
                    zone=finding["zone"],
                    note=finding["evidence"],
                    photo=path.parent / photos[0],
                    source=source,
                )
            )
    return Corpus(records=tuple(records), skipped=tuple(skipped))


def _old_needs_photo(note: str, cue_hits: tuple[str, ...]) -> bool:
    """Правило `needs_photo` ДО решения D081 — то, что разбирает плечо A.

    Три отказа от экономии кадра: комментария нет вовсе, слова подняли не
    ровно один пункт, или подняли один пункт — но с выбором класса (кадр
    разрешал бы спор). Кадр пропускался мимо модели только в оставшемся
    случае: один пункт, один допустимый класс, добавить кадру нечего. Это
    буквальная копия прежнего тела `classify.needs_photo` — застывшая
    нарочно, обратно в продукт она не возвращается, это и есть измеряемое
    старое поведение.
    """
    if not note.strip():
        return True
    if len(cue_hits) != 1:
        return True
    return len(picks_for(cue_hits)) != 2


@dataclass(frozen=True)
class LegOutcome:
    """Один вызов `classify` — одно плечо на одной записи.

    `used_photo` берётся из `Suggestion.used_photo`, а не из нашей заготовки:
    для плеча A это независимая сверка, что подмена `needs_photo`
    действительно подействовала, а не осталась незамеченной. `None` — вызов
    оборвался отказом, и что решил `classify()` до отказа, узнать нельзя.
    """

    top_code: str | None
    top_level: str | None
    top_zone: str | None
    used_photo: bool | None
    usage: dict[str, int]
    error: str = ""


def _call_leg(note: str, photo: bytes | None, zone_hint: str | None) -> LegOutcome:
    """Один вызов `classify`. Отказ модели не роняет прогон — это исход, а не сбой."""
    try:
        suggestion = classify(note, photo=photo, zone_hint=zone_hint)
    except RecognizeError as exc:
        return LegOutcome(
            top_code=None,
            top_level=None,
            top_zone=None,
            used_photo=None,
            usage={},
            error=str(exc),
        )
    top = suggestion.top()
    return LegOutcome(
        top_code=top.code if top else None,
        top_level=top.level if top else None,
        top_zone=top.zone if top else None,
        used_photo=suggestion.used_photo,
        usage=dict(suggestion.usage),
    )


#: Подмена `classify.needs_photo` — состояние ОДНОГО модуля на весь процесс,
#: и потому шесть параллельных задач обязаны сериализоваться на этом шаге, а
#: не только «плечо A с плечом B одной записи» (см. `_forced_needs_photo`).
_PATCH_LOCK = threading.Lock()


@contextmanager
def _forced_needs_photo(value: bool) -> Iterator[None]:
    """Подменить `classify.needs_photo` на время одного вызова — плечо A.

    Это ГЛОБАЛЬНАЯ подмена атрибута модуля, а не аргумент функции: пока она
    стоит, ЛЮБОЙ поток, зовущий `classify()`, увидит именно её. Опасность не
    только в том, что плечи A и B ОДНОЙ записи считались бы параллельно
    (поэтому `run_case` вообще не разбивает их на разные задачи) — опасность
    ещё и в том, что плечи A РАЗНЫХ записей могут запуститься в разных потоках
    одновременно (пул из `CONCURRENCY` воркеров). `classify()` читает
    `needs_photo` не первой же строкой: до этого она успевает сходить в
    `load_recognize_settings()`, `shortlist()`, `list_zones()` — окно между
    установкой подмены и её фактическим чтением не бесконечно малое, GIL
    успевает переключиться на другой поток. Без блокировки два потока с
    разными старыми значениями («кадр нужен» и «не нужен») могли бы обменяться
    подсказками, и плечо A перестало бы мерить именно старое правило.

    Лок держится на всё время вызова `classify()` плеча A, то есть плечи A
    разных записей фактически считаются по одному — это сознательная цена
    правильности, а не недосмотр. Плечо B кадр никогда не передаёт
    (`photo=None`), поэтому `photo is not None and needs_photo(note)`
    останавливается на первом условии и даже не читает подменённую функцию —
    блокировка ему не нужна и не даётся.
    """
    with _PATCH_LOCK:
        original = classify_module.needs_photo
        classify_module.needs_photo = lambda _note: value
        try:
            yield
        finally:
            classify_module.needs_photo = original


def _leg_a(record: Record, zone_hint: str | None, old_value: bool) -> LegOutcome:
    """Плечо A «как было»: кадр читается всегда, отправлять ли его — решает подмена.

    Кадр читается с диска безусловно, а не только когда `old_value` истинно:
    если бы это решение принимал сам загрузчик, он предугадывал бы то самое
    поведение `classify()`, которое подмена как раз обязана проверить, а не
    подменить собой.
    """
    photo_bytes = record.photo.read_bytes()
    with _forced_needs_photo(old_value):
        return _call_leg(record.note, photo_bytes, zone_hint)


def _leg_b(record: Record, zone_hint: str | None) -> LegOutcome:
    """Плечо B «как стало» (D081): кадра в запросе нет — ни при каких условиях."""
    return _call_leg(record.note, None, zone_hint)


@dataclass(frozen=True)
class CaseOutcome:
    """Обе руки одной записи: один и тот же вход, разное решение о кадре."""

    record: Record
    zone_hint: str | None
    hint_source: str
    old_needs_photo: bool
    a: LegOutcome
    b: LegOutcome


def run_case(record: Record, hint: Hint) -> CaseOutcome:
    """Оба плеча одной записи — ПОДРЯД и в одной задаче, не в двух параллельных.

    Зона-подсказка (`hint`) одна на оба плеча — иначе сравнение было бы
    нечестным, разница читалась бы как эффект кадра, а на деле была бы
    эффектом разных зон. `cue_hits` пересчитывается через `shortlist()` заново
    ЗДЕСЬ, а не берётся из `classify()`: после D081 у продукта его взять
    неоткуда, и без этого пересчёта старое правило нечем было бы кормить.
    """
    cue_hits = shortlist(record.note, hint.zone).cue_hits
    old_value = _old_needs_photo(record.note, cue_hits)
    outcome_a = _leg_a(record, hint.zone, old_value)
    outcome_b = _leg_b(record, hint.zone)
    return CaseOutcome(
        record=record,
        zone_hint=hint.zone,
        hint_source=hint.source,
        old_needs_photo=old_value,
        a=outcome_a,
        b=outcome_b,
    )


def run_all(records: Sequence[Record], hints: Sequence[Hint]) -> tuple[CaseOutcome, ...]:
    """Прогнать обе руки по всем записям, 6 одновременных задач (как `bench_run.py`).

    Задача в пуле — ОДНА НА ЗАПИСЬ (а не одна на плечо, как `bench_run.py`
    делает для кадр×модель): иначе плечи A и B одной записи, а с ними и плечи
    A разных записей, оказались бы независимыми задачами и могли бы
    перетоптать общую подмену `needs_photo` (см. `_forced_needs_photo`).
    """
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {
            pool.submit(run_case, record, hint): index
            for index, (record, hint) in enumerate(zip(records, hints, strict=True))
        }
        results: dict[int, CaseOutcome] = {}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return tuple(results[i] for i in range(len(records)))


def _code_match(leg: LegOutcome, record: Record) -> bool:
    return leg.top_code is not None and leg.top_code == record.code


def _exact_match(leg: LegOutcome, record: Record) -> bool:
    return _code_match(leg, record) and leg.top_level == record.level


def _zone_match(leg: LegOutcome, record: Record) -> bool:
    return leg.top_zone is not None and leg.top_zone == record.zone


@dataclass(frozen=True)
class LegTotals:
    """Числа по одному плечу — три отдельные мерки попадания, не одна."""

    title: str
    n: int
    code_hits: int
    exact_hits: int
    zone_hits: int
    errors: int
    usage: Counter[str]

    def share(self, hits: int) -> float:
        return hits / self.n * 100 if self.n else 0.0

    def as_dict(self) -> dict[str, object]:
        """Плоский словарь для `--out` — `Counter` не отдаётся `asdict` как есть."""
        return {
            "title": self.title,
            "n": self.n,
            "code_hits": self.code_hits,
            "exact_hits": self.exact_hits,
            "zone_hits": self.zone_hits,
            "errors": self.errors,
            "usage": dict(self.usage),
        }


def _leg_totals(
    title: str, outcomes: Sequence[CaseOutcome], pick: Callable[[CaseOutcome], LegOutcome]
) -> LegTotals:
    code_hits = exact_hits = zone_hits = errors = 0
    usage: Counter[str] = Counter()
    for outcome in outcomes:
        leg = pick(outcome)
        if leg.error:
            errors += 1
        if _code_match(leg, outcome.record):
            code_hits += 1
        if _exact_match(leg, outcome.record):
            exact_hits += 1
        if _zone_match(leg, outcome.record):
            zone_hits += 1
        usage.update(leg.usage)  # ключи как есть (input/output) — не переименовываются
    return LegTotals(
        title=title,
        n=len(outcomes),
        code_hits=code_hits,
        exact_hits=exact_hits,
        zone_hits=zone_hits,
        errors=errors,
        usage=usage,
    )


def _fmt_pick(code: str | None, level: str | None, zone: str | None) -> str:
    if code is None:
        return "—"
    return f"{code}:{level}@{zone}"


def _fmt_leg(leg: LegOutcome) -> str:
    if leg.error:
        return f"ОШИБКА: {leg.error[:60]}"
    return _fmt_pick(leg.top_code, leg.top_level, leg.top_zone)


def _table(outcomes: Sequence[CaseOutcome]) -> list[str]:
    lines = [
        "| Проверка | Эталон | Зона-подсказка | Кадр факт. (A) | Плечо A (как было) | "
        "Плечо B (как стало) |",
        "|---|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        expected = f"{outcome.record.code}:{outcome.record.level}@{outcome.record.zone}"
        hint = f"{outcome.zone_hint or '—'} ({outcome.hint_source})"
        frame = "да" if outcome.a.used_photo else "нет" if outcome.a.used_photo is False else "?"
        lines.append(
            f"| {outcome.record.source} | {expected} | {hint} | {frame} | "
            f"{_fmt_leg(outcome.a)} | {_fmt_leg(outcome.b)} |"
        )
    return lines


def _hint_sources_line(outcomes: Sequence[CaseOutcome]) -> str:
    counted = Counter(o.hint_source for o in outcomes)
    return (
        f"Откуда бот брал зону-подсказку — {FROM_WORDS}: {counted.get(FROM_WORDS, 0)}, "
        f"{FROM_MEMORY}: {counted.get(FROM_MEMORY, 0)}, "
        f"{FROM_NOWHERE}: {counted.get(FROM_NOWHERE, 0)} (всего записей: {len(outcomes)})."
    )


def _totals_table(a: LegTotals, b: LegTotals) -> list[str]:
    lines = [
        "| Плечо | Записей | Код верный | Код+класс верно | Зона верна | Ошибок | "
        "Токены вход/выход (сумма) |",
        "|---|---|---|---|---|---|---|",
    ]
    for totals in (a, b):
        lines.append(
            f"| {totals.title} | {totals.n} | "
            f"{totals.code_hits} ({totals.share(totals.code_hits):.0f}%) | "
            f"{totals.exact_hits} ({totals.share(totals.exact_hits):.0f}%) | "
            f"{totals.zone_hits} ({totals.share(totals.zone_hits):.0f}%) | {totals.errors} | "
            f"{totals.usage.get('input', 0)} / {totals.usage.get('output', 0)} |"
        )
    return lines


def _token_diff_line(a: LegTotals, b: LegTotals) -> str:
    """Цена кадра в токенах. При отказах — та же разность, но без слова «цена».

    Разность считается по тем же суммам, что стоят в таблице, и не
    пересчитывается по удавшимся вызовам отдельно: два плеча с разным числом
    отказов дали бы «цену кадра», посчитанную по разным записям, а это уже не
    сравнение. Поэтому при отказах число печатается, но названо тем, чем
    является, — неполной суммой.
    """
    diff = a.usage.get("input", 0) - b.usage.get("input", 0)
    if a.errors or b.errors:
        return (
            f"Разница входных токенов (A − B): {diff:+d}. Ценой кадра это число НЕ является: "
            f"в прогоне есть отказы, и токены по ним не начислялись вовсе."
        )
    return (
        f"Разница входных токенов (A − B): {diff:+d}. Это и есть цена кадра — то, ради "
        f"экономии чего принято D081."
    )


def _errors_warning(a: LegTotals, b: LegTotals) -> str:
    """Предупреждение про отказы — до таблиц, а не примечанием под ними (T224).

    Пустая строка, когда отказов нет: молчание здесь и есть сообщение «числа
    ниже посчитаны по состоявшимся вызовам».

    Почему это вообще нужно. Отказ идёт в отчёт строкой с пустым ответом, и по
    каждой мерке попадания такая строка считается промахом — иначе доли
    считались бы по разному числу записей в двух плечах. Само по себе это
    верно, но напечатанное без оговорки выглядит результатом: прогон с
    подставным ключом честно рапортует «код верный 0 (0 %)» и «разница входных
    токенов +0», то есть ровно то же, что сказал бы настоящий замер, у которого
    кадр не даёт ничего. Различить эти два прогона по числам нельзя — только по
    столбцу «Ошибок», который стоит предпоследним и читается последним.
    """
    if not (a.errors or b.errors):
        return ""
    if a.errors == a.n and b.errors == b.n:
        return (
            f"ЗАМЕРА НЕ ПРОИЗОШЛО: все вызовы ({a.n + b.n}) оборвались отказом модели. "
            f"Ни одна цифра ниже замером не является — это разметка пустого прогона. "
            f"Текст отказа виден в столбцах плеч; типичная причина — подставной или "
            f"просроченный ключ модели."
        )
    return (
        f"ЧАСТЬ ВЫЗОВОВ ОБОРВАЛАСЬ ОТКАЗОМ МОДЕЛИ: плечо A — {a.errors} из {a.n}, "
        f"плечо B — {b.errors} из {b.n}. Такая запись считается промахом по каждой "
        f"мерке и не приносит токенов, поэтому доли ниже занижены, а суммы неполны: "
        f"сравнивать плечи между собой можно, объявлять числа замером — нет."
    )


def render(records_total: int, skipped: Sequence[str], outcomes: Sequence[CaseOutcome]) -> str:
    """Отчёт замера: шапка с датой и числом записей, разбор по записям, итог по плечам.

    Число без даты и объёма корпуса через неделю нечем проверить — оба
    печатаются в шапке, а не только в имени файла `--out`.
    """
    a_totals = _leg_totals("A — как было (кадр по старому правилу)", outcomes, lambda o: o.a)
    b_totals = _leg_totals("B — как стало (только комментарий, D081)", outcomes, lambda o: o.b)
    lines = [
        f"Замер цены D081 (T202) на боевых записях — {date.today().isoformat()}",
        f"Записей в корпусе: {records_total}. Прогнано в этом замере: {len(outcomes)}.",
        "",
    ]
    # Предупреждение про отказы — ДО таблиц: читатель должен узнать, что числа
    # неполны, раньше, чем прочтёт сами числа (T224).
    if warning := _errors_warning(a_totals, b_totals):
        lines.extend((warning, ""))
    if skipped:
        lines.append(f"Пропущено без фотографий: {len(skipped)}.")
        lines.extend(f"  {line}" for line in skipped)
        lines.append("")
    lines.extend(_table(outcomes))
    lines.append("")
    lines.append(_hint_sources_line(outcomes))
    lines.append("")
    lines.extend(_totals_table(a_totals, b_totals))
    lines.append("")
    lines.append(_token_diff_line(a_totals, b_totals))
    return "\n".join(lines)


def _write_out(
    out: Path, records_total: int, skipped: Sequence[str], outcomes: Sequence[CaseOutcome]
) -> None:
    """Построчный json замера — местный файл для разбора, не публикация.

    Сам инструмент ключ модели никуда не кладёт: в его структурах данных
    `OPENAI_API_KEY` не появляется. Единственное, что приходит сюда извне, —
    текст отказа провайдера в `LegOutcome.error`, и он цитируется как есть;
    провайдер в ответе 401 повторяет ключ замаскированным. Почему текст не
    режется своим выражением — в шапке модуля.
    """
    a_totals = _leg_totals("A — как было (кадр по старому правилу)", outcomes, lambda o: o.a)
    b_totals = _leg_totals("B — как стало (только комментарий, D081)", outcomes, lambda o: o.b)
    payload = {
        "date": date.today().isoformat(),
        "records_total": records_total,
        "records_run": len(outcomes),
        "skipped": list(skipped),
        "outcomes": [asdict(o) for o in outcomes],
        "totals": {"a": a_totals.as_dict(), "b": b_totals.as_dict()},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    # default=str — единственное, что в этом обходе нужно превратить в строку,
    # это Path кадра внутри Record; ничего секретного в структуре нет.
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="корень репозитория (по умолчанию — вычисленный от файла)",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="куда записать построчный json (необязательно)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="сколько первых записей взять (для дешёвой проверки на одной)",
    )
    args = parser.parse_args(argv)

    corpus = load_corpus(args.root)
    if not corpus.records:
        print(
            "Боевых данных нет: examples/*/inspection.json не найдены или в них нет "
            "записей с фото (данные вне git, решение D002). Замерять нечего — "
            "это не поломка инструмента."
        )
        for line in corpus.skipped:
            print(line)
        return 2

    records = corpus.records if args.limit is None else corpus.records[: args.limit]
    # Методику читают обе половины ниже: `hints_bot` — зоны, `run_case` —
    # перечень пунктов. Без `STATE_DIR` `check_environment()` отказывает, и до
    # T224 этот отказ выходил наружу трассировкой на двадцать строк — из неё
    # человеку надо было ещё вычитать, что не хватает переменной окружения.
    # Отказ ловится здесь, а не вокруг одного вызова: `run_all` считает в
    # потоках и поднимает его же из `future.result()`.
    try:
        hints = hints_bot(records)
        outcomes = run_all(records, hints)
    except DomainError as failure:
        print(f"Замерять не по чему: {failure}")
        return 2

    print(render(len(corpus.records), corpus.skipped, outcomes))

    if args.out is not None:
        _write_out(args.out, len(corpus.records), corpus.skipped, outcomes)
        print(f"\nПодробности: {args.out}")

    # Прогон, где не удался ни один вызов, — не «прогон прошёл». Нулём отсюда
    # выходить нельзя: у платного замера это единственный признак, по которому
    # видно, что 34 вызова не состоялись, а отчёт заполнен нулями (T224).
    if all(outcome.a.error and outcome.b.error for outcome in outcomes):
        return 1
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
