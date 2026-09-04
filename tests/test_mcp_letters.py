"""T171: письмо партнёру по уже записанной проверке.

Письмо — это то, что человек отправляет партнёру руками (Q010, D035), и до
этой задачи достать его из системы было нечем: бот отдаёт письмо один раз, в
момент завершения проверки, и больше нигде оно не лежит.

Собирает письмо движок, а не этот блок. Здесь проверяется другое — то, из-за
чего пересборка вообще опасна:

**Пересборка не имеет права пересчитать оценку.** Движок считает по той
методике, которая лежит в `CHECKLIST_DIR` в момент вызова, а методика с тех
пор могла измениться. Поэтому методика прибивается к ТОЙ версии, которой
помечена проверка, а посчитанное движком сверяется с записанным — расхождение
это отказ, а не письмо с другой буквой (D033, D049).

**Недостающая шапка названа словами.** Слой чтения сегодня не отдаёт ни
аудитора, ни города, и движок подписывает такое письмо прочерком, выходя с
нулевым кодом. Молча отдать его нельзя: оно выглядит готовым к отправке.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest
from mcp_checklist_harness import build_methodology

from src.db.models import FindingRow, InspectionDetail, InspectionRow
from src.mcp import letters
from src.mcp.errors import ToolError

ВЕРСИЯ = "harness-2026-09-04-abcdef012345"

#: Другая версия — та, которой у нас в хранилище нет вовсе.
ЧУЖАЯ_ВЕРСИЯ = "harness-2026-01-01-000000000000"


def _строка(*, version: str = ВЕРСИЯ, pct: float = 99.5, grade: str = "A") -> InspectionRow:
    return InspectionRow(
        id="11111111-1111-1111-1111-111111111111",
        tenant_code="укашка",
        unit_name="Белград-1",
        chat_id=744230399,
        kind="Плановая",
        inspection_date=date(2026, 8, 19),
        report_lang="ru",
        checklist_version=version,
        pct=pct,
        grade=grade,
        findings_count=1,
        pushed_at="2026-08-19T18:00:00+02:00",
    )


def _находка(*, code: str = "CLN01", level: str = "D1") -> FindingRow:
    return FindingRow(
        id="22222222-2222-2222-2222-222222222222",
        inspection_id="11111111-1111-1111-1111-111111111111",
        unit_name="Белград-1",
        inspection_date=date(2026, 8, 19),
        n=1,
        code=code,
        level=level,
        zone="fridge",
        zone_unusual=False,
        source="comment",
        lang="ru",
        text="Пол в холодильнике в разводах",
        comment="",
    )


def _проверка(**kwargs: object) -> InspectionDetail:
    строка = _строка(**kwargs)  # type: ignore[arg-type]
    return InspectionDetail(
        inspection=строка,
        deductions=0.5,
        counts={"D1": 1},
        by_zone={},
        findings=(_находка(),),
    )


@pytest.fixture
def хранилище(tmp_path: Path) -> letters.Papers:
    """Хранилище со снимком ровно той версии, которой помечена проверка."""
    build_methodology(tmp_path / "store" / "versions" / ВЕРСИЯ)
    build_methodology(tmp_path / "live")
    return letters.Papers(live=tmp_path / "live", store=tmp_path / "store")


def _md5(каталог: Path) -> dict[str, str]:
    return {
        str(файл.relative_to(каталог)): hashlib.md5(файл.read_bytes()).hexdigest()  # noqa: S324
        for файл in sorted(каталог.rglob("*"))
        if файл.is_file()
    }


# --- письмо собирается и несёт записанную оценку ------------------------------


def test_письмо_собрано_движком_и_несёт_записанную_букву(хранилище: letters.Papers) -> None:
    """Главное: наружу уходит текст письма, а оценка в нём — записанная."""
    ответ = letters.build(_проверка(), lang=None, papers=хранилище)

    assert ответ["score_verified"] is True
    письмо = ответ["letter"]
    assert isinstance(письмо, str)
    assert "99.5%" in письмо
    assert "Белград-1" in письмо


def test_язык_письма_задаётся_аргументом(хранилище: letters.Papers) -> None:
    """«Собрать заново на другом языке» — это аргумент, а не другая проверка."""
    по_русски = letters.build(_проверка(), lang="ru", papers=хранилище)["letter"]
    по_английски = letters.build(_проверка(), lang="en", papers=хранилище)["letter"]

    assert isinstance(по_русски, str) and isinstance(по_английски, str)
    assert по_русски != по_английски
    assert "Здравствуйте" in по_русски
    assert "Hello" in по_английски


def test_язык_по_умолчанию_тот_на_котором_отчёт_записан(хранилище: letters.Papers) -> None:
    ответ = letters.build(_проверка(), lang=None, papers=хранилище)
    assert ответ["lang"] == "ru"


def test_неизвестный_язык_отказ_а_не_молчаливый_русский(хранилище: letters.Papers) -> None:
    """Движок на незнакомый язык молча откатывается на русский: `if lang not in
    T: lang = "ru"`. Спрашивающий получил бы письмо не на том языке и не узнал
    бы об этом — поэтому язык проверяется до вызова движка."""
    with pytest.raises(ToolError, match="de"):
        letters.build(_проверка(), lang="de", papers=хранилище)


# --- методика прибита к версии проверки ---------------------------------------


def test_методика_берётся_из_снимка_версии_этой_проверки(хранилище: letters.Papers) -> None:
    ответ = letters.build(_проверка(), lang=None, papers=хранилище)
    assert ответ["checklist_version"] == ВЕРСИЯ
    assert ответ["methodology"] == letters.FROM_SNAPSHOT


def test_версии_нет_в_хранилище_но_боевая_ею_и_является(tmp_path: Path) -> None:
    """Хранилище может быть не заведено вовсе: до T098 весь MCP был чтением.
    Тогда годится боевая методика — но только если она И ЕСТЬ та самая версия."""
    живая = build_methodology(tmp_path / "live")
    версия = letters.version_of(живая)
    ответ = letters.build(
        _проверка(version=версия), lang=None, papers=letters.Papers(live=живая, store=None)
    )
    assert ответ["methodology"] == letters.FROM_LIVE
    assert ответ["score_verified"] is True


def test_версии_проверки_нет_нигде_отказ_а_не_пересчёт_по_сегодняшней(
    хранилище: letters.Papers,
) -> None:
    """Самый опасный случай: методика с тех пор изменилась. Собрать письмо по
    сегодняшней означало бы отдать партнёру другую букву под старой датой."""
    with pytest.raises(ToolError, match=ЧУЖАЯ_ВЕРСИЯ):
        letters.build(_проверка(version=ЧУЖАЯ_ВЕРСИЯ), lang=None, papers=хранилище)


def test_имя_версии_не_выпускает_за_хранилище(tmp_path: Path) -> None:
    """`checklist_version` приезжает из базы и подставляется в путь каталога
    снимка. Проверяется НАСТОЯЩИЙ побег, а не «такого каталога нет»: за `..`
    кладётся работающая методика, по которой оценка сходится с записанной, —
    то есть без сторожа письмо собралось бы по ней и вернулось бы успехом.

    Первый заход этого теста был зелёным по неверной причине: `../../../etc`
    не каталог методики, и отказ приходил ниже, от сверки версий. Снятие
    сторожа такой тест не краснил (та же ловушка, что поймана на T098)."""
    build_methodology(tmp_path / "escape")
    # Хранилище настоящее: без существующего `versions/` подъём по `..` не
    # состоялся бы на уровне файловой системы, и тест снова был бы зелёным
    # не потому, что сторож работает.
    build_methodology(tmp_path / "store" / "versions" / ВЕРСИЯ)
    хранилище = letters.Papers(live=None, store=tmp_path / "store")
    побег = "../../escape"
    assert (tmp_path / "store" / letters.VERSIONS_DIR / побег).is_dir(), (
        "побег обязан вести в методику"
    )

    with pytest.raises(ToolError):
        letters.build(_проверка(version=побег), lang=None, papers=хранилище)


def test_методики_нет_вовсе_отказ_называет_переменную(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="AUDIT_DATA_DIR"):
        letters.build(_проверка(), lang=None, papers=letters.Papers(live=None, store=None))


# --- оценка не пересчитывается ------------------------------------------------


def test_расхождение_с_записанной_оценкой_отказ_а_не_письмо(tmp_path: Path) -> None:
    """Снимок может лежать под нужным именем и быть не тем: имя каталога это
    ещё не доказательство. Доказательство — совпадение посчитанного движком с
    записанным в проверке."""
    build_methodology(tmp_path / "store" / "versions" / ВЕРСИЯ)
    papers = letters.Papers(live=None, store=tmp_path / "store")

    # Записано 97%, а по этому снимку движок считает 99.5%: снимок не тот,
    # хотя каталог назван правильно.
    with pytest.raises(ToolError, match="97"):
        letters.build(_проверка(pct=97.0, grade="A"), lang=None, papers=papers)


def test_расхождение_буквы_тоже_отказ(хранилище: letters.Papers) -> None:
    with pytest.raises(ToolError, match="C"):
        letters.build(_проверка(grade="C"), lang=None, papers=хранилище)


def test_записанная_оценка_отдаётся_как_лежит(хранилище: letters.Papers) -> None:
    """Наружу уходит записанное число, а не то, что вернул движок при сверке."""
    ответ = letters.build(_проверка(), lang=None, papers=хранилище)
    assert ответ["pct"] == 99.5
    assert ответ["grade"] == "A"


# --- боевая методика не трогается ---------------------------------------------


def test_боевая_методика_не_меняется_ни_одним_байтом(хранилище: letters.Papers) -> None:
    assert хранилище.live is not None
    до = _md5(хранилище.live)
    letters.build(_проверка(), lang=None, papers=хранилище)
    assert _md5(хранилище.live) == до


# --- недостающая шапка названа ------------------------------------------------


def test_недостающая_шапка_названа_и_письмо_не_объявлено_готовым(
    хранилище: letters.Papers,
) -> None:
    """Слой чтения не отдаёт аудитора, город, партнёра и контакт: движок
    подписывает такое письмо прочерком и выходит с нулевым кодом."""
    ответ = letters.build(_проверка(), lang=None, papers=хранилище)

    не_восстановлено = ответ["not_restored"]
    assert isinstance(не_восстановлено, list)
    assert set(не_восстановлено) == set(letters.COVER_FIELDS)
    assert ответ["ready_to_send"] is False
    status = ответ["status"]
    assert isinstance(status, str) and "auditor" in status


def test_шапка_подхватывается_сама_как_только_слой_чтения_её_отдаст(
    хранилище: letters.Papers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перечень недостающего выводится из строки чтения, а не переписан
    списком: строка, у которой поля появились, перестаёт быть неполной сама."""
    проверка = _проверка()

    class _Полная:
        def __getattr__(self, имя: str) -> object:
            if имя in letters.COVER_FIELDS:
                return "Василий Гарро" if имя == "auditor" else "Белград"
            return getattr(проверка.inspection, имя)

    полная = InspectionDetail(
        inspection=_Полная(),  # type: ignore[arg-type]
        deductions=проверка.deductions,
        counts=проверка.counts,
        by_zone=проверка.by_zone,
        findings=проверка.findings,
    )
    ответ = letters.build(полная, lang=None, papers=хранилище)

    assert ответ["not_restored"] == []
    assert ответ["ready_to_send"] is True
    письмо = ответ["letter"]
    assert isinstance(письмо, str) and "Василий Гарро" in письмо


# --- состояние проверки не пишется никуда, кроме временного каталога ----------


def test_проверка_восстанавливается_из_записанного_а_не_из_состояния_чата(
    хранилище: letters.Papers, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Файла проверки в `STATE_DIR` давно нет: чат ведёт следующую. Письмо
    собирается из того, что записано в базе, и переменную состояния не читает."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "нет-такого-каталога"))
    ответ = letters.build(_проверка(), lang=None, papers=хранилище)
    assert ответ["score_verified"] is True


def test_состояние_собирается_из_находок_проверки(хранилище: letters.Papers) -> None:
    """Формулировка аудитора уезжает в письмо как записана — её никто не
    переводит: перевод чужих слов в отчёте партнёру был бы выдумкой."""
    ответ = letters.build(_проверка(), lang="en", papers=хранилище)
    письмо = ответ["letter"]
    assert isinstance(письмо, str)
    assert "Пол в холодильнике в разводах" in письмо


def test_состояние_проверки_имеет_форму_которую_читает_движок(
    хранилище: letters.Papers, tmp_path: Path
) -> None:
    """Форма файла состояния — договор с движком, и она проверяется явно:
    молча разошедшись, она дала бы письмо без находок вовсе."""
    состояние = json.loads(letters.state_json(_проверка(), lang="ru"))
    assert set(состояние) == {"meta", "findings", "info"}
    assert состояние["meta"]["unit"] == "Белград-1"
    assert состояние["meta"]["date"] == "2026-08-19"
    assert состояние["findings"][0]["qid"] == "CLN01"
    assert состояние["findings"][0]["evidence"] == "Пол в холодильнике в разводах"
    assert состояние["findings"][0]["photos"] == []


# --- отказы приходят отказами, а не пустым письмом ----------------------------


def test_каталога_методики_нет_отказ_называет_переменную_а_путь_уходит_в_лог(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Абсолютных путей в ответе быть не должно (T120), а чинить человеку надо."""
    нет_такого = tmp_path / "нет-такого-каталога"
    with pytest.raises(ToolError) as отказ:
        letters.build(_проверка(), lang=None, papers=letters.Papers(live=нет_такого, store=None))

    assert "AUDIT_DATA_DIR" in str(отказ.value)
    assert str(нет_такого) not in str(отказ.value)
    assert str(нет_такого) in capsys.readouterr().err


def test_сломанная_методика_отказ_словами_движка_без_путей(tmp_path: Path) -> None:
    """Движок падает без общего перехвата, и трейсбек печатает пути к файлам
    продукта. В ответ уходит причина, но не устройство машины."""
    снимок = build_methodology(tmp_path / "store" / "versions" / ВЕРСИЯ)
    (снимок / "scoring.json").write_text('{"start_pct": 100.0}', encoding="utf-8")

    with pytest.raises(ToolError) as отказ:
        letters.build(
            _проверка(), lang=None, papers=letters.Papers(live=None, store=tmp_path / "store")
        )

    текст = str(отказ.value)
    assert "письмо не" in текст
    assert str(снимок) not in текст


def test_движок_ответил_на_сверку_мусором_отказ_а_не_письмо(
    хранилище: letters.Papers, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(letters, "_run", lambda *a, **k: (0, "не json", ""))
    with pytest.raises(ToolError, match="JSON"):
        letters.build(_проверка(), lang=None, papers=хранилище)


def test_сборщик_письма_отказал_отказ_доезжает_до_спрашивающего(
    хранилище: letters.Papers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ сборки письма не имеет права выглядеть как письмо."""
    настоящий = letters._run

    def _подмена(script: Path, args: list[str], **kwargs: object) -> tuple[int, str, str]:
        if script == letters.REPORT_SCRIPT:
            return 1, "", "Письмо не собрано: пустой план действий"
        return настоящий(script, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(letters, "_run", _подмена)
    with pytest.raises(ToolError, match="план действий"):
        letters.build(_проверка(), lang=None, papers=хранилище)


def test_пустое_письмо_с_нулевым_кодом_тоже_отказ(
    хранилище: letters.Papers, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Движок отчитался об успехе и вернул пустоту — отправлять партнёру
    нечего, и молча отдать пустую строку нельзя."""
    настоящий = letters._run

    def _подмена(script: Path, args: list[str], **kwargs: object) -> tuple[int, str, str]:
        if script == letters.REPORT_SCRIPT:
            return 0, "   \n", ""
        return настоящий(script, args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(letters, "_run", _подмена)
    with pytest.raises(ToolError, match="пуст"):
        letters.build(_проверка(), lang=None, papers=хранилище)
