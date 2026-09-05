"""Карта кадров: что видно на фотографии → какие пункты стоит показать.

Источник — `data/photo-cues.md`, документ управляющей компании. Он лежит рядом с
методикой и читается при каждом обращении: методику подкладывают томом снаружи,
и кеш означал бы работу по старой карте до перезапуска.

Карта здесь только **добавляет** кандидатов и переставляет их вперёд. Резать ей
перечень запрещено: разведка на боевом кадре показала, что при отсутствии
правильного кода среди кандидатов модель не отказывается, а уверенно предлагает
похожий пункт (`docs/forge/research/recognize-probe.md`).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from src.domain import check_environment

from . import language

#: Файл карты кадров внутри каталога методики (`AUDIT_DATA_DIR`).
CUES_FILE = "photo-cues.md"

#: Начала заголовков раздела с порогами классов — на всех языках правил (T192).
#: Это не подсказки «что видно», а справка «когда D1, а когда D2»: она уходит в
#: промпт отдельным куском. Кортеж, потому что его отдают в `str.startswith`:
#: карту пишет управляющая компания, и на каком языке — код знать не может.
THRESHOLDS_HEADINGS = language.section_headings(language.THRESHOLDS)

#: Начала заголовков раздела со словами колонок (T143). Тоже не подсказки «что
#: видно»: его строки говорят, какими словами аудитор называет колонку («Грязь»,
#: «Поломка»), а не какой пункт стоит за объектом на кадре.
COLUMN_WORDS_HEADINGS = language.section_headings(language.COLUMN_WORDS)

#: Разделы карты, которые подсказками не являются и в перечень строк не идут.
_NOT_CUES = THRESHOLDS_HEADINGS + COLUMN_WORDS_HEADINGS

_CODE = re.compile(r"\b[A-Z]{3}\d{2}\b")
_WORD = re.compile(r"[а-яёa-z0-9]+")

#: Окончания отсекаются по длине — от длинных к коротким. Полноценный
#: морфологический разбор здесь не нужен и стоил бы отдельной зависимости:
#: задача — свести «печь» и «печи», «crumbs» и «crumb» к одному ключу.
#:
#: Сами окончания и стоп-слова живут в `language_rules.json` рядом с кодом:
#: язык — параметр, а не константа (T192), и третий язык добавляется словарём,
#: а не правкой этого файла. Правила всех языков складываются — почему именно
#: так, написано в `src/recognize/language.py`.
_SUFFIXES = language.suffixes()

#: Слова, которые есть в половине подсказок и потому ничего не различают.
_STOPWORDS = language.stopwords()

#: Стем считается различающим, если ведёт не более чем к стольким **различным
#: кодам** — сколько бы строк карты его ни содержало (T142, задача #113).
#:
#: Раньше здесь стояло число СТРОК (три), и от роста карты обычные слова молча
#: теряли силу: строка про печь, расписанная отдельными строками на её узлы,
#: делала слово «печь» неразличающим, хотя все эти строки ведут в одну и ту же
#: пару пунктов. Задача просила считать порог долей
#: карты; замер показал, что доля лечит симптом и ломает главное. На
#: карте-черновике T118 (275 строк) доля 4 % — это порог в 11 строк, и при нём
#: различающими становятся ВСЕ 417 основ карты, включая «продукт»: он один
#: выносит в голову перечня десять пунктов, а «открытая упаковка» поднимает 12
#: кодов вместо пяти. То есть отсечка перестаёт работать ровно тогда, когда
#: карта дорастает до размера, ради которого её и меняли.
#:
#: Считать надо не строки и не их долю, а многозначность самого слова: цена
#: срабатывания — коды, вынесенные в голову перечня для модели. У этой величины
#: размер карты в знаменателе не стоит вовсе, поэтому требование задачи («слово,
#: различающее при 75 строках, обязано остаться различающим при 500») выполнено
#: не подбором коэффициента, а тем, что размер в правило не входит.
#:
#: Шесть — по замеру на обеих версиях карты. Строка карты обычно несёт два кода
#: («Грязь | Поломка»), то есть шесть — это примерно три объекта: дальше слово
#: называет уже не объект, а категорию. На черновике T118 при шести ни одна
#: основа не теряет силу против сегодняшнего порога, а «заказ» (4 строки, 3
#: кода) её возвращает; «продукт» (10 кодов) и «щуп» (9) остаются отсечёнными.
#: Голова перечня на 17 боевых записях растёт с 7,5 кода до 7,8 в среднем.
DISTINCTIVE_CODES_AT_MOST = 6


@dataclass(frozen=True)
class Cue:
    """Одна строка карты: что видно на кадре и какие пункты за этим стоят.

    `codes` — все коды строки подряд, в порядке карты: это то, чем сужается
    перечень для модели, и там колонки не важны.

    `by_column` помнит, из какой колонки таблицы взят каждый код. Разница
    существенна для быстрого пути (T113): «Печь | CLN05 | TEH05» — это не выбор
    из двух кандидатов, а два разных вопроса про один объект, и карта сама это
    объясняет («грязь — это `CLN*`, поломка — `TEH*`»). Пустые колонки
    (прочерк) сюда не попадают.
    """

    phrase: str
    codes: tuple[str, ...]
    by_column: tuple[tuple[str, tuple[str, ...]], ...] = ()


def _stem(word: str) -> str:
    w = word.replace("ё", "е")
    for suffix in _SUFFIXES:
        if len(w) - len(suffix) >= 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def stems(text: str) -> set[str]:
    """Значимые основы слов текста. Публичная: тем же ключом ищет быстрый путь."""
    return {
        stem
        for word in _WORD.findall(text.lower())
        if len(word) >= 3 and word not in _STOPWORDS
        for stem in (_stem(word),)
        if len(stem) >= 3
    }


def words_and_gaps(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Слова текста подряд и то, чем разделены соседние слова.

    Промежутков ровно на один меньше, чем слов: `gaps[i]` — это то, что стоит
    между `words[i]` и `words[i + 1]`. Нужны они там, где важно не только
    соседство, но и чем соседи разделены: отрицание за знаком препинания
    относится к другой части фразы, а не к следующему слову («жалоб нет, в зале
    урна переполнена»). Одного порядка слов для этого мало — знаки препинания
    в него не попадают вовсе.
    """
    prepared = text.lower().replace("ё", "е")
    spans = [match.span() for match in _WORD.finditer(prepared)]
    words = tuple(prepared[start:end] for start, end in spans)
    gaps = tuple(prepared[spans[i][1] : spans[i + 1][0]] for i in range(len(spans) - 1))
    return words, gaps


logger = logging.getLogger(__name__)


def cues_path() -> Path:
    """Где лежит карта кадров. Каталог методики задаётся `AUDIT_DATA_DIR`."""
    return check_environment().data_dir / CUES_FILE


def _read(path: Path | None) -> str:
    """Текст карты кадров. Карты нет — пустой текст, а не отказ (T157, D068).

    Карта числится необязательным файлом методики (`OPTIONAL_DATA_FILES`), и
    отказ здесь делал её обязательной в работе, оставляя необязательной на
    старте. Всплывало это не при подъёме стенда, где чинит человек с доступом к
    машине, а в чате у аудитора на первом же комментарии — так умирало демо.

    Пустая карта означает «ни одна строка не произнесена»: быстрый путь молчит,
    разбор идёт моделью, как при любом другом несовпадении условий (D063).
    Перечень пунктов при этом не режется — карта его только дополняет и
    переставляет, поэтому опасение про усечённый перечень сюда не относится.
    """
    target = path if path is not None else cues_path()
    if not target.is_file():
        logger.warning(
            "карта кадров %s не найдена (%s): быстрый путь не сработает, разбор идёт моделью",
            CUES_FILE,
            target,
        )
        return ""
    return target.read_text(encoding="utf-8")


def load_cues(path: Path | None = None) -> tuple[Cue, ...]:
    """Разобрать карту кадров в строки «фраза → коды».

    Берутся только строки таблиц: первая ячейка — что видно, коды — из
    остальных. Разделы, подсказками не являющиеся, пропускаются целиком
    (`_NOT_CUES`): в порогах классов коды стоят в первой ячейке, а в словах
    колонок кодов нет вовсе, и обе таблицы читаются своими функциями.

    Заголовок таблицы запоминается: по нему строка узнаёт, какая колонка чем
    была («Грязь», «Поломка», «Кандидаты»). Заголовком считается первая строка
    таблицы без кодов — тот же признак, по которому она сегодня пропускается.
    """
    cues: list[Cue] = []
    skipping = False
    headers: tuple[str, ...] = ()
    for line in _read(path).splitlines():
        if line.startswith("## "):
            skipping = line.strip().startswith(_NOT_CUES)
            headers = ()
            continue
        if skipping or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        codes = tuple(dict.fromkeys(_CODE.findall(" ".join(cells[1:]))))
        phrase = cells[0]
        if not phrase or set(phrase) <= set("- :"):
            continue
        if not codes:
            headers = tuple(cells)
            continue
        cues.append(Cue(phrase=phrase, codes=codes, by_column=_columns(cells, headers)))
    return tuple(cues)


def _columns(cells: list[str], headers: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Коды строки по колонкам. Пустые колонки (прочерк) отбрасываются."""
    columns: list[tuple[str, tuple[str, ...]]] = []
    for index, cell in enumerate(cells[1:], start=1):
        codes = tuple(dict.fromkeys(_CODE.findall(cell)))
        if not codes:
            continue
        header = headers[index] if index < len(headers) else ""
        columns.append((header, codes))
    return tuple(columns)


def match_cues(note: str, cues: tuple[Cue, ...]) -> tuple[str, ...]:
    """Коды, которые карта поднимает по словам комментария.

    Подсказка срабатывает, когда совпали два слова или одно различающее.
    Различающее — то, что ведёт не более чем к `DISTINCTIVE_CODES_AT_MOST`
    различным кодам; число строк, в которых слово встречается, значения не
    имеет (T142). Порядок — сначала по числу совпавших слов, потом по доле
    совпавшего в самой подсказке: строка из одного слова, совпавшая целиком,
    стоит выше длинной строки того же объекта, где из шести слов совпало одно.
    """
    words = stems(note)
    if not words:
        return ()
    phrase_stems = [stems(c.phrase) for c in cues]
    leads_to: dict[str, set[str]] = {}
    for cue, phrase in zip(cues, phrase_stems, strict=True):
        for stem in phrase:
            leads_to.setdefault(stem, set()).update(cue.codes)

    scored: list[tuple[int, float, int, tuple[str, ...]]] = []
    for order, (cue, phrase) in enumerate(zip(cues, phrase_stems, strict=True)):
        hit = phrase & words
        if not hit:
            continue
        distinctive = any(len(leads_to[stem]) <= DISTINCTIVE_CODES_AT_MOST for stem in hit)
        if len(hit) < 2 and not distinctive:
            continue
        scored.append((-len(hit), -len(hit) / len(phrase), order, cue.codes))

    codes: list[str] = []
    for _, _, _, cue_codes in sorted(scored):
        for code in cue_codes:
            if code not in codes:
                codes.append(code)
    return tuple(codes)


def class_thresholds(path: Path | None = None) -> str:
    """Раздел карты с порогами классов — тот кусок, что уходит в промпт.

    Целиком карта в промпт не идёт: она вдвое длиннее самого перечня пунктов,
    а модели нужна не она, а границы D1/D2/D3 по тем пунктам, где выбор есть.
    """
    lines: list[str] = []
    collecting = False
    for line in _read(path).splitlines():
        if line.startswith("## "):
            if collecting:
                break
            collecting = line.strip().startswith(THRESHOLDS_HEADINGS)
            continue
        if collecting:
            lines.append(line)
    return "\n".join(lines).strip()


def column_words(path: Path | None = None) -> dict[str, frozenset[str]]:
    """Слова, которыми аудитор называет колонку строки карты (T143).

    Карта различает грязь и поломку одного объекта колонками («Печь | CLN05 |
    TEH05»), но какими словами аудитор скажет, о чём речь, знает управляющая
    компания, а не код: на её же формулировках («затирки», «потёртости»,
    «затёртости», «налипшее») встроенный словарь молчит. Этот раздел — её
    место дописать слово без выпуска кода.

    Возвращается заголовок колонки в нижнем регистре → основы слов. Карта
    словарь **дополняет**, а не заменяет (D077, дословно владельца: «дополняем
    наш список терминов»); раздела нет — пустой ответ, и это законное
    состояние (D068): различение колонок остаётся на встроенном минимуме.

    Читаются строки таблицы после разделителя — как в любой другой таблице
    этого документа, поэтому шапка в словарь не попадает.
    """
    picked: dict[str, set[str]] = {}
    collecting = False
    after_separator = False
    for line in _read(path).splitlines():
        if line.startswith("## "):
            if collecting:
                break
            collecting = line.strip().startswith(COLUMN_WORDS_HEADINGS)
            after_separator = False
            continue
        if not collecting or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if all(cell and set(cell) <= set("- :") for cell in cells):
            after_separator = True
            continue
        if not after_separator:
            continue
        header = cells[0].lower()
        words = stems(" ".join(cells[1:]))
        if header and words:
            picked.setdefault(header, set()).update(words)
    return {header: frozenset(words) for header, words in picked.items()}
