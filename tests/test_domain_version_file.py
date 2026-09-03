"""T026: разбор `checklist_version.txt` (D050) — формы строки издания и отказы.

Формулировку «имя набора плюс дата» управляющая компания кладёт в файл руками,
поэтому разбор обязан прощать бытовые вольности (запятая вместо пробела,
пробелы по краям, комментарии) и одинаково твёрдо отказывать там, где издание
не читается однозначно — молчаливый откат на `local-…` спрятал бы чужую или
испорченную методику под видом неизданной.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import requires_data

from src.domain import check_environment, checklist_version
from src.domain.errors import ConfigError
from src.domain.version import EXAMPLE, VERSION_FILE

pytestmark = requires_data

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


# --- Принимается: версия собирается ------------------------------------------


def test_запятая_разделяет_имя_и_дату(методика: Path) -> None:
    """Запятая между именем и датой — законный разделитель, а не часть имени."""
    _издано(методика, "imf, 2026-09-01")

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version())


def test_подчёркивание_разделяет_имя_и_дату(методика: Path) -> None:
    """Подчёркивание — тоже законный разделитель между именем и датой."""
    _издано(методика, "imf_2026-09-01")

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version())


def test_пробелы_по_краям_строки_не_мешают(методика: Path) -> None:
    """Пробелы по краям строки — не часть имени и разбору не мешают."""
    _издано(методика, "   imf 2026-09-01   ")

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version())


def test_пробел_внутри_имени_становится_дефисом_в_идентификаторе(методика: Path) -> None:
    """Идентификатор — один токен: пробел внутри имени превращается в дефис."""
    _издано(методика, "IMF sanitation 2026-09-01")

    assert re.fullmatch(rf"IMF-sanitation-2026-09-01-{FINGERPRINT}", checklist_version())


def test_имя_кириллицей_доезжает_как_есть(методика: Path) -> None:
    """Имя набора не транслитерируется и не переводится — доезжает как есть."""
    _издано(методика, "методика-УК 2026-09-01")

    assert re.fullmatch(rf"методика-УК-2026-09-01-{FINGERPRINT}", checklist_version())


def test_пустая_строка_и_комментарий_перед_изданием_пропускаются(методика: Path) -> None:
    """Пустая строка и `#`-комментарий перед изданием не мешают его прочитать."""
    (методика / VERSION_FILE).write_text(
        "\n# комментарий управляющей компании\nimf 2026-09-01\n", encoding="utf-8"
    )

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version())


def test_из_двух_изданий_берётся_первое(методика: Path) -> None:
    """Второе издание в файле не должно влиять на версию — берётся первое."""
    (методика / VERSION_FILE).write_text("imf 2026-09-01\nчерновик 2025-01-01\n", encoding="utf-8")

    assert re.fullmatch(rf"imf-2026-09-01-{FINGERPRINT}", checklist_version())


# --- Отвергается: ConfigError, а не молчаливый откат на local-… --------------


def test_пустой_файл_версии_отвергается(методика: Path) -> None:
    """Пустой файл — не законное «изданий не было», а порча: отказ, а не `local-…`."""
    (методика / VERSION_FILE).write_text("", encoding="utf-8")

    with pytest.raises(ConfigError):
        checklist_version()


def test_файл_только_из_комментариев_отвергается(методика: Path) -> None:
    """Файл из одних комментариев — содержательной строки нет, это тоже отказ."""
    (методика / VERSION_FILE).write_text("# просто комментарий\n# ещё один\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        checklist_version()


def test_дата_без_имени_отвергается(методика: Path) -> None:
    """Дата без имени — идентификатор не собрать, УК забыла указать набор."""
    _издано(методика, "2026-09-01")

    with pytest.raises(ConfigError):
        checklist_version()


def test_дата_не_в_конце_строки_отвергается(методика: Path) -> None:
    """Дата обязана быть в конце строки — иначе её нельзя отделить от имени."""
    _издано(методика, "2026-09-01 imf")

    with pytest.raises(ConfigError):
        checklist_version()


def test_несуществующая_дата_в_календаре_отвергается(методика: Path) -> None:
    """13-й месяц синтаксически похож на дату, но в календаре его нет."""
    _издано(методика, "imf 2026-13-40")

    with pytest.raises(ConfigError):
        checklist_version()


def test_дата_не_в_формате_гггг_мм_дд_отвергается(методика: Path) -> None:
    """Дата в формате ДД.ММ.ГГГГ не распознаётся — нужен только ISO ГГГГ-ММ-ДД."""
    _издано(методика, "imf 01.09.2026")

    with pytest.raises(ConfigError):
        checklist_version()


def test_год_двумя_знаками_отвергается(методика: Path) -> None:
    """Год из двух знаков — не ГГГГ-ММ-ДД, разбор обязан отказать, а не догадываться."""
    _издано(методика, "imf 26-09-01")

    with pytest.raises(ConfigError):
        checklist_version()


# --- Форма отказа: чинибельна и одинакова в обеих дверях ---------------------


def test_отказ_называет_файл_и_показывает_пример(методика: Path) -> None:
    """Текст отказа обязан называть файл и показывать пример правильной формы."""
    _издано(методика, "бессмыслица без даты")

    with pytest.raises(ConfigError) as через_checklist_version:
        checklist_version()
    with pytest.raises(ConfigError) as через_check_environment:
        check_environment()

    for отказ in (через_checklist_version, через_check_environment):
        text = str(отказ.value)
        assert VERSION_FILE in text, "в отказе нет имени файла — непонятно, что чинить"
        assert EXAMPLE in text, "в отказе нет примера правильной формы"


def test_непригодный_файл_отказывает_в_обеих_дверях(методика: Path) -> None:
    """`check_environment` ловит порченый файл версии сам, не только `checklist_version`."""
    _издано(методика, "imf 2026-13-40")

    with pytest.raises(ConfigError):
        check_environment()
    with pytest.raises(ConfigError):
        checklist_version()
