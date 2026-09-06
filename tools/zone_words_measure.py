#!/usr/bin/env python3
"""T242 (#198): узнаётся ли зона, названная НЕ в именительном падеже.

Зону записи определяет разбор слов аудитора (`src/bot/zones.py`, D047): имена
зон берутся из методики, слова — из комментария, совпадение идёт по основам
(`recognize.cues.stems`). В методике имя зоны записано в именительном падеже, а
на точке его так не произносят: говорят «на тепловом участке», «в раздевалке»
(имена синтетической методики). Разбор при этом обязан узнать зону — иначе она
молча подставится памятью о ПРОШЛОЙ записи (D048), и вычет уедет в чужую зону
отчёта партнёру.

**Что здесь меряется.** Имя зоны из действующего издания методики ставится в
предложный падеж — падеж места, тот самый, которым место и называют, — и
разбору отдаётся то, что получилось. Считаются три вещи:

* **зона узнана** — `zone_from_words` вернул её код. Это продуктовое число;
* **зона узнана НЕВЕРНО** — вернулся код другой зоны. Это опаснее всего:
  неузнанная зона видна аудитору кнопкой, а чужая уводит запись молча. То же
  правило, что и в `tools/fastpath_measure.py`;
* **основа слова уехала** — `stems(слово в падеже) != stems(слова из методики)`.
  Это причина, а не следствие: имя может остаться узнаваемым вторым своим
  словом и при уехавшей основе первого, и тогда продуктовое число промах
  прячет. Пример дефекта, ради которого замер заведён: стеммер сводит
  «участке» к «участк», а «участок» оставляет как есть — беглая гласная.

**Формы порождаются правилами, и правила эти — данные языка, а не константы.**
`CASES` объявляет их на каждый язык продукта поимённо; язык, для которого
правил нет, называется вслух отдельной строкой, а не пропускается молча.
У английского падежей нет вовсе, и его плечо здесь — контрольное: оно
показывает, что замер не русский по устройству, и стережёт узнавание имени,
записанного как есть.

**Порождённые формы печатаются целиком, и это часть замера.** Правила ниже —
не морфологический разбор: слово, у которого гласная перед последней согласной
не беглая («моноблок»), они испортят («монобл*ке*» вместо «моноблоке»). Пока
такое слово в именах зон не встречается, но встретится после переименования, и
единственная защита от того, чтобы замер начал мерить свою же ошибку, — глаза
человека на таблице форм. Строки, к которым правило беглой гласной
применилось, отчёт называет отдельно.

**Преобразование пословное, и служебные слова оно не трогает.** Слово, от
которого разбор не оставляет основы вовсе (стоп-слово «для», однобуквенное
«и»), остаётся как есть — «незначимо» решает сам продукт, а не список рядом.
Слово-определение в родительном падеже («Стеллаж хранения») при этом склоняется
вместе с главным, и получается не фраза, а построчный падеж каждого слова:
основы это не меняет (её и меряем), а на грамотность порождённая строка не
претендует.

**Издание — `NO_CHAT`, и это ответ, а не пропуск.** Замер идёт по действующей
методике, живой проверки за ним нет (T225, T226).

Ни одного обращения к сети и ни одной копейки: разбор детерминированный.

**Вывод в репозиторий не кладут.** Таблица печатает боевые названия зон, а
репозиторий публичный (`tests/test_methodology_leak.py`).

Запуск:  python tools/zone_words_measure.py

Окружение: `AUDIT_DATA_DIR` инструмент подставляет себе сам (методика
репозитория), `STATE_DIR` обязан задать запускающий — его требует любое
обращение к методике (`src/domain/config.py`), и `make zonewords` его
подставляет. Не задан — внятный отказ и код 2, а не трассировка (T239).

Коды возврата: 0 — норма, 1 — есть НЕВЕРНО узнанная зона, 2 — замерять не по
чему: зон нет или окружение не настроено.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Разбор зоны по словам живёт в блоке `bot` и правится там же, стеммер — в
# `recognize`. Замер их ЗОВЁТ, а не повторяет: своя копия правил разошлась бы
# с продуктом молча. Контракт слоёв (import-linter) этим не задет — `tools/`
# стоит НАД продуктом.
from src.bot.texts import UI_LANGS  # noqa: E402
from src.bot.zones import SPOKEN, zone_from_words  # noqa: E402
from src.domain import list_zones  # noqa: E402
from src.domain.errors import DomainError  # noqa: E402
from src.recognize.config import NO_CHAT  # noqa: E402
from src.recognize.cues import stems  # noqa: E402

#: Падеж, в котором называют место. Имя одно на замер: замер не про полную
#: парадигму, а про то, что аудитор произносит, стоя в этом месте.
PREPOSITIONAL = "предложный"

#: Плечо языка, у которого падежей нет вовсе. Не пропуск, а объявление.
NO_CASES = "падежей нет"

#: Откуда взялось имя зоны: из методики (переводится и переименовывается
#: управляющей компанией) или из списка обиходных названий продукта.
FROM_METHODOLOGY = "методика"
FROM_SPOKEN = "обиход"

#: Слово короче этого правило беглой гласной не трогает. Беглую гласную от
#: обычной длиной не отличить («участок» против «моноблока»), и на коротком
#: слове («ток», «срок», «блок») ошибка правила гарантирована, а выигрыша нет:
#: в них гласная не беглая. Шесть — по именам зон обеих методик: «участок» (7)
#: под правило попадает, «блок» (4) нет, и обе формы верны.
FLEETING_MIN_LEN = 6

#: Беглая гласная: окончание именительного падежа → чем оно становится перед
#: падежным окончанием. «участок» → «участке», «барашек» → «барашке»,
#: «козырёк» → «козырьке», «образец» → «образце».
_RU_FLEETING = (("ок", "к"), ("ек", "к"), ("ёк", "ьк"), ("ец", "ц"))

#: Шипящие и «ц»: после них безударное «о» окончания пишется как «е»
#: («прилегающая» → «прилегающей», но «морозильная» → «морозильной»).
_RU_HUSHING = "жчшщц"

#: Заднеязычные: после них прилагательное на «-ий» берёт «-ом», а не «-ем»
#: («русский» → «русском», но «горячий» → «горячем»).
_RU_BACK = "кгх"

#: Гласные: слово на согласную склоняется приписыванием окончания, слово на
#: гласную — заменой. Прочая гласная на конце значит, что слово в именительном
#: падеже не стоит вовсе, и правило его не трогает.
_RU_VOWELS = "аеиоуыэюя"


def _ru_prepositional(word: str) -> str:
    """Слово в предложном падеже по правилам русского языка.

    Окончания проверяются сверху вниз, побеждает первое подошедшее — длинные
    стоят раньше коротких. Слово на согласную идёт последней веткой, и именно
    в ней сидит беглая гласная — предмет замера.

    Однозначным разбором это не является и не притворяется. «-ие» у
    существительного даёт «-ии» («оборудование» → «оборудовании»), у
    прилагательного во множественном — «-их»: выбрана первая ветка, потому что
    имена зон — это существительное с определением, а не перечень. «-ь» у
    женского рода даёт «-и» («плесень» → «плесени»), у мужского «-е»: выбран
    женский по той же причине. Пока имена зон обеих методик такими словами не
    кончаются; когда закончатся, спорную форму покажет таблица.
    """
    if word.endswith(("ия", "ие")):
        return word[:-2] + "ии"
    if word.endswith("ий"):
        return word[:-2] + ("ом" if word[-3:-2] in _RU_BACK else "ем")
    if word.endswith(("ый", "ой")):
        return word[:-2] + "ом"
    if word.endswith("ая"):
        return word[:-2] + ("ей" if word[-3:-2] in _RU_HUSHING else "ой")
    if word.endswith("яя"):
        return word[:-2] + "ей"
    if word.endswith("ое"):
        return word[:-2] + "ом"
    if word.endswith("ее"):
        return word[:-2] + "ем"
    if word.endswith("ые"):
        return word[:-2] + "ых"
    if word.endswith(("а", "я", "о", "е", "ы", "й")):
        return word[:-1] + "е"
    if word.endswith("ь"):
        return word[:-1] + "и"
    if word[-1:] in _RU_VOWELS:
        # Слово на прочую гласную («и», «у», «ю») в именительном падеже не
        # стоит вовсе, и приписывать ему падежное окончание значило бы
        # породить несуществующее слово, а потом мерить на нём разбор.
        # Правило беглой гласной ниже — только для слова на согласную.
        return word
    if len(word) >= FLEETING_MIN_LEN:
        for ending, replacement in _RU_FLEETING:
            if word.endswith(ending):
                return word[: -len(ending)] + replacement + "е"
    return word + "е"


#: Правила падежа на каждый язык продукта. Языка без объявления здесь быть не
#: должно: молчаливый пропуск означал бы «этот язык не меряется», и узнать это
#: было бы неоткуда. Пустой кортеж — законное объявление «падежей нет».
CASES: dict[str, tuple[tuple[str, Callable[[str], str]], ...]] = {
    "ru": ((PREPOSITIONAL, _ru_prepositional),),
    # Английское существительное по падежам не изменяется: место называют тем
    # же словом («in the hot kitchen»). Плечо остаётся контрольным — имя,
    # записанное как есть, обязано узнаваться.
    "en": (),
}


@dataclass(frozen=True)
class Form:
    """Имя зоны и то, во что его поставил падеж."""

    zone: str
    lang: str
    origin: str
    #: Как имя записано в методике (или в списке обиходных названий).
    nominative: str
    #: То же имя пословно в падеже. Совпадает с `nominative`, если падежей нет.
    spoken: str
    case: str

    @property
    def drifted(self) -> tuple[str, ...]:
        """Слова имени, у которых падеж увёл основу. Пусто — основы совпали."""
        before = _words(self.nominative)
        after = _words(self.spoken)
        return tuple(
            f"{was} → {now}"
            for was, now in zip(before, after, strict=True)
            if stems(was) != stems(now)
        )


@dataclass(frozen=True)
class Outcome:
    """Что разбор ответил на одно имя в падеже."""

    form: Form
    recognized: str | None

    @property
    def verdict(self) -> str:
        if self.recognized is None:
            return "не узнана"
        return "узнана" if self.recognized == self.form.zone else "НЕВЕРНО"

    @property
    def wrong(self) -> bool:
        return self.recognized is not None and self.recognized != self.form.zone


def _words(text: str) -> tuple[str, ...]:
    return tuple(text.lower().split())


def inflect(name: str, rule: Callable[[str], str]) -> str:
    """Имя пословно в падеже. Слово без основы (служебное) не трогается вовсе."""
    return " ".join(rule(word) if stems(word) else word for word in _words(name))


def forms(*, chat_id: int | None) -> tuple[Form, ...]:
    """Все имена всех зон издания, в именительном падеже и в падеже места.

    Имена берутся у методики (`list_zones`) на каждом языке интерфейса и у
    списка обиходных названий продукта (`bot.zones.SPOKEN`) — того самого,
    которым зона узнаётся, когда её называют не так, как записано в методике.
    Обиходные названия языком не подписаны и разбираются правилами того языка,
    на котором написаны, — русского: они и заведены как русская речь на точке.
    """
    out: list[Form] = []
    for zone in list_zones(chat_id=chat_id):
        named = [(lang, FROM_METHODOLOGY, zone.title(lang)) for lang in UI_LANGS]
        named += [("ru", FROM_SPOKEN, spoken) for spoken in SPOKEN.get(zone.code, ())]
        for lang, origin, name in named:
            cases = CASES.get(lang)
            if not cases:
                out.append(Form(zone.code, lang, origin, name, name.lower(), NO_CASES))
                continue
            for case, rule in cases:
                out.append(Form(zone.code, lang, origin, name, inflect(name, rule), case))
    return tuple(out)


def measure(built: Sequence[Form], *, chat_id: int | None) -> tuple[Outcome, ...]:
    """Отдать разбору каждую форму и записать, что он ответил."""
    return tuple(
        Outcome(form=f, recognized=zone_from_words(f.spoken, chat_id=chat_id)) for f in built
    )


def _table(outcomes: Sequence[Outcome]) -> list[str]:
    lines = [
        "| Зона | Язык | Имя откуда | Как в методике | Как произнесено | Падеж | Вердикт "
        "| Основы уехали |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {o.form.zone} | {o.form.lang} | {o.form.origin} | {o.form.nominative} | "
        f"{o.form.spoken} | {o.form.case} | {o.verdict} | "
        f"{', '.join(o.form.drifted) or '—'} |"
        for o in outcomes
    )
    return lines


def _totals(outcomes: Sequence[Outcome]) -> list[str]:
    lines = [
        "| Плечо | Имён | Узнано | Не узнано | НЕВЕРНО | Имён с уехавшей основой |",
        "|---|---|---|---|---|---|",
    ]
    arms = sorted({(o.form.lang, o.form.case) for o in outcomes})
    for lang, case in arms:
        part = [o for o in outcomes if o.form.lang == lang and o.form.case == case]
        ok = [o for o in part if o.verdict == "узнана"]
        wrong = [o for o in part if o.wrong]
        drifted = [o for o in part if o.form.drifted]
        lines.append(
            f"| {lang}, {case} | {len(part)} | {len(ok)} | "
            f"{len(part) - len(ok) - len(wrong)} | {len(wrong)} | {len(drifted)} |"
        )
    return lines


def _zone_totals(outcomes: Sequence[Outcome], *, lang: str, case: str) -> str:
    """Зоны, узнанные хотя бы одним своим именем, — продуктовое число замера."""
    part = [o for o in outcomes if o.form.lang == lang and o.form.case == case]
    zones = sorted({o.form.zone for o in part})
    recognized = {o.form.zone for o in part if o.verdict == "узнана"}
    clean = {z for z in zones if not any(o.form.drifted for o in part if o.form.zone == z)}
    return (
        f"Зон узнано хотя бы одним именем ({lang}, {case}): {len(recognized)} из {len(zones)}. "
        f"Зон, у которых НИ ОДНО имя не потеряло основу: {len(clean)} из {len(zones)}."
    )


def _fleeting_touched(outcomes: Sequence[Outcome]) -> list[str]:
    """Слова, к которым правило беглой гласной применилось. Проверять глазами."""
    touched = sorted(
        {
            f"{was} → {now}"
            for o in outcomes
            for was, now in zip(_words(o.form.nominative), _words(o.form.spoken), strict=True)
            if len(was) >= FLEETING_MIN_LEN
            and any(was.endswith(end) for end, _ in _RU_FLEETING)
            and was != now
        }
    )
    if not touched:
        return ["Правило беглой гласной не применилось ни к одному слову имён зон."]
    return [
        "Правило беглой гласной применилось к словам (проверить, что формы верны):",
        *(f"  {pair}" for pair in touched),
    ]


def render(outcomes: Sequence[Outcome]) -> str:
    """Отчёт замера. Печатает боевые названия зон — в репозиторий не класть."""
    declared = ", ".join(
        f"{lang}: {', '.join(case for case, _ in cases) if cases else NO_CASES}"
        for lang, cases in CASES.items()
    )
    return "\n".join(
        [
            f"Замер узнавания зоны по падежной форме имени — {date.today().isoformat()}",
            "Имена берутся из ДЕЙСТВУЮЩЕЙ методики: после её правки замер снимается заново.",
            f"Правила падежа объявлены так — {declared}.",
            "",
            *_table(outcomes),
            "",
            *_totals(outcomes),
            "",
            _zone_totals(outcomes, lang="ru", case=PREPOSITIONAL),
            "",
            *_fleeting_touched(outcomes),
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__).parse_args(argv)

    # Методику читают обе половины ниже — и построение имён, и сам разбор.
    # Без `STATE_DIR` `check_environment()` отказывает, и трассировка вместо
    # отчёта заставила бы вычитывать из стека, что не задана переменная
    # окружения (T239, T224).
    try:
        built = forms(chat_id=NO_CHAT)
        outcomes = measure(built, chat_id=NO_CHAT)
    except DomainError as failure:
        print(f"Замерять не по чему: {failure}")
        return 2

    if not outcomes:
        print(
            "Зон в методике нет: замерять нечего. Методика лежит вне git "
            "(решение D002) — на свежей копии это не поломка инструмента."
        )
        return 2

    print(render(outcomes))

    # Ненулевой код — на чужую зону. Неузнанная зона видна аудитору кнопкой и
    # подставляется памятью, чужая уводит запись молча: то же правило, что в
    # `tools/fastpath_measure.py`.
    return 1 if any(o.wrong for o in outcomes) else 0


if __name__ == "__main__":
    os.environ.setdefault("AUDIT_DATA_DIR", str(ROOT / "data"))
    raise SystemExit(main())
