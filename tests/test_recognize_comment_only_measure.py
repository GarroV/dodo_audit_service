"""T224 (#179): оснастка замера цены D081 проверяется вхолостую, без единого платного вызова.

Сам замер (`tools/comment_only_measure.py`) платный — 34 обращения к модели — и
ждёт решения владельца. Проверять при этом надо не арифметику попаданий (её
даст только живой прогон), а то, **понятен ли вывод оснастки без объяснений**:
человек запустит её один раз, прочитает отчёт и по нему примет решение.

Три случая, ради которых тесты и написаны, — все три были найдены прогоном
вхолостую с подставным ключом:

1. **Окружение не настроено.** Замер читает методику дважды (`hints_bot` — зоны,
   `run_case` — перечень пунктов), а `check_environment()` требует `STATE_DIR`,
   которого сам инструмент себе не подставляет. Раньше это выходило наружу
   трассировкой на два десятка строк.
2. **Все вызовы оборвались отказом.** Отчёт печатал «код верный 0 (0 %)» и
   «разница входных токенов +0. Это и есть цена кадра» — то есть ровно то же,
   что сказал бы настоящий замер, у которого кадр ничего не даёт. Отличить один
   прогон от другого можно было только по предпоследнему столбцу таблицы.
3. **Отказов часть.** Доли занижены, суммы токенов неполны — и это обязано быть
   сказано, а не оставлено читателю в уме.

Ни одного обращения к сети: модель подменяется на уровне `classify.ask_model`,
корпус синтетический и лежит во временной папке. Методика — синтетическая
(T141), поэтому боевые `data/` и `examples/` тесту не нужны вовсе и меток
`requires_*` здесь нет.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.recognize import classify as classify_module
from src.recognize.client import ModelAnswer
from src.recognize.errors import ModelUnavailable
from tools import comment_only_measure as com

#: Пункт синтетической методики с зоной и одним классом — им отвечает подменённая
#: модель. Код, а не формулировка: сущности связываются кодами (`CLAUDE.md`).
ITEM = "CLN05"
LEVEL = "D1"
ZONE = "hot_kitchen"

#: Расход, который подменённая модель объявляет за вызов. Числа разные для
#: запроса с кадром и без — иначе «цена кадра» вышла бы нулевой и строка отчёта
#: про неё ничего бы не стерегла.
USAGE_WITH_PHOTO = {"input": 1500, "output": 40}
USAGE_TEXT_ONLY = {"input": 400, "output": 40}


def _inspection(root: Path, notes: tuple[str, ...]) -> None:
    """Синтетическая выгрузка проверки в `<root>/examples/<имя>/` — вход загрузчика.

    Кадр настоящим изображением быть не обязан: оснастка читает его байты и
    отдаёт `classify`, а `classify` здесь подменён. Файл всё же создаётся —
    `_leg_a` читает его с диска безусловно, и подсунуть отсутствующий путь
    значило бы проверять не то.
    """
    folder = root / "examples" / "probe"
    (folder / "photos").mkdir(parents=True)
    (folder / "photos" / "p01.jpg").write_bytes(b"not-a-real-jpeg")
    findings = [
        {
            "n": number,
            "qid": ITEM,
            "level": LEVEL,
            "zone": ZONE,
            "photos": ["photos/p01.jpg"],
            "comment": "",
            "evidence": note,
        }
        for number, note in enumerate(notes, start=1)
    ]
    (folder / "inspection.json").write_text(
        json.dumps({"findings": findings, "info": {}, "meta": {}}, ensure_ascii=False),
        encoding="utf-8",
    )


def _answer(photo: bytes | None) -> ModelAnswer:
    return ModelAnswer(
        payload={
            "records": [
                {
                    "item": f"{ITEM}:{LEVEL}",
                    "zone": ZONE,
                    "wording": "ответ подменённой модели",
                    "reason": "подмена теста",
                    "confidence": 0.9,
                }
            ],
            "question": "",
        },
        usage=dict(USAGE_WITH_PHOTO if photo is not None else USAGE_TEXT_ONLY),
    )


def _substitute(monkeypatch: pytest.MonkeyPatch, failing: int) -> None:
    """Подменить модель: первые `failing` вызовов отказывают, остальные отвечают.

    Подменяется `classify.ask_model`, а не `client.ask_model`: `classify`
    затянул имя к себе импортом, и подмена в клиенте до него не дошла бы.
    Счётчик общий на процесс — тесты гоняют по одной записи или по две, и
    порядок вызовов внутри `run_case` жёсткий (сначала плечо A, потом B).
    """
    calls = {"n": 0}

    def fake(**kwargs: Any) -> ModelAnswer:
        calls["n"] += 1
        if calls["n"] <= failing:
            raise ModelUnavailable("модель подменена тестом: отказ")
        return _answer(kwargs["photo"])

    monkeypatch.setattr(classify_module, "ask_model", fake)


@pytest.fixture
def корпус(tmp_path: Path, domain_env: Path) -> Path:
    """Корень с одной синтетической проверкой из двух записей. Методика — своя."""
    _inspection(tmp_path, ("Печь: под лентой нагар", "Мебель участка: крошки на столах"))
    return tmp_path


def test_без_переменной_state_dir_внятный_отказ_а_не_трассировка(
    корпус: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Окружение не настроено — одна строка про недостающую переменную и код 2.

    До T224 отсюда вылетала трассировка `ConfigError` через `hints_bot`: инструмент
    сам подставляет себе только `AUDIT_DATA_DIR`, а методику без `STATE_DIR`
    прочитать нельзя. Человеку приходилось вычитывать из двадцати строк стека,
    что не хватает переменной окружения.
    """
    monkeypatch.delenv("STATE_DIR")

    rc = com.main(["--root", str(корпус)])

    out = capsys.readouterr().out
    assert rc == 2
    assert "Замерять не по чему" in out
    assert "STATE_DIR" in out
    assert "Traceback" not in out


def test_все_вызовы_с_отказом_замером_не_называются(
    корпус: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустой прогон обязан отличаться от замера, у которого кадр ничего не дал.

    Числа в обоих случаях одни и те же — нули и «+0», — поэтому различить их
    может только сказанное вслух и код возврата, а не таблица.
    """
    _substitute(monkeypatch, failing=4)

    rc = com.main(["--root", str(корпус)])

    out = capsys.readouterr().out
    assert rc == 1, "прогон без единого удавшегося вызова вышел нулём, то есть «всё прошло»"
    assert "ЗАМЕРА НЕ ПРОИЗОШЛО" in out
    assert "Ценой кадра это число НЕ является" in out
    assert "Это и есть цена кадра" not in out


def test_часть_отказов_названа_отдельно_от_полного_провала(
    корпус: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Один отказ из четырёх вызовов — доли занижены, и об этом сказано.

    Код возврата при этом нулевой: замер состоялся, просто неполный. Единица
    закреплена за случаем, когда не удался ни один вызов.
    """
    _substitute(monkeypatch, failing=1)

    rc = com.main(["--root", str(корпус)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "ЧАСТЬ ВЫЗОВОВ ОБОРВАЛАСЬ ОТКАЗОМ МОДЕЛИ" in out
    assert "ЗАМЕРА НЕ ПРОИЗОШЛО" not in out
    assert "Ценой кадра это число НЕ является" in out


def test_удавшийся_прогон_называет_цену_кадра_и_выходит_нулём(
    корпус: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Без отказов оговорок нет, а разница токенов названа ценой кадра.

    Это и есть то, ради чего замер существует (D081): сколько стоит кадр,
    отправленный вместе с комментарием. Плечо A шлёт кадр, плечо B никогда —
    значит, разница обязана быть положительной, а не нулевой.
    """
    _substitute(monkeypatch, failing=0)

    rc = com.main(["--root", str(корпус)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "ЗАМЕРА НЕ ПРОИЗОШЛО" not in out
    assert "ЧАСТЬ ВЫЗОВОВ ОБОРВАЛАСЬ" not in out
    ожидаемая = (USAGE_WITH_PHOTO["input"] - USAGE_TEXT_ONLY["input"]) * 2
    assert f"Разница входных токенов (A − B): +{ожидаемая}. Это и есть цена кадра" in out


def test_без_боевых_данных_код_возврата_2(
    domain_env: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустой корень — законный итог (D002), а не поломка инструмента."""
    rc = com.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 2
    assert "не поломка инструмента" in out


def test_json_замера_пишется_и_читается(
    корпус: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`--out` кладёт разбор по записям — им проверяют отчёт, когда он уже прочитан."""
    _substitute(monkeypatch, failing=0)
    out_path = tmp_path / "out" / "measure.json"

    assert com.main(["--root", str(корпус), "--out", str(out_path)]) == 0

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["records_total"] == 2
    assert len(payload["outcomes"]) == 2
    assert payload["totals"]["a"]["errors"] == 0
    assert payload["totals"]["a"]["usage"]["input"] == USAGE_WITH_PHOTO["input"] * 2
