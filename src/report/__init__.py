"""Блок `report` — PDF-отчёт партнёру и текст письма к нему.

Контракт — `docs/forge/blocks/report.md`. Наружу блок отдаёт две функции;
разметку отчёта, выбор шаблона письма и расчёт оценки он не повторяет, а зовёт
движок подпроцессом. Состояние проверки блок сам не открывает — ходит через
`domain`.
"""

from .build import build_letter, build_pdf
from .errors import PdfNotBuilt, PhotoMissing, ReportError

__all__ = [
    "PdfNotBuilt",
    "PhotoMissing",
    "ReportError",
    "build_letter",
    "build_pdf",
]
