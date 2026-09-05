"""Граница `language.load_rules`: сломанный файл правил — это отказ, а не тишина.

Правила разбора слов читаются один раз при импорте (`src/recognize/language.py`,
`RULES = load_rules()`): если что-то из двенадцати проверок ниже перестанет
падать, продукт стартует на половинчатых правилах — слова режутся, а колонка
или раздел карты не находятся никогда, — и заметно это станет только на демо.

`test_recognize_language.py` уже проверяет два случая отказа (отсутствующее
поле и позитивный сценарий третьего языка). Здесь — оставшиеся одиннадцать
проверок на границе `load_rules`/`_one`/`_words` плюс двенадцатая, отдельная:
`section_headings` с именем раздела, которого нет в правилах вовсе (опечатка в
коде, не в файле правил).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from src.recognize.errors import RecognizeConfigError
from src.recognize.language import COLUMN_WORDS, THRESHOLDS, load_rules, section_headings

#: Валидный язык — основа для случаев 5–11: каждый тест портит РОВНО одно поле
#: этой копии, поэтому падение видно как следствие порчи, а не кривой оснастки.
ВАЛИДНЫЙ_ЯЗЫК: dict[str, Any] = {
    "about": "оснастка теста: слова придуманы, языка такого в продукте нет",
    "stopwords": ["and"],
    "suffixes": ["ing"],
    "negations": ["not"],
    "column_words": {"dirt": ["stain"]},
    "sections": {
        THRESHOLDS: "## Thresholds",
        COLUMN_WORDS: "## Column words",
    },
}


def _записать(tmp_path: Path, язык: dict[str, Any]) -> Path:
    """Сохранить правила одного языка «xx» как `language_rules.json`."""
    файл = tmp_path / "language_rules.json"
    файл.write_text(json.dumps({"xx": язык}, ensure_ascii=False), encoding="utf-8")
    return файл


def test_отсутствующий_файл_это_отказ_а_не_голый_FileNotFoundError(tmp_path: Path) -> None:
    """Файл читается при импорте: пропавший путь обязан дать понятный отказ.

    Без перехвата `FileNotFoundError` ушёл бы наружу нетронутым — это отказ
    без объяснения, какой файл и почему, на самом старте продукта.
    """
    файл = tmp_path / "нет-такого-файла.json"

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "не прочитан" in str(отказ.value), str(отказ.value)


def test_содержимое_не_json_это_отказ(tmp_path: Path) -> None:
    """Битый JSON — не повод падать `json.JSONDecodeError` без контекста файла."""
    файл = tmp_path / "language_rules.json"
    файл.write_text("{{{", encoding="utf-8")

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "JSON" in str(отказ.value), str(отказ.value)


def test_пустой_объект_это_ни_одного_объявленного_языка(tmp_path: Path) -> None:
    """`{}` разбирается как валидный JSON, но продукту без единого языка нельзя."""
    файл = tmp_path / "language_rules.json"
    файл.write_text("{}", encoding="utf-8")

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "ни одного языка" in str(отказ.value), str(отказ.value)


def test_список_вместо_объекта_тоже_ни_одного_языка(tmp_path: Path) -> None:
    """Список — тоже валидный JSON, но `code -> правила` он не описывает.

    Без явной проверки типа код тихо получил бы `{str(i): ... for i, ...}` по
    индексам вместо кодов языка — тест ловит именно эту ловушку.
    """
    файл = tmp_path / "language_rules.json"
    файл.write_text("[]", encoding="utf-8")

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "ни одного языка" in str(отказ.value), str(отказ.value)


def test_пустой_about_это_отказ(tmp_path: Path) -> None:
    """`about` — не украшение: откуда взяты слова, тоже часть правил."""
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["about"] = ""
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "about" in str(отказ.value), str(отказ.value)


def test_стоп_слово_в_верхнем_регистре_никогда_бы_не_совпало(tmp_path: Path) -> None:
    """Сравнение слов идёт с уже понижённым текстом комментария и карты.

    Стоп-слово `"The"` не встретится в тексте, приведённом к нижнему регистру,
    ни разу — оно осталось бы значимым словом молча, без единого отказа.
    """
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["stopwords"] = ["The"]
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "нижнем регистре" in str(отказ.value), str(отказ.value)


def test_пустой_список_стоп_слов_это_отказ(tmp_path: Path) -> None:
    """Пустой список — не «слов пока нет», а язык без единого стоп-слова."""
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["stopwords"] = []
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "stopwords" in str(отказ.value), str(отказ.value)


def test_окончания_строкой_вместо_списка_это_отказ(tmp_path: Path) -> None:
    """Строка вместо списка молча разобралась бы по символам, а не по словам."""
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["suffixes"] = "ing"
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "suffixes" in str(отказ.value), str(отказ.value)


def test_пустой_словарь_колонок_это_отказ(tmp_path: Path) -> None:
    """Язык без словаря колонок — это язык, где колонка не выбирается никогда."""
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["column_words"] = {}
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "колонок" in str(отказ.value), str(отказ.value)


def test_объявлен_только_один_раздел_карты_это_отказ(tmp_path: Path) -> None:
    """Оба раздела обязательны: без порогов классов промпт остаётся без них."""
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["sections"] = {THRESHOLDS: "## Thresholds"}
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "раздел" in str(отказ.value), str(отказ.value)


def test_заголовок_раздела_без_решётки_это_отказ(tmp_path: Path) -> None:
    """Раздел карты узнаётся по `str.startswith("## ")` — без него не найдётся."""
    язык = copy.deepcopy(ВАЛИДНЫЙ_ЯЗЫК)
    язык["sections"] = {
        THRESHOLDS: "Thresholds без решётки",
        COLUMN_WORDS: "## Column words",
    }
    файл = _записать(tmp_path, язык)

    with pytest.raises(RecognizeConfigError) as отказ:
        load_rules(файл)

    assert "## " in str(отказ.value), str(отказ.value)


def test_неизвестное_имя_раздела_падает_а_не_возвращает_пустоту() -> None:
    """Имя раздела приходит из кода (`THRESHOLDS`/`COLUMN_WORDS`), не из файла.

    Опечатка в этом имени — ошибка кода, и она обязана падать сразу, а не
    молча вернуть пустой кортеж, который затем нигде ничего не найдёт.
    """
    with pytest.raises(RecognizeConfigError) as отказ:
        section_headings("такого раздела нет")

    assert "такого раздела нет" in str(отказ.value), str(отказ.value)
