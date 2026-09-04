"""T020 и T024: окружение блока `domain` и каталог методики.

Чек-лист, зоны и ставки вычетов читаются из `AUDIT_DATA_DIR`. Каталога нет или
он неполный — блок обязан упасть с внятным сообщением, а не отдать пустой
чек-лист: проверка на пустом чек-листе выглядит как «нарушений не нашлось».

Форк методики (`checklist_data/`, который создаёт `manage.py`) останавливает
запуск: при папке состояния на чат разные чаты начали бы считать по разной
методике, и расхождение вылезло бы только в готовом отчёте у партнёра.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import DATA, TEST_DATA, requires_data

from src.domain import add_finding, check_environment, list_items, start_inspection
from src.domain.errors import ConfigError


def подложить_правдоподобные_каталоги(tmp_path: Path) -> None:
    """Разложить рядом ровно то, на что подмывает молча свалиться по умолчанию.

    Без этого тест «переменной нет — отказ» проходит и на реализации с тихим
    путём по умолчанию: та тоже падает, просто по другой причине. Проверено
    порчей — подстановка `./data` теста не уронила.
    """
    shutil.copytree(TEST_DATA, tmp_path / "data")
    (tmp_path / ".state").mkdir()


def test_без_переменной_каталога_методики_падает_и_называет_её(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    подложить_правдоподобные_каталоги(tmp_path)
    monkeypatch.delenv("AUDIT_DATA_DIR", raising=False)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / ".state"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as e:
        check_environment()
    assert "AUDIT_DATA_DIR" in str(e.value), f"не сказано, чего не хватает: {e.value}"
    assert "не задана" in str(e.value).lower(), (
        f"методику взяли из соседней папки вместо отказа: {e.value}"
    )


def test_без_переменной_каталога_состояния_падает_и_называет_её(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    подложить_правдоподобные_каталоги(tmp_path)
    monkeypatch.setenv("AUDIT_DATA_DIR", str(TEST_DATA))
    monkeypatch.delenv("STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as e:
        check_environment()
    assert "STATE_DIR" in str(e.value), f"не сказано, чего не хватает: {e.value}"
    assert "не задана" in str(e.value).lower(), f"состояние ушло бы в папку по умолчанию: {e.value}"


def test_несуществующий_каталог_методики_назван_в_сообщении(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "нет-такого"
    monkeypatch.setenv("AUDIT_DATA_DIR", str(missing))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as e:
        check_environment()
    assert str(missing) in str(e.value), f"не показан путь, который искали: {e.value}"


def test_неполный_каталог_называет_недостающий_файл(
    data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без zones.csv движок молча взял бы зоны из своей копии данных."""
    (data_copy / "zones.csv").unlink()
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as e:
        check_environment()
    assert "zones.csv" in str(e.value), f"не назван недостающий файл: {e.value}"


def test_форк_чек_листа_в_рабочем_каталоге_останавливает_запуск(
    domain_env: Path, tmp_path: Path
) -> None:
    """T024: `manage.py` кладёт `checklist_data/` в текущую папку — это форк."""
    (tmp_path / "checklist_data").mkdir()
    with pytest.raises(ConfigError) as e:
        check_environment()
    assert "checklist_data" in str(e.value), f"не сказано, что нашли: {e.value}"
    assert str(tmp_path) in str(e.value), f"не сказано, где нашли: {e.value}"


def test_форк_чек_листа_в_каталоге_состояния_останавливает_запуск(domain_env: Path) -> None:
    domain_env.mkdir(parents=True)
    (domain_env / "checklist_data").mkdir()
    with pytest.raises(ConfigError) as e:
        check_environment()
    assert "checklist_data" in str(e.value)


def test_форк_чек_листа_в_папке_чата_останавливает_запись(domain_env: Path) -> None:
    """Папка чата — рабочий каталог движка, форк в ней он бы и подхватил.

    Явный `CHECKLIST_DIR` перебивает такой форк по приоритету, но полагаться на
    один заслон нельзя: методику из-под ног не выдёргивают посреди проверки.
    """
    start_inspection(42, unit="Белград-1", kind="planned", report_lang="ru")
    (domain_env / "chat_42" / "checklist_data").mkdir()
    with pytest.raises(ConfigError) as e:
        add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    assert "checklist_data" in str(e.value), f"не сказано, что нашли: {e.value}"


def test_чтение_чек_листа_без_настроенного_окружения_это_отказ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пустой список тут страшнее исключения: он выглядит как честный ответ."""
    подложить_правдоподобные_каталоги(tmp_path)
    monkeypatch.delenv("AUDIT_DATA_DIR", raising=False)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / ".state"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        list_items()


def test_каталог_методики_проходит_проверку(domain_env: Path) -> None:
    settings = check_environment()
    assert settings.data_dir == TEST_DATA
    assert settings.state_dir == domain_env
    assert settings.audit_script.is_file(), "движок не найден по пути, который блок будет звать"


@requires_data
def test_боевой_каталог_методики_проходит_проверку(live_data_env: Path) -> None:
    """Отдельно и намеренно на БОЕВОЙ методике (T141): это про неё, а не про продукт.

    Смысл — «каталог управляющей компании полон настолько, что продукт на нём
    поднимется». Синтетический набор такого не проверяет: он собран нами и
    полон по построению, а неполнота боевого — отказ на старте у аудитора.
    """
    settings = check_environment()
    assert settings.data_dir == DATA
    assert settings.state_dir == live_data_env
