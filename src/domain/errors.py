"""Отказы блока `domain`.

Отдельные классы нужны не для красоты: бот показывает аудитору текст отказа, а
решает по типу — переспросить (`ValidationError`), предложить начать проверку
(`InspectionNotStarted`) или вообще не подниматься (`ConfigError`).
"""

from __future__ import annotations


class DomainError(Exception):
    """Базовый отказ предметной области."""


class ConfigError(DomainError):
    """Окружение непригодно: нет переменной, каталога методики или найден форк.

    Поднимается на старте и при каждом обращении к методике. Пустой чек-лист
    вместо исключения выглядел бы как честный ответ «нарушений не нашлось».
    """


class ValidationError(DomainError):
    """Значение отвергнуто до вызова движка: неизвестная зона, код, язык."""


class InspectionNotStarted(DomainError):
    """В этом чате проверка не начата — записывать нечего и считать нечего."""


class EngineError(DomainError):
    """Движок отказал.

    Текст берётся у самого движка: методику и её проверки блок не дублирует,
    но и не глотает — отказ уходит наружу как есть.
    """

    def __init__(self, message: str, *, code: int, command: str) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.command = command
