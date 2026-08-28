"""Проверка, что раннер тестов работает и видит пакеты проекта."""

import src


def test_пакет_импортируется() -> None:
    assert src.__doc__ is not None
