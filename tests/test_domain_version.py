"""T026: составной идентификатор версии методики — имя, дата и отпечаток (D050).

Проверка, посчитанная по одной методике, обязана оставаться объяснимой через
год. Поэтому идентификатор версии составной: имя набора и дата публикации нужны
человеку, отпечаток данных — машине. Отпечаток и есть защита от главного случая:
данные поправили, а дату поднять забыли — под прежним именем такая правка не
проходит.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.domain import check_environment, checklist_version
from src.domain.errors import ConfigError
from src.domain.version import VERSION_FILE

#: Отпечаток в хвосте идентификатора: 12 знаков шестнадцатеричного вида.
FINGERPRINT = r"[0-9a-f]{12}"


@pytest.fixture
def методика(data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Копия боевой методики как каталог `AUDIT_DATA_DIR` — её можно править."""
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def _издано(методика: Path, строка: str) -> None:
    (методика / VERSION_FILE).write_text(f"{строка}\n", encoding="utf-8")


def test_версия_составлена_из_имени_даты_и_отпечатка(методика: Path) -> None:
    _издано(методика, "imf 2026-09-01")

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version()), (
        f"версия {checklist_version()} не составная — D050 требует имя, дату и отпечаток"
    )


def test_правка_данных_под_прежним_именем_даёт_другую_версию(методика: Path) -> None:
    """Тот самый случай D050: данные поправили, дату поднять забыли."""
    _издано(методика, "imf 2026-09-01")
    было = checklist_version()

    with (методика / "checklist.csv").open("a", encoding="utf-8") as f:
        f.write("ZZZ01,violation,Тест,Test,Вопрос,Question,D1,fridge,10\n")

    стало = checklist_version()
    assert стало != было, "правка данных прошла под прежним идентификатором версии"
    assert стало.startswith("imf-2026-09-01-"), "имя и дата обязаны остаться человеку видны"


def test_правка_ставок_вычета_тоже_меняет_версию(методика: Path) -> None:
    """Отпечаток держит всю методику, а не только чек-лист: ставки в нём же."""
    _издано(методика, "imf 2026-09-01")
    было = checklist_version()

    scoring = методика / "scoring.json"
    scoring.write_text(scoring.read_text(encoding="utf-8").replace("0.5", "0.75"), encoding="utf-8")

    assert checklist_version() != было, "правка scoring.json не изменила версию"


def test_версия_устойчива_на_неизменных_данных(методика: Path) -> None:
    _издано(методика, "imf 2026-09-01")

    assert checklist_version() == checklist_version()


def test_без_файла_версии_остаётся_отпечаток_данных(методика: Path) -> None:
    """Набор никто не издавал — имени и даты взять негде, отпечаток остаётся."""
    # Не «файла и так нет», а «файла нет»: копия боевой методики его может нести.
    (методика / VERSION_FILE).unlink(missing_ok=True)

    assert re.fullmatch(rf"local-{FINGERPRINT}", checklist_version())


def test_имя_и_дата_через_дефис_читаются_так_же(методика: Path) -> None:
    """Форма, которой методику подписывали до D050, остаётся допустимой."""
    _издано(методика, "imf-2026-08-21")

    assert re.fullmatch(rf"imf-2026-08-21-{FINGERPRINT}", checklist_version())


def test_файл_версии_без_даты_отвергается_на_старте(методика: Path) -> None:
    """Набор без даты — нарушение D050, и узнать об этом надо до выезда на точку."""
    _издано(методика, "imf")

    with pytest.raises(ConfigError) as отказ:
        check_environment()

    assert VERSION_FILE in str(отказ.value)
    assert "2026-09-01" in str(отказ.value), "в отказе нужен пример правильной формы"


def test_старая_проверка_без_версии_читается(методика: Path) -> None:
    """Правка не имеет права сделать нечитаемыми проверки, заведённые раньше."""
    from src.domain import get_state, start_inspection
    from src.domain.state import DOMAIN_KEY, state_file

    _издано(методика, "imf 2026-09-01")
    start_inspection(4242, "Белград-1", "плановая", "ru")
    path = state_file(4242, check_environment())
    raw = path.read_text(encoding="utf-8").replace(
        f'"checklist_version": "{checklist_version()}"', '"checklist_version": ""'
    )
    path.write_text(raw, encoding="utf-8")

    состояние = get_state(4242)

    assert состояние is not None, "проверка без версии перестала читаться"
    assert состояние.checklist_version == ""
    assert DOMAIN_KEY in path.read_text(encoding="utf-8")


def test_версия_считается_и_без_файла_маршрута(методика: Path) -> None:
    """Маршрут обхода необязателен, и его отсутствие не должно ронять версию."""
    from src.domain.route import ROUTE_FILE

    _издано(методика, "imf 2026-09-01")
    (методика / ROUTE_FILE).unlink(missing_ok=True)

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version())


def test_имя_из_одних_разделителей_отвергается(методика: Path) -> None:
    """«- 2026-09-01» — это дата с мусором вместо имени, а не издание набора."""
    _издано(методика, "- 2026-09-01")

    with pytest.raises(ConfigError) as отказ:
        checklist_version()

    assert "имени набора" in str(отказ.value)
