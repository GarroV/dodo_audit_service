"""Типы предметной области, которыми блок разговаривает с остальными.

Язык — параметр, никогда не константа: у пункта, зоны и оценки хранятся обе
формулировки, а выбирает вызывающий. Поэтому `question`, `process`, `title` и
`label` — методы с языком, а не поля.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ValidationError

#: Языки, на которых методика есть в данных: колонки `*_ru` и `*_en`
#: (`data/checklist.csv`, `data/zones.csv`). Третий язык появится строками
#: переводов, а не правкой кода.
TEXT_LANGS = ("ru", "en")


def pick_text(ru: str, en: str, lang: str) -> str:
    """Выбрать формулировку по языку. Неизвестный язык — отказ, а не подстановка.

    Молчаливый откат на русский дал бы партнёру отчёт, в котором часть текста
    внезапно не на его языке, и заметил бы это только он.
    """
    if lang == "ru":
        return ru
    if lang == "en":
        return en
    raise ValidationError(f"Язык «{lang}» в методике не заведён. Доступны: {', '.join(TEXT_LANGS)}")


@dataclass(frozen=True)
class ChecklistItem:
    """Строка чек-листа. Ссылаться на пункт можно только кодом."""

    code: str
    kind: str
    process_ru: str
    process_en: str
    question_ru: str
    question_en: str
    levels: list[str]
    zones: list[str]
    days: int

    def process(self, lang: str) -> str:
        return pick_text(self.process_ru, self.process_en, lang)

    def question(self, lang: str) -> str:
        return pick_text(self.question_ru, self.question_en, lang)

    def applies_to(self, zone: str) -> bool:
        """Пустой список зон и `*` означают «во всех зонах» — так же читает движок."""
        return not self.zones or self.zones == ["*"] or zone in self.zones


@dataclass(frozen=True)
class Zone:
    """Физическая зона пиццерии и её доля в оценке."""

    code: str
    title_ru: str
    title_en: str
    share_pct: float

    def title(self, lang: str) -> str:
        return pick_text(self.title_ru, self.title_en, lang)


@dataclass(frozen=True)
class Finding:
    """Зафиксированная запись проверки.

    `level = D0` — это не нарушение, а информационная запись (замер, настройка,
    фото продукта): такого уровня нет в ставках вычетов, поэтому он стоит ноль.
    """

    n: int
    code: str
    level: str
    zone: str
    text: str
    comment: str = ""
    photos: list[str] = field(default_factory=list)
    zone_unusual: bool = False

    @property
    def is_violation(self) -> bool:
        return self.level != "D0"


@dataclass(frozen=True)
class Inspection:
    """Состояние проверки одного чата.

    Три языка хранятся раздельно и ни один не выведен из другого: аудитор ведёт
    бота по-русски, говорит по-сербски, а отчёт партнёру уходит на английском.
    """

    chat_id: int
    unit: str
    kind: str
    date: str
    report_lang: str
    ui_lang: str
    speech_lang: str
    checklist_version: str
    tenant: str
    city: str = ""
    partner: str = ""
    contact: str = ""
    auditor: str = ""
    findings: list[Finding] = field(default_factory=list)

    def finding(self, n: int) -> Finding | None:
        return next((f for f in self.findings if f.n == n), None)


@dataclass(frozen=True)
class ZoneScore:
    """Итог по одной зоне: сколько записей и сколько от доли осталось."""

    code: str
    name_ru: str
    name_en: str
    share: float
    counts: dict[str, int]
    loss: float
    left: float
    zeroed: bool

    def name(self, lang: str) -> str:
        return pick_text(self.name_ru, self.name_en, lang)


@dataclass(frozen=True)
class Score:
    """Оценка проверки — целиком из `audit.py score`, ни одной цифры своей."""

    pct: float
    grade: str
    label_ru: str
    label_en: str
    counts: dict[str, int]
    deductions: float
    by_zone: dict[str, ZoneScore]

    def label(self, lang: str) -> str:
        return pick_text(self.label_ru, self.label_en, lang)
