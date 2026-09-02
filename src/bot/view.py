"""Из чего складываются строки, которые аудитор читает в чате.

Вынесено из роутеров, потому что строки и хендлеры живут по разным законам:
хендлер отвечает за порядок диалога, а здесь — за то, что именно человек видит.
Смешанные вместе, они превращаются в роутер на четыреста строк, где формат
подтверждения приходится искать среди `await`.

Ни одной цифры оценки тут не считается: процент и буква приходят из
`domain.score()`, который зовёт движок. Здесь их только форматируют.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from src import domain
from src.recognize.models import UNKNOWN_ZONE, Candidate

from . import sidecar
from .texts import t

#: Информационный уровень записи: замеры и фото продукта (`INF09`–`INF11`,
#: задача T057). Это не ставка вычета и не порог буквы — тех в коде быть не
#: может (`data/scoring.json`), — а признак, по которому подтверждение
#: называется «замер», а не «нарушение». Смысл уровня описан в
#: `src/domain/models.py: Finding`.
INFO_LEVEL = "D0"

#: Сколько знаков комментария показывать, подтверждая, что бот услышал.
NOTE_PREVIEW_LIMIT = 160


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


def candidate_lines(candidates: Sequence[Candidate], lang: str) -> str:
    """Пронумерованный список предложений: пункт, класс, зона, формулировка.

    Претензии проверки правил (`Candidate.flags`) не прячутся: формулировку с
    пометкой аудитор обязан прочитать глазами, а не подтвердить не глядя.
    """
    titles = zone_titles(lang)
    lines = []
    for index, candidate in enumerate(candidates, start=1):
        key = "record.candidate_flagged" if candidate.flags else "record.candidate_line"
        lines.append(
            t(
                key,
                lang,
                index=index,
                code=candidate.code,
                level=candidate.level,
                zone=zone_title(candidate.zone, lang, titles),
                wording=candidate.wording,
            )
        )
    return "\n".join(lines)


def confirm_line(finding: domain.Finding, pct: float, lang: str) -> str:
    """Подтверждение фиксации — одна строка (T055).

    Пункт, класс, зона, накопленный процент. Ни таблицы после каждого кадра, ни
    пересказа формулировки пункта: `docs/06-mvp-bot.md`, шаг 5.
    """
    key = "record.saved_info" if finding.level == INFO_LEVEL else "record.saved"
    line = t(
        key,
        lang,
        n=finding.n,
        code=finding.code,
        level=finding.level,
        zone=zone_title(finding.zone, lang),
        pct=percent(pct),
    )
    if finding.zone_unusual:
        # Движок такую запись пропускает и лишь помечает. Показать пометку
        # обязан бот, иначе о ней узнает только партнёр.
        return line + t("record.zone_unusual", lang)
    return line


def changed_line(finding: domain.Finding, pct: float, lang: str) -> str:
    """Та же строка после правки — с пересчитанным процентом (T056).

    Пометка «замер» держится и здесь: смена зоны у информационной записи не
    превращает её в нарушение, а строка без пометки читалась бы именно так.
    """
    return t(
        "edit.changed_info" if finding.level == INFO_LEVEL else "edit.changed",
        lang,
        n=finding.n,
        code=finding.code,
        level=finding.level,
        zone=zone_title(finding.zone, lang),
        pct=percent(pct),
    )


def counts_line(counts: Mapping[str, int]) -> str:
    """Счётчики по классам так, как их назвал движок: `D1 — 5, D2 — 1`.

    Классы не перечисляются здесь списком: это методика, и она приходит из
    оценки. Появится новый класс — строка соберётся сама.
    """
    return ", ".join(f"{level} — {count}" for level, count in sorted(counts.items()) if count)


def record_lines(findings: Sequence[domain.Finding], sources: Mapping[int, str], lang: str) -> str:
    """Список зафиксированного для предвычитки отчёта (T058).

    Записи, которые бот распознал по кадру сам, помечены (решение D044): за
    формулировку со слов аудитора отвечает аудитор, за догадку по картинке —
    нет, и перед отправкой партнёру это должно быть видно.
    """
    titles = zone_titles(lang)
    lines = []
    for finding in findings:
        mark = (
            t("finish.source_photo", lang) if sources.get(finding.n) == sidecar.SOURCE_PHOTO else ""
        )
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
            )
        )
    return "\n".join(lines)


def unclaimed_lines(frames: Iterable[sidecar.SeenFrame], lang: str) -> str:
    """Кадры, не попавшие ни в одну запись (T068)."""
    return "\n".join(t("finish.unclaimed_line", lang, message_id=f.message_id) for f in frames)
