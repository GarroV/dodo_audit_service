"""Из чего складываются строки, которые аудитор читает в чате.

Вынесено из роутеров, потому что строки и хендлеры живут по разным законам:
хендлер отвечает за порядок диалога, а здесь — за то, что именно человек видит.
Смешанные вместе, они превращаются в роутер на четыреста строк, где формат
подтверждения приходится искать среди `await`.

Ни одной цифры оценки тут не считается: процент и буква приходят из
`domain.score()`, который зовёт движок. Здесь их только форматируют.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src import domain
from src.recognize.models import UNKNOWN_ZONE, Candidate

from .texts import t

#: Информационный уровень записи: замеры и фото продукта (`INF09`–`INF11`,
#: задача T057). Это не ставка вычета и не порог буквы — тех в коде быть не
#: может (`data/scoring.json`), — а признак, по которому подтверждение
#: называется «замер», а не «нарушение». Смысл уровня описан в
#: `src/domain/models.py: Finding`.
INFO_LEVEL = "D0"

#: Сколько знаков комментария показывать, подтверждая, что бот услышал.
NOTE_PREVIEW_LIMIT = 160

#: Сколько знаков слов аудитора показывать при фиксации по словам (T117, D063;
#: с T121 — рядом с уже сделанной записью). Не 160, как в предпросмотре
#: «Услышал»: там нужна отметка, что бот услышал, а здесь — сами слова, и резать
#: их нельзя. Сверка по словам отвечает по одной
#: сработавшей строке карты, а в одной фразе бывает два нарушения (правило 11
#: `docs/03-recording-rules.md`), и второе стоит обычно в конце — обрезка
#: спрятала бы ровно его. Потолок при этом есть, и причина у него одна:
#: сообщение обязано влезть в лимит телеграма (4096 знаков) вместе с пунктом,
#: строкой карты и подсказкой. Живым словам он не мешает: самая длинная
#: формулировка боевых проверок в `examples/` — 204 знака при медиане 124.
FAST_NOTE_LIMIT = 3000


def shorten(text: str, limit: int = NOTE_PREVIEW_LIMIT) -> str:
    """Сжать до одной читаемой строки: аудитору нужна отметка, а не пересказ."""
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def percent(value: float) -> str:
    """Процент так же, как его печатает движок: одна цифра после точки."""
    return f"{value:.1f}"


def zone_titles(lang: str) -> dict[str, str]:
    """Код зоны → название на нужном языке. Коды связывают, названия показывают."""
    return {zone.code: zone.title(lang) for zone in domain.list_zones()}


def zone_title(code: str, lang: str, titles: Mapping[str, str] | None = None) -> str:
    """Название зоны. Незнакомый код показываем как есть — врать про зону нельзя."""
    if not code or code == UNKNOWN_ZONE:
        return t("record.zone_unknown", lang)
    known = zone_titles(lang) if titles is None else titles
    return known.get(code, code)


def candidate_lines(
    candidates: Sequence[Candidate],
    lang: str,
    taken: Mapping[tuple[str, str], int] | None = None,
) -> str:
    """Пронумерованный список предложений: пункт, класс, зона, формулировка.

    Претензии проверки правил (`Candidate.flags`) не прячутся: формулировку с
    пометкой аудитор обязан прочитать глазами, а не подтвердить не глядя.

    `taken` — карта уже занятых пар «пункт + зона» (`refusal.occupied_pairs`,
    задача T137). Занятый пункт попадает сюда штатно: сверка со списком
    нарушений упирается в отказ движка, и материал уходит модели, потому что на
    кадре бывает второе нарушение. Непомеченным он выглядит как обычное
    предложение — и нажатие даёт второй отказ подряд по тому же поводу, о
    котором продукт только что сказал сам.

    Пара, а не код: тот же пункт в другой зоне — законная и частая запись, и
    пометить её значило бы отговаривать аудитора от верного действия.
    """
    titles = zone_titles(lang)
    occupied = taken or {}
    lines = []
    for index, candidate in enumerate(candidates, start=1):
        key = "record.candidate_flagged" if candidate.flags else "record.candidate_line"
        line = t(
            key,
            lang,
            index=index,
            code=candidate.code,
            level=candidate.level,
            zone=zone_title(candidate.zone, lang, titles),
            wording=candidate.wording,
        )
        n = occupied.get((candidate.code, candidate.zone))
        if n is not None:
            line += t("record.candidate_taken", lang, n=n)
        lines.append(line)
    return "\n".join(lines)


def zone_unusual_mark(finding: domain.Finding, lang: str) -> str:
    """Пометка «зона не из списка пункта» — или пусто (T147).

    Движок такую запись пропускает и лишь помечает флагом (`zone_unusual`,
    `docs/04-engine.md`), а в отчёт партнёру пометка не попадает вовсе.
    Значит, единственное место, где нетипичную зону видно, — чат, и показать
    её обязан бот.

    Одной функцией на все три строки записи намеренно. Пометка стояла только в
    первичной фиксации, а правка зоны — самый частый способ увести запись туда,
    где пункта нет, — молчала, как молчал и список перед сборкой отчёта.
    Раздельные условия в трёх местах и есть причина, по которой два из трёх
    оказались забыты.
    """
    return t("record.zone_unusual", lang) if finding.zone_unusual else ""


def confirm_line(finding: domain.Finding, lang: str) -> str:
    """Подтверждение фиксации — одна строка (T055).

    Пункт, класс, зона. Ни таблицы после каждого кадра, ни пересказа
    формулировки пункта: `docs/06-mvp-bot.md`, шаг 5.

    **Накопленного процента здесь больше нет** (T162, решение владельца D072:
    «показывать только в конце»). Во время обхода число аудитору ничего не
    даёт, а соблазн не записывать мелочь создаёт — и такое влияние не видно ни
    в отчёте, ни в базе. Оценка целиком показывается при завершении
    (`finish.summary`), и это единственное место, где она нужна.
    """
    key = "record.saved_info" if finding.level == INFO_LEVEL else "record.saved"
    line = t(
        key,
        lang,
        n=finding.n,
        code=finding.code,
        level=finding.level,
        zone=zone_title(finding.zone, lang),
    )
    return line + zone_unusual_mark(finding, lang)


def confirmed_block(
    finding: domain.Finding, lang: str, *, title: str, zone_guessed: bool = False
) -> str:
    """Запись, которую аудитор подтвердил кнопкой (T055, расширен T135).

    Раньше здесь была одна строка `confirm_line`, и спека (`docs/06-mvp-bot.md`,
    шаг 5) объясняла это тем, что пункт аудитор «прочитал на кнопке». На кнопке
    до T136 стояла голая цифра, а формулировка — в перечне выше, откуда взгляд
    уже ушёл; проверить же строку `#1 CLN05 · D1 · Тепловой участок` нечем —
    **код глазами не читается**. Это тот же довод, по которому вопрос пункта
    попал в `fixed_block`, и асимметрия между двумя показами выходила обратной
    здравому смыслу: подробно там, где человек ничего не подтверждал.

    Блоком быстрого пути это всё же не становится. Строки карты у подтверждённой
    записи нет вовсе, «ваших слов» тоже: её текстом стала формулировка модели, и
    звать её словами аудитора было бы враньём. Добавки ровно две — вопрос пункта
    и то, что уйдёт в отчёт партнёру.

    Совпали текст записи и вопрос пункта — показывается одно: так ложится ручной
    выбор пункта по кадру без комментария, и повтор выдал бы за две вещи одну.

    `zone_guessed` — та же оговорка и тем же текстом, что у `fixed_block` (T156).
    Правило про зону из памяти одно на все пути записи, а стояло оно только на
    быстром: подсказка уходила в модель, модель возвращала зону как свою, и
    подтверждение печаталось без оговорки. Опасность здесь меньше — зона видна
    на кнопке кандидата, — но вычет уезжает партнёру в ту же зону, и пометка,
    которая появляется через раз, перестаёт что-либо значить.
    """
    line = confirm_line(finding, lang)
    guess = t("record.fixed_zone_guess", lang) if zone_guessed else ""
    note = shorten(finding.text, FAST_NOTE_LIMIT)
    if note.strip() == title.strip():
        return t("record.confirmed_plain", lang, line=line, guess=guess, title=title)
    return t("record.confirmed", lang, line=line, guess=guess, title=title, note=note)


def fixed_block(
    finding: domain.Finding,
    lang: str,
    *,
    title: str,
    cue: str,
    zone_guessed: bool = False,
) -> str:
    """Запись, легшая по словам сразу, без подтверждения (T121, D064).

    Не одна строка, и это главное отличие от `confirm_line`. Подтверждения
    больше нет, а значит, нет и момента, когда аудитор читал пункт на кнопке:
    строка `#1 CLN02 · D1 · Кассовая зона · 99.5%` промах сопоставления не
    показывает никак — код глазами не читается. Поэтому рядом стоят вопрос
    пункта словами, слова аудитора и сработавшая строка карты: три вещи, по
    которым промах видно, и все три — вместо снятой кнопки.

    Слова берутся из САМОЙ записи, а не из показанного предложения: показать
    надо то, что легло в отчёт, а не то, что собирались положить. Потолок длины
    телеграмный (`FAST_NOTE_LIMIT`) и живым словам не мешает.

    `zone_guessed` — зона взята из памяти о прошлой записи (D048), а не из этих
    слов (T124). Тогда «по вашим словам» про зону неправда, и оговорка стоит
    прямо под строкой записи: сама зона в ней видна, но не видно, откуда она
    взялась, — а вычет уезжает партнёру в ту зону, которую бот подставил сам.
    """
    return t(
        "record.fixed",
        lang,
        line=confirm_line(finding, lang),
        guess=t("record.fixed_zone_guess", lang) if zone_guessed else "",
        title=title,
        note=shorten(finding.text, FAST_NOTE_LIMIT),
        cue=cue,
    )


def changed_line(finding: domain.Finding, lang: str) -> str:
    """Та же строка после правки (T056).

    Процент пересчитывается движком, как и раньше, но аудитору по ходу обхода
    не показывается (T162, D072) — как и в `confirm_line`.

    Пометка «замер» держится и здесь: смена зоны у информационной записи не
    превращает её в нарушение, а строка без пометки читалась бы именно так.

    Пометка нетипичной зоны — тем более (T147): именно правка чаще всего и
    уводит запись в зону, где этого пункта нет.
    """
    line = t(
        "edit.changed_info" if finding.level == INFO_LEVEL else "edit.changed",
        lang,
        n=finding.n,
        code=finding.code,
        level=finding.level,
        zone=zone_title(finding.zone, lang),
    )
    return line + zone_unusual_mark(finding, lang)


def counts_line(counts: Mapping[str, int]) -> str:
    """Счётчики по классам так, как их назвал движок: `D1 — 5, D2 — 1`.

    Классы не перечисляются здесь списком: это методика, и она приходит из
    оценки. Появится новый класс — строка соберётся сама.
    """
    return ", ".join(f"{level} — {count}" for level, count in sorted(counts.items()) if count)


def record_lines(findings: Sequence[domain.Finding], lang: str) -> str:
    """Список зафиксированного для предвычитки отчёта (T058).

    Записи, которые бот распознал по кадру сам, помечены (решение D044): за
    формулировку со слов аудитора отвечает аудитор, за догадку по картинке —
    нет, и перед отправкой партнёру это должно быть видно.

    Пометку несёт сама запись (`Finding.source`, задача T108). Пустой источник
    — это «неизвестно», а не «со слов аудитора»: так выглядят проверки,
    начатые до D044, и выдавать их за чьи-то слова нельзя.

    Нетипичная зона называется здесь же (T147). Это предвычитка — последний
    момент, когда ошибку в зоне ещё можно поймать: дальше отчёт уходит
    партнёру, а в нём пометки нет.
    """
    titles = zone_titles(lang)
    lines = []
    for finding in findings:
        mark = t("finish.source_photo", lang) if finding.source == domain.SOURCE_PHOTO else ""
        lines.append(
            t(
                "finish.record_line",
                lang,
                n=finding.n,
                code=finding.code,
                level=finding.level,
                zone=zone_title(finding.zone, lang, titles),
                source=mark,
                text=shorten(finding.text),
                unusual=zone_unusual_mark(finding, lang),
            )
        )
    return "\n".join(lines)
