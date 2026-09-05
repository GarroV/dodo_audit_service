#!/usr/bin/env python3
"""T166 (D077): сколько раз признак «<описание>, это <процесс>» срабатывает зря.

`process_hint` (`src/recognize/process_hint.py`) распознаёт указание аудитора
словарю: «ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА» — фраза владельца из D077.
Признак **ненадёжен по устройству**: тот же речевой акт бывает и обычным
комментарием, поэтому единственное, что о нём можно узнать, — как часто он
срабатывает на текстах, которые указанием словарю не были.

**Чего этот замер померить не может, и это главное.** Признак живёт в СЫРЫХ
СЛОВАХ аудитора. Хранить их продукт начал только что (T183), боевого корпуса
ещё нет, и до его появления доля ложных срабатываний не считается ни по чему.
Замер поэтому устроен так, чтобы начать считать её сам, как только слова
появятся: сырые слова из состояния проверок идут первым корпусом, а пока их нет
— печатается прямо, что мерить нечего, и остаются корпуса-заместители.

Заместители — не выборка того же рода, и выдавать их за неё нельзя:

* `examples/*/inspection.json` — формулировки записей и комментарии партнёру.
  Речь аудитора, но отшлифованная: в отчёт идёт не то, что он сказал на точке.
* `data/checklist.csv` — вопросы методики. Речь управляющей компании о
  нарушениях: там имена процессов встречаются часто, а указанием словарю ни
  один вопрос не является. Хороший корпус именно для ложных срабатываний.
* `data/criteria.md` — критерии классов, самый большой связный текст
  управляющей компании про нарушения, какой у нас есть.

Что печатается по каждому корпусу: сколько текстов, в скольких вообще есть имя
процесса (потолок признака, если бы связки не было), в скольких стоит связка и
в скольких сработал признак целиком. Ни одного обращения к сети.

Запуск:  python tools/process_hint_measure.py [--root PATH]
Коды возврата: 0 — норма, 2 — методики нет (мерить не по чему).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.domain import list_items  # noqa: E402
from src.domain.config import check_environment  # noqa: E402
from src.domain.errors import DomainError  # noqa: E402
from src.domain.state import read_words  # noqa: E402
from src.recognize.config import DEFAULT_LANG  # noqa: E402
from src.recognize.cues import stems  # noqa: E402
from src.recognize.process_hint import CONNECTIVE, process_hint  # noqa: E402

#: Сколько символов слов аудитора показывать в разборе срабатывания.
_SNIPPET = 70


@dataclass(frozen=True)
class Corpus:
    """Набор текстов, на котором меряется признак, и чем этот набор является."""

    title: str
    #: Чем корпус НЕ является — печатается рядом с числами, чтобы их не путали.
    caveat: str
    texts: tuple[str, ...]
    #: На каком языке корпус написан. Не украшение: и связка, и имена процессов
    #: у каждого языка свои, и померить английский корпус русскими именами
    #: значило бы получить ноль и принять его за отсутствие ложных срабатываний.
    lang: str = DEFAULT_LANG


@dataclass(frozen=True)
class Result:
    """Что вышло на одном корпусе."""

    corpus: Corpus
    #: Тексты, где вообще встречается имя процесса, — потолок признака без связки.
    named: int
    #: Тексты, где стоит связка признака.
    linked: int
    #: Тексты, на которых признак сработал целиком, и какой процесс он назвал.
    fired: tuple[tuple[str, str], ...]


def _process_stems(lang: str) -> list[frozenset[str]]:
    return [frozenset(stems(i.process(lang))) for i in list_items() if stems(i.process(lang))]


def _has_connective(text: str) -> bool:
    """Стоит ли в тексте связка признака. Выражение берётся у самого признака."""
    return CONNECTIVE.search(text) is not None


def spoken_words(state_dir: Path) -> tuple[str, ...]:
    """Сырые слова аудитора из состояния проверок (T183).

    Раскладка — одна проверка, одна папка чата (D007), файл внутри всегда
    `inspection.json`. Читается тем же `read_words`, что и у продукта: своя
    копия правил чтения разошлась бы молча, и замер начал бы мерить не то.
    """
    said: list[str] = []
    for path in sorted(state_dir.glob("*/inspection.json")) if state_dir.is_dir() else []:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        said.extend(w for w in read_words(raw, path).values() if w.strip())
    return tuple(said)


def example_texts(root: Path) -> tuple[str, ...]:
    """Формулировки записей и комментарии партнёру из боевых проверок."""
    texts: list[str] = []
    for path in sorted(root.glob("examples/*/inspection.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for finding in data.get("findings", []):
            texts.extend(t for t in (finding.get("evidence"), finding.get("comment")) if t)
    return tuple(texts)


def checklist_questions(lang: str = DEFAULT_LANG) -> tuple[str, ...]:
    """Вопросы методики: речь управляющей компании о нарушениях.

    Язык — параметр: та же методика заполнена на обоих языках, и английская
    колонка (T196) — единственный корпус того же рода, какой у английской
    связки вообще есть. Связка «is» частотнее русской «это», и без этого
    корпуса про неё нечего было бы сказать, кроме предположения.
    """
    return tuple(i.question(lang) for i in list_items() if i.question(lang))


def criteria_lines(data_dir: Path) -> tuple[str, ...]:
    """Критерии классов: самый большой связный текст УК про нарушения."""
    path = data_dir / "criteria.md"
    if not path.is_file():
        return ()
    return tuple(
        line.strip(" ●•-\t")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


def corpora(root: Path) -> Iterator[Corpus]:
    settings = check_environment()
    yield Corpus(
        "Сырые слова аудитора (T183) — ЕДИНСТВЕННЫЙ корпус того же рода",
        "то, что человек сказал на точке; ради него признак и заведён",
        spoken_words(settings.state_dir),
    )
    yield Corpus(
        "Записи боевых проверок: формулировки и комментарии партнёру",
        "речь аудитора, но отшлифованная — в отчёт идёт не сказанное на точке",
        example_texts(root),
    )
    yield Corpus(
        "Вопросы методики по-русски",
        "речь управляющей компании: указанием словарю не является ни один",
        checklist_questions("ru"),
    )
    yield Corpus(
        "Вопросы методики по-английски — ЕДИНСТВЕННЫЙ корпус английской связки",
        "та же речь управляющей компании на втором языке; боевых английских "
        "проверок не существует, и живой английской речи здесь нет",
        checklist_questions("en"),
        lang="en",
    )
    yield Corpus(
        "Критерии классов, построчно",
        "речь управляющей компании: указанием словарю не является ни одна",
        criteria_lines(settings.data_dir),
    )


def measure(corpus: Corpus) -> Result:
    names = _process_stems(corpus.lang)
    named = linked = 0
    fired: list[tuple[str, str]] = []
    for text in corpus.texts:
        words = stems(text)
        if any(name <= words for name in names):
            named += 1
        if _has_connective(text):
            linked += 1
        hint = process_hint(text, lang=corpus.lang)
        if hint is not None:
            fired.append((hint.process, hint.said[:_SNIPPET]))
    return Result(corpus=corpus, named=named, linked=linked, fired=tuple(fired))


def render(results: Sequence[Result]) -> str:
    lines = [
        f"Замер признака «<описание>, это <процесс>» — {date.today().isoformat()}",
        "Признак ненадёжен по устройству: тот же речевой акт бывает и обычным",
        "комментарием. Меряется только одно — как часто он срабатывает там, где",
        "указанием словарю не было ничего.",
        "",
        "| Корпус | Текстов | С именем процесса | Со связкой | СРАБОТАЛ |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {r.corpus.title} | {len(r.corpus.texts)} | {r.named} | {r.linked} | {len(r.fired)} |"
        for r in results
    )
    lines.append("")
    for result in results:
        lines.append(f"{result.corpus.title} — {result.corpus.caveat}.")
        if not result.corpus.texts:
            lines.append("  Пусто: мерить нечего, и это не поломка инструмента.")
        for process, said in result.fired:
            lines.append(f"  сработал на «{said}…» → процесс «{process}»")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="корень репозитория")
    args = parser.parse_args(argv)

    # Каталог состояния НЕ подставляется по умолчанию намеренно: подставленный
    # пустой каталог дал бы «ноль ложных срабатываний» вместо «сырых слов нет»,
    # а это два разных утверждения, и второе — весь смысл замера.
    try:
        if not checklist_questions():
            print("Методики нет: мерить признак не по чему — имена процессов берутся из неё.")
            return 2
        print(render([measure(c) for c in corpora(args.root)]))
    except DomainError as failure:
        print(f"Замерить не по чему: {failure}")
        return 2
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
