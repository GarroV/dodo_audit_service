"""T183: сырые слова аудитора хранятся рядом с записью.

Найдено предыдущей задачей блока (T166) при честном отказе: признак «это
чистота» проверяем, но мерить его не на чем — **слова аудитора продукт не хранит
нигде**. Комментарий в запись не пишется намеренно (текстом записи становится
формулировка по правилам фиксации), предложение модели умирает вместе с
процессом, а слова доживают только на быстром пути и только потому, что там они
и есть текст записи.

Без них нельзя ни померить такой признак, ни разобрать, ПОЧЕМУ модель
промахнулась: у нас есть предложение системы (T181) и итоговая запись, но нет
того, что человек сказал.

Хранятся тем же приёмом, что источник записи (D044) и предложение системы
(D077): свой ключ в блоке `domain` файла проверки, а не поле внутри структуры
движка — записи ведёт движок, и дописывать в его структуры своё значит однажды
их потерять.

**Слова не меняются никогда.** Это показание о моменте, а не поле записи:
правка переписывает формулировку, которая уйдёт партнёру, а сказанное на точке
от этого не становится другим. Разница между сказанным и записанным и есть тот
сигнал, ради которого слова сохраняются, — переписанные правкой, они стирали бы
его ровно в тот момент, когда он появляется.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain import add_finding, drop_finding, edit_finding, get_state, start_inspection
from src.domain.config import check_environment
from src.domain.engine import state_file
from src.domain.errors import DomainError
from src.domain.state import read_words

CHAT = 793_000_183

#: Пример владельца из D077 — ровно та фраза, ради которой заведён T166.
СКАЗАНО = "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА"


def _начатая() -> None:
    start_inspection(
        CHAT,
        unit="Проверка сырых слов",
        kind="planned",
        report_lang="ru",
        date="2026-09-04",
        auditor="Тест",
    )


def _файл() -> Path:
    return state_file(CHAT, check_environment())


def _запись(n: int = 1):
    state = get_state(CHAT)
    assert state is not None
    finding = state.finding(n)
    assert finding is not None
    return finding


def test_слова_аудитора_живут_рядом_с_записью(domain_env: Path) -> None:
    """То, ради чего задача заведена: сказанное сохраняется целиком."""
    _начатая()
    add_finding(
        CHAT,
        "CLN05",
        "D1",
        "hot_kitchen",
        "Нагар на подине печи",
        words=СКАЗАНО,
    )

    assert _запись().words == СКАЗАНО


def test_слова_не_подменяются_текстом_записи(domain_env: Path) -> None:
    """Смысл хранения — в РАЗНИЦЕ: текст записи и слова аудитора разные вещи."""
    _начатая()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи", words=СКАЗАНО)

    запись = _запись()
    assert запись.text == "Нагар на подине печи"
    assert запись.words == СКАЗАНО


def test_запись_без_слов_остаётся_без_слов(domain_env: Path) -> None:
    """Кадр без комментария — законный случай: слов не было вовсе."""
    _начатая()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи")

    assert _запись().words == ""
    assert read_words({}, state_file(CHAT, check_environment())) == {}


def test_правка_записи_слова_не_трогает(domain_env: Path) -> None:
    """Правка меняет то, что уйдёт партнёру, а не то, что было сказано на точке."""
    _начатая()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи", words=СКАЗАНО)

    edit_finding(CHAT, 1, text="Нагар на подине печи, зона выпечки")
    edit_finding(CHAT, 1, zone="dining")

    запись = _запись()
    assert запись.text == "Нагар на подине печи, зона выпечки"
    assert запись.zone == "dining"
    assert запись.words == СКАЗАНО, "правка стёрла показание о моменте"


def test_удаление_записи_уносит_её_слова(domain_env: Path) -> None:
    """Снятая запись не оставляет за собой речь о нарушении, которого в проверке нет.

    Проверяется само хранилище, а не соседняя запись: движок номера НЕ
    переиспользует (в состоянии живёт счётчик `seq`), поэтому чужой записи
    оставленные слова достаться не могут. Но выборку для управляющей компании
    они бы засорили — там это речь о нарушении, которого в проверке уже нет.
    """
    _начатая()
    add_finding(CHAT, "CLN05", "D1", "hot_kitchen", "Нагар на подине печи", words=СКАЗАНО)
    сохранено = json.loads(_файл().read_text(encoding="utf-8"))["domain"]["words"]
    assert сохранено == {"1": СКАЗАНО}, сохранено

    drop_finding(CHAT, 1)

    осталось = json.loads(_файл().read_text(encoding="utf-8"))["domain"]["words"]
    assert осталось == {}, f"слова снятой записи остались в проверке: {осталось}"


def test_чужая_структура_слов_читается_отказом(domain_env: Path) -> None:
    """Прочитанное «неизвестно что» уехало бы в выборку как слова аудитора."""
    _начатая()
    path = state_file(CHAT, check_environment())

    with pytest.raises(DomainError) as exc:
        read_words({"domain": {"words": {"1": {"текст": "не строка"}}}}, path)

    assert "#1" in str(exc.value)


def test_номер_записи_не_число_это_отказ(domain_env: Path) -> None:
    _начатая()
    path = state_file(CHAT, check_environment())

    with pytest.raises(DomainError):
        read_words({"domain": {"words": {"первая": "слова"}}}, path)
