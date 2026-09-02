"""T034: модель недоступна — перечень пунктов для ручного выбора кнопками.

`ModelUnavailable` не роняет проверку (см. `errors.py`): бот ловит отказ и
показывает те же пункты, что пошли бы в запрос к модели, но без обращения к
сети и без ранжирования по словам комментария — порядок тот же, что в самом
чек-листе. Функция здесь не решает, сколько кнопок показать за раз: контракт
блока «не больше пяти» — это про кандидатов модели, ранжированных по
уверенности (аудитор на телефоне не разберёт больше на экране предложений).
Ручной перечень — не предложение, а полный список для осознанного выбора;
показывать ли его страницами — решение бота (T034 со стороны `bot`), не этого
блока.

Пункты ручного решения аудитора (`MGM22`, `MGM23`) здесь есть — это ровно тот
случай, где ответ выставляет только человек.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.domain import allowed_levels, get_item

from .config import DEFAULT_LANG
from .shortlist import shortlist


@dataclass(frozen=True)
class ManualCandidate:
    """Один пункт для кнопки ручного выбора: код, допустимые классы, текст."""

    code: str
    levels: tuple[str, ...]
    title: str


def manual_candidates(
    zone_hint: str | None, *, lang: str = DEFAULT_LANG
) -> tuple[ManualCandidate, ...]:
    """Пункты для ручного выбора — без сети, без ранжирования по словам.

    `zone_hint` сужает так же, как для модели: зональная база не режется,
    `None` — крайний случай, когда даже зона неизвестна, отдаёт пункты всех
    зон. `with_manual=True` — единственное отличие от запроса к модели: тут
    решает человек, и `MGM22`/`MGM23` ему доступны.
    """
    picked = shortlist("", zone_hint, with_manual=True)
    return tuple(
        ManualCandidate(
            code=code, levels=tuple(allowed_levels(code)), title=get_item(code).question(lang)
        )
        for code in picked.codes
    )
