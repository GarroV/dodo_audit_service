"""T030: перечень кандидатов, который уходит в запрос к модели.

Порядок сборки один и тот же всегда:

1. **База — зональный перечень.** Все пункты-нарушения, применимые к зоне. Резать
   его нельзя ничем: разведка показала, что при отсутствии правильного кода среди
   кандидатов модель не молчит, а уверенно предлагает похожий пункт с осмысленной
   формулировкой. Такая ошибка не выглядит как ошибка.
2. **Карта кадров — сверху.** Коды, поднятые словами комментария, идут первыми и
   добавляются, даже если в зоне такого пункта нет: слова аудитора важнее вида
   зоны, а окончательное решение всё равно за ним.
3. **Служебное отсекается.** `kind=aggregate` и `kind=info` не предлагаются
   (правило 8), `MGM22`/`MGM23` — ручное решение аудитора.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain import list_items

from .cues import load_cues, match_cues

#: Пункты, которые ставит только человек: «другое критическое нарушение» и
#: «массовость нарушений D1». Модель их не предлагает — так велит последний
#: раздел `data/photo-cues.md`. В ручном перечне кнопок они остаются.
MANUAL_ONLY = ("MGM22", "MGM23")


@dataclass(frozen=True)
class Shortlist:
    """Кандидаты для запроса: коды в порядке показа модели."""

    codes: tuple[str, ...]
    cue_hits: tuple[str, ...]
    zone: str | None

    def __len__(self) -> int:
        return len(self.codes)


def _offered(kind: str, code: str, *, with_manual: bool) -> bool:
    if kind != "violation":
        return False
    return with_manual or code not in MANUAL_ONLY


def shortlist(note: str, zone_hint: str | None = None, *, with_manual: bool = False) -> Shortlist:
    """Собрать перечень кандидатов. Неизвестная зона — отказ от `domain`.

    `with_manual` включает пункты ручного решения аудитора: он нужен ручному
    выбору кнопками, где решает человек, и выключен для запроса к модели.
    """
    hits = match_cues(note, load_cues())
    everything = list_items()
    offered = {i.code for i in everything if _offered(i.kind, i.code, with_manual=with_manual)}
    base_items = everything if zone_hint is None else list_items(zone=zone_hint)
    base = [i.code for i in base_items if i.code in offered]
    front = tuple(code for code in hits if code in offered)
    codes = list(front) + [code for code in base if code not in front]
    return Shortlist(codes=tuple(codes), cue_hits=front, zone=zone_hint)
