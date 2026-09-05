"""T192 (#157): правила разбора слов аудитора — данные, а не константы в коде.

Правило проекта записано в его же `CLAUDE.md`: «Язык — параметр, никогда не
константа». Разбор слов ему не подчинялся: заголовки разделов карты кадров
узнавались по русским названиям, стоп-слова и окончания были русскими, словарь
колонок знал заголовки «Грязь» и «Поломка» и только русские слова. Проверка,
которую аудитор ведёт по-английски, разбиралась русскими правилами — то есть не
разбиралась вовсе, и заметно это стало на демо-наборе: два раздела карты не
находились, а артикль `the` считался значимой основой и требовался от аудитора
дословно.

Здесь лежит **весь** язык разбора: `language_rules.json` рядом с этим файлом.
Третий язык добавляется записью в нём и ничем больше — кода, который знает
список языков, в продукте нет.

**Почему правила складываются, а не выбираются по параметру `lang`.** Языков в
разборе одновременно три, и они не обязаны совпадать: карту кадров пишет
управляющая компания, комментарий говорит аудитор, отчёт печатается на языке
партнёра. Причём `stems()` зовут и оттуда, где языка нет вовсе — разбор самой
карты (`cues.load_cues`) и узнавание зоны по словам (`bot.zones`). Выбор правил
по `lang` означал бы, что русская карта перестаёт совпадать, как только аудитор
попросил английский отчёт, — молча и без единого отказа. Сложение такого отказа
не имеет: чужие стоп-слова в тексте не встречаются, а окончания чужого алфавита
не подходят ни к одному слову своего.

**Чем за это платим.** Два языка одного алфавита (сербский рядом с английским)
складываются уже не бесплатно: служебное слово одного может оказаться значимым
словом другого. Пересечение стоп-слов поэтому ловится тестом
(`tests/test_recognize_language.py`), а не договорённостью. Основы при этом
считаются одинаково и у строки карты, и у комментария, поэтому чужое окончание
даёт не промах, а другую основу с обеих сторон сразу.

**Читается один раз, при импорте.** Это правила самого продукта, а не методика:
карту кадров подкладывают томом снаружи и потому перечитывают на каждом вызове,
а файл правил лежит в репозитории и меняется вместе с кодом.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import RecognizeConfigError

#: Файл правил. Лежит рядом с кодом: это часть продукта, а не методики.
RULES_FILE = Path(__file__).with_name("language_rules.json")

#: Разделы карты кадров, которые подсказками «что видно на кадре» не являются и
#: потому читаются своими функциями (`cues.class_thresholds`, `cues.column_words`).
THRESHOLDS = "thresholds"
COLUMN_WORDS = "column_words"
_SECTIONS = (THRESHOLDS, COLUMN_WORDS)

#: Поля, которые обязан объявить каждый язык. Пропущенное — это язык, который
#: разбирается наполовину: слова режутся, а колонка не выбирается никогда.
_FIELDS = ("about", "stopwords", "suffixes", "negations", "column_words", "sections")

#: Куда частица отрицания смотрит — часть правил языка, а не кода (T195).
#: По-русски «не», «без», «ни» относятся к тому, что стоит ПОСЛЕ них, а «нет» —
#: к обеим сторонам сразу: говорят и «нет нагара», и «нагара нет». По-английски
#: вперёд смотрят все частицы. Направление здесь не украшение: правило, снимающее
#: слово с обеих сторон от любой частицы, отбрасывает саму «печь» во фразе «печь
#: не сломана» — строка карты перестаёт находиться, и аудитор получает не ту
#: причину отказа, которая на самом деле сработала.
FORWARD = "forward"
BACKWARD = "backward"
BOTH = "both"
_DIRECTIONS = (FORWARD, BACKWARD, BOTH)


@dataclass(frozen=True)
class LanguageRules:
    """Правила разбора слов одного языка."""

    #: Слова, которые есть в половине подсказок и потому ничего не различают.
    stopwords: frozenset[str]
    #: Окончания, которые отсекаются при сведении слова к основе.
    suffixes: tuple[str, ...]
    #: Частица, переворачивающая смысл («без нагара»), → куда она смотрит.
    negations: Mapping[str, str]
    #: Заголовок колонки карты → слова, которыми аудитор эту колонку называет.
    column_words: Mapping[str, tuple[str, ...]]
    #: Разделы карты кадров: `THRESHOLDS` и `COLUMN_WORDS` → начало заголовка.
    sections: Mapping[str, str]


def _fail(what: str) -> RecognizeConfigError:
    return RecognizeConfigError(
        f"{RULES_FILE.name}: {what}. Правила разбора слов читаются при старте, и "
        f"молчаливый откат на встроенные значения означал бы язык, который "
        f"разбирается наполовину"
    )


def _words(raw: object, where: str) -> tuple[str, ...]:
    """Список слов в нижнем регистре: сравнение идёт с уже понижённым текстом."""
    if not isinstance(raw, list) or not raw or not all(isinstance(w, str) and w for w in raw):
        raise _fail(f"{where} — не непустой список строк")
    words: list[str] = [str(w) for w in raw]
    upper = [w for w in words if w != w.lower()]
    if upper:
        raise _fail(f"{where} содержит слова не в нижнем регистре: {upper}")
    return tuple(dict.fromkeys(words))


def _negations(raw: object, code: str) -> Mapping[str, str]:
    """Частицы отрицания языка: частица → направление.

    Направление объявляется у каждой частицы поимённо и проверяется по списку:
    опечатка в нём («forwrd») означала бы частицу, которая не действует никуда,
    и молчаливую дыру ровно там, где стоит защита от записи отрицания.
    """
    if not isinstance(raw, dict) or not raw:
        raise _fail(f"у языка «{code}» пустой список частиц отрицания")
    out: dict[str, str] = {}
    for word, direction in raw.items():
        (particle,) = _words([word], f"{code}/negations")
        if direction not in _DIRECTIONS:
            raise _fail(
                f"у языка «{code}» частица «{particle}» смотрит в «{direction}»: "
                f"ожидалось одно из {list(_DIRECTIONS)}"
            )
        out[particle] = str(direction)
    return out


def _one(raw: Mapping[str, object], code: str) -> LanguageRules:
    for field in _FIELDS:
        if field not in raw:
            raise _fail(f"у языка «{code}» не объявлено поле «{field}»")
    if not isinstance(raw["about"], str) or not raw["about"].strip():
        raise _fail(f"у языка «{code}» пустое поле «about»: откуда взяты слова — часть правил")

    columns = raw["column_words"]
    if not isinstance(columns, dict) or not columns:
        raise _fail(f"у языка «{code}» пустой словарь колонок")
    sections = raw["sections"]
    if not isinstance(sections, dict) or set(sections) != set(_SECTIONS):
        raise _fail(f"у языка «{code}» названы не все разделы карты: ожидались {list(_SECTIONS)}")
    for kind, heading in sections.items():
        if not isinstance(heading, str) or not heading.startswith("## "):
            raise _fail(f"у языка «{code}» заголовок раздела «{kind}» не начинается с «## »")

    return LanguageRules(
        stopwords=frozenset(_words(raw["stopwords"], f"{code}/stopwords")),
        suffixes=_words(raw["suffixes"], f"{code}/suffixes"),
        negations=_negations(raw["negations"], code),
        column_words={
            _words([header], f"{code}/column_words")[0]: _words(words, f"{code}/{header}")
            for header, words in columns.items()
        },
        sections={str(kind): str(heading) for kind, heading in sections.items()},
    )


def load_rules(path: Path = RULES_FILE) -> Mapping[str, LanguageRules]:
    """Прочитать правила всех языков. Сломанный файл — отказ, а не половина правил."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _fail(f"файл правил не прочитан ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise _fail(f"файл правил не разобран как JSON ({exc})") from exc
    if not isinstance(raw, dict) or not raw:
        raise _fail("в файле правил не объявлено ни одного языка")
    return {str(code): _one(body, str(code)) for code, body in raw.items()}


#: Правила всех языков продукта. Читаются один раз: файл лежит в репозитории.
RULES: Mapping[str, LanguageRules] = load_rules()


def stopwords(rules: Mapping[str, LanguageRules] = RULES) -> frozenset[str]:
    """Служебные слова всех языков разом."""
    return frozenset(word for r in rules.values() for word in r.stopwords)


def suffixes(rules: Mapping[str, LanguageRules] = RULES) -> tuple[str, ...]:
    """Окончания всех языков, от длинных к коротким.

    Порядок — часть правила, а не оформление: основа отсекается по ПЕРВОМУ
    совпавшему окончанию, поэтому «-ами» обязано проверяться раньше «-и».
    Сортировка устойчива, поэтому внутри одной длины сохраняется порядок файла.
    """
    merged = dict.fromkeys(suffix for r in rules.values() for suffix in r.suffixes)
    return tuple(sorted(merged, key=lambda s: -len(s)))


def negations(rules: Mapping[str, LanguageRules] = RULES) -> Mapping[str, str]:
    """Частицы отрицания всех языков разом: частица → направление.

    Одна и та же частица в двух языках обязана смотреть в одну сторону, иначе
    складывать правила нельзя: разбор перестал бы зависеть только от текста и
    начал зависеть от порядка языков в файле. Расхождение — отказ, а не
    молчаливый выбор последнего объявления.
    """
    merged: dict[str, str] = {}
    for language in rules.values():
        for word, direction in language.negations.items():
            if merged.setdefault(word, direction) != direction:
                raise _fail(
                    f"частица «{word}» объявлена в разных языках с разным направлением "
                    f"(«{merged[word]}» и «{direction}»)"
                )
    return merged


def column_words(rules: Mapping[str, LanguageRules] = RULES) -> dict[str, tuple[str, ...]]:
    """Встроенный минимум словаря колонок: заголовок колонки → слова аудитора.

    Ключ — заголовок колонки самой карты в нижнем регистре, поэтому языки здесь
    складываются без риска: «грязь» и «dirt» — разные ключи. Карта этот словарь
    ДОПОЛНЯЕТ своим разделом (D077), а не заменяет.
    """
    merged: dict[str, tuple[str, ...]] = {}
    for language in rules.values():
        for header, words in language.column_words.items():
            merged[header] = tuple(dict.fromkeys(merged.get(header, ()) + tuple(words)))
    return merged


def section_headings(kind: str, rules: Mapping[str, LanguageRules] = RULES) -> tuple[str, ...]:
    """Начала заголовков раздела карты на всех языках.

    Кортеж, потому что его отдают в `str.startswith`: карта монолингвальна, но
    какая она — код заранее не знает и знать не должен.
    """
    if kind not in _SECTIONS:
        raise _fail(f"раздела «{kind}» в правилах нет: объявлены {list(_SECTIONS)}")
    return tuple(dict.fromkeys(r.sections[kind] for r in rules.values()))
