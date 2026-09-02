"""Окружение блока `bot`: токен, список разрешённых Telegram ID, режим запуска."""

from __future__ import annotations

import pytest

from src.bot.config import BotSettings, load_bot_settings
from src.bot.errors import BotConfigError


def test_loads_settings_from_mapping() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111, 222 ,333",
        "BOT_MODE": "polling",
    }
    settings = load_bot_settings(env)
    assert settings == BotSettings(
        token="123:abc",
        allowed_ids=frozenset({111, 222, 333}),
        mode="polling",
        auditor_names={},
    )


def test_missing_token_is_config_error() -> None:
    env = {"ALLOWED_TELEGRAM_IDS": "111", "BOT_MODE": "polling"}
    with pytest.raises(BotConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_bot_settings(env)


def test_missing_allowed_ids_is_config_error() -> None:
    """Список разрешённых ID пуст — бот не должен подниматься, отвечая всем подряд."""
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "BOT_MODE": "polling"}
    with pytest.raises(BotConfigError, match="ALLOWED_TELEGRAM_IDS"):
        load_bot_settings(env)


def test_blank_allowed_ids_is_config_error() -> None:
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "ALLOWED_TELEGRAM_IDS": "  ", "BOT_MODE": "polling"}
    with pytest.raises(BotConfigError, match="ALLOWED_TELEGRAM_IDS"):
        load_bot_settings(env)


def test_non_numeric_allowed_id_is_config_error() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111,не-число",
        "BOT_MODE": "polling",
    }
    with pytest.raises(BotConfigError, match="не-число"):
        load_bot_settings(env)


def test_default_mode_is_polling() -> None:
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "ALLOWED_TELEGRAM_IDS": "111"}
    assert load_bot_settings(env).mode == "polling"


def test_unknown_mode_is_config_error() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111",
        "BOT_MODE": "carrier-pigeon",
    }
    with pytest.raises(BotConfigError, match="carrier-pigeon"):
        load_bot_settings(env)


def test_trailing_comma_is_ignored() -> None:
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "ALLOWED_TELEGRAM_IDS": "111,222,"}
    assert load_bot_settings(env).allowed_ids == frozenset({111, 222})


def test_only_commas_is_config_error() -> None:
    """Непустая строка, но без единого ID — тот же отказ, что и на пустой."""
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "ALLOWED_TELEGRAM_IDS": ",,,"}
    with pytest.raises(BotConfigError, match=r"ALLOWED_TELEGRAM_IDS пуст"):
        load_bot_settings(env)


def test_reads_from_real_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:zzz")
    monkeypatch.setenv("ALLOWED_TELEGRAM_IDS", "42")
    monkeypatch.setenv("BOT_MODE", "polling")
    settings = load_bot_settings()
    assert settings.token == "999:zzz"
    assert settings.allowed_ids == frozenset({42})


def test_auditor_names_are_parsed_into_map() -> None:
    """Имя проверяющего берётся по Telegram ID (T063): карта «ID:имя» через запятую."""
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111,222",
        "AUDITOR_NAMES": "111:Владимир Гарро, 222 : Пётр Петров ",
    }
    assert load_bot_settings(env).auditor_names == {111: "Владимир Гарро", 222: "Пётр Петров"}


def test_auditor_names_absent_is_empty_map_not_failure() -> None:
    """Переменная необязательна: без неё имя берётся из профиля Telegram."""
    env = {"TELEGRAM_BOT_TOKEN": "123:abc", "ALLOWED_TELEGRAM_IDS": "111"}
    assert load_bot_settings(env).auditor_names == {}


def test_auditor_names_without_colon_is_config_error() -> None:
    """Молча пропустить кривую запись нельзя: имя молча уедет из профиля, а не из карты."""
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111",
        "AUDITOR_NAMES": "Владимир Гарро",
    }
    with pytest.raises(BotConfigError, match="AUDITOR_NAMES"):
        load_bot_settings(env)


def test_auditor_names_with_non_numeric_id_is_config_error() -> None:
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111",
        "AUDITOR_NAMES": "garro:Владимир Гарро",
    }
    with pytest.raises(BotConfigError, match="AUDITOR_NAMES"):
        load_bot_settings(env)


def test_auditor_name_for_id_outside_allowed_list_is_config_error() -> None:
    """Две разъезжающиеся копии списка ID — источник тихой ошибки: имя есть, доступа нет."""
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "ALLOWED_TELEGRAM_IDS": "111",
        "AUDITOR_NAMES": "999:Чужой",
    }
    with pytest.raises(BotConfigError, match="999"):
        load_bot_settings(env)
