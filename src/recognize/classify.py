"""T031: разбор комментария аудитора в предложения записей.

Порядок один и тот же: сузить перечень (`shortlist`) → собрать строгую схему по
этому перечню (`schema`) → задать вопрос модели (`client`) → разобрать ответ.
Ни на одном шаге ничего не досочиняется: код и класс приходят перечислением,
зона — из слов аудитора или подсказки, а решение принимает человек.
"""

from __future__ import annotations

from typing import Any

from src.domain import list_zones

from .client import ask_model
from .config import RecognizeSettings, load_recognize_settings
from .cues import class_thresholds
from .models import NONE_CODE, UNKNOWN_ZONE, Candidate, Suggestion
from .prompt import instructions, question_text
from .schema import picks_for, response_schema, split_pick
from .shortlist import shortlist

#: Язык формулировок и текста пунктов по умолчанию. Это параметр, а не
#: константа: у проверки язык отчёта хранится отдельно от языка интерфейса и
#: языка речи, и бот передаёт сюда именно язык отчёта — формулировка уходит в
#: отчёт партнёру.
DEFAULT_LANG = "ru"


def _clamp(value: Any) -> float:
    """Уверенность в границах 0…1. В строгой схеме границ числа не заявить."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))


def needs_photo(note: str, cue_hits: tuple[str, ...]) -> bool:
    """Смотреть ли на кадр. Однозначный комментарий кадра не требует.

    Однозначным считается только тот случай, когда карта кадров подняла ровно
    один пункт и у этого пункта единственный допустимый класс: тогда кадру
    нечего добавить, а его разбор — 1148 лишних токенов входа на каждый
    комментарий. Во всех остальных случаях кадр идёт в запрос, в том числе
    чтобы поймать расхождение слов аудитора с изображением.
    """
    if not note.strip():
        return True
    if len(cue_hits) != 1:
        return True
    return len(picks_for(cue_hits)) != 2  # один пункт с одним классом плюс NONE


def _candidate(record: dict[str, Any], zone_hint: str | None) -> Candidate | None:
    code, level = split_pick(str(record.get("item", "")))
    if code == NONE_CODE or not level:
        return None
    zone = str(record.get("zone", UNKNOWN_ZONE))
    if zone == UNKNOWN_ZONE and zone_hint:
        zone = zone_hint
    return Candidate(
        code=code,
        level=level,
        zone=zone,
        wording=str(record.get("wording", "")).strip(),
        confidence=_clamp(record.get("confidence")),
        reason=str(record.get("reason", "")).strip(),
    )


def _suggestion(
    payload: dict[str, Any],
    usage: dict[str, int],
    zone_hint: str | None,
    settings: RecognizeSettings,
    *,
    used_photo: bool,
) -> Suggestion:
    raw = payload.get("records")
    records = raw if isinstance(raw, list) else []
    candidates = tuple(
        c
        for record in records
        if isinstance(record, dict)
        for c in (_candidate(record, zone_hint),)
        if c is not None
    )[: settings.max_candidates]
    question = str(payload.get("question", "")).strip()
    top = candidates[0] if candidates else None
    needs_human = (
        top is None
        or bool(question)
        or top.confidence < settings.min_confidence
        or top.zone == UNKNOWN_ZONE
    )
    return Suggestion(
        candidates=candidates,
        needs_human=needs_human,
        question=question,
        used_photo=used_photo,
        usage=usage,
    )


def classify(
    note: str,
    photo: bytes | None = None,
    zone_hint: str | None = None,
    *,
    lang: str = DEFAULT_LANG,
    settings: RecognizeSettings | None = None,
    model: str | None = None,
) -> Suggestion:
    """Предложить записи по комментарию аудитора. Решение остаётся за ним.

    `lang` — язык формулировок и текста пунктов (язык отчёта проверки).
    `settings` и `model` нужны замеру точности (T035), который гоняет один и тот
    же вход по нескольким моделям; бот их не передаёт.
    """
    cfg = settings or load_recognize_settings()
    picked = shortlist(note, zone_hint)
    picks = picks_for(picked.codes)
    zones = list_zones()
    use_photo = photo is not None and needs_photo(note, picked.cue_hits)
    answer = ask_model(
        instructions=instructions(class_thresholds()),
        question=question_text(note, picks, zones, zone_hint, lang, with_photo=use_photo),
        schema=response_schema(picks, [z.code for z in zones]),
        photo=photo if use_photo else None,
        settings=cfg,
        model=model,
    )
    return _suggestion(answer.payload, answer.usage, zone_hint, cfg, used_photo=use_photo)
