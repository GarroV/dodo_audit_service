"""T113, T125: тесты замера доли срабатываний быстрого пути.

Замер (`tools/fastpath_measure.py`) — критерий пользы карты слов
`data/photo-cues.md`: пополнение карты бессмысленно, если быстрый путь после
него не срабатывает чаще или начинает срабатывать неверно.

Главное, что здесь проверяется, — что замер меряет ТО, ЧТО ПРОИСХОДИТ НА
ТОЧКЕ. До T125 (задача #100) он звал `fast_path` с зоной из эталонной записи,
то есть из уже известного правильного ответа, и его 18% к живому боту
отношения не имели. Бот берёт зону из слов комментария (`src/bot/zones.py`),
а память о прошлой записи (D048) — только догадка на случай, когда о зоне не
сказано ничего.

Остальное: подсчёт (`measure`) на синтетике с заранее известным исходом; коды
возврата CLI (1 — есть неверное срабатывание, 2 — боевых данных нет); шапка
отчёта с версией карты (D066); и то, что живой замер на боевых записях
остаётся зелёным, — это регрессионный якорь.

**Второй блок теста (T195, задача #160) — про второй раздел отчёта**, замер
защиты от отрицания. Корпус там строится из самой карты на ходу, поэтому и
тесты здесь идут по синтетической методике (`domain_env`/`data_copy`), а не
по боевой: боевая лежит вне git и её правит управляющая компания (D002).
Главное, что проверяется, — что «пропуск» в замере это буквально «`fast_path`
всё равно сработал», а не рассогласование кода. Строка со словом, повторённым
в ней дважды («Мебель и мебель участка»), давала такой пропуск на одном из
двух вхождений; **задача T197 его закрыла**, и тест теперь стережёт обратное —
что оба вхождения дают отказ, — потому что пропуск сюда вернётся ровно тем же
местом, каким появился.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ROOT, requires_data, requires_examples

from src.recognize.cues import CUES_FILE, load_cues
from src.recognize.fastpath import NO_COLUMN, NO_ZONE, WRONG_ZONE
from tools import fastpath_measure as fpm
from tools.fastpath_measure import FROM_MEMORY, FROM_NOWHERE, FROM_WORDS, Mode, Record

#: «Печь» — строка карты, произнесённая целиком, «нагар» выбирает колонку
#: «Грязь». Зоны в этих словах нет: их у бота придётся брать из памяти.
OVEN = "Печь: под лентой нагар"
#: Те же слова, но зону аудитор назвал сам.
OVEN_WITH_ZONE = "Тепловой участок: печь, под лентой нагар"
#: Зона названа («в зале»), строка карты произнесена, колонка выбрана «крошками».
FURNITURE = "Мебель участка в зале: крошки на столах"


def test_замер_не_подставляет_эталонную_зону_которой_у_бота_нет(domain_env: Path) -> None:
    """T125: «Печь: под лентой нагар» зоны не называет — у бота зоны нет, и пункта тоже.

    Дефект #100: замер звал `fast_path` с зоной ИЗ ЭТАЛОННОЙ ЗАПИСИ, то есть из
    уже известного правильного ответа. Живой бот берёт зону только из слов
    аудитора (`src/bot/zones.py`) и памяти о прошлой записи; на первой записи
    проверки памяти ещё нет — значит, зоны нет вовсе и быстрый путь обязан
    отказать, а не показывать пункт.
    """
    records = (Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="synthetic"),)

    (outcome,) = fpm.measure(records)

    assert outcome.fired is None
    assert outcome.reason == NO_ZONE
    assert outcome.hint.zone is None
    assert outcome.hint.source == FROM_NOWHERE


def test_память_проверки_может_подставить_чужую_зону(domain_env: Path) -> None:
    """Память — догадка (D048), и догадка бывает неверной; замер обязан это показывать.

    Первая запись сделана в зале, вторая — про печь, но зону аудитор не назвал.
    Бот подставит зал, и `CLN05` к залу не применим: законный отказ. Замер с
    эталонной зоной этого не видел вовсе — он подставлял тепловой участок и
    рапортовал срабатывание, которого на точке не будет.
    """
    records = (
        Record(code="CLN06", zone="dining", note=FURNITURE, source="synthetic"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="synthetic"),
    )

    first, second = fpm.measure(records)

    assert first.fired == "CLN06"
    assert first.hint.source == FROM_WORDS
    assert second.fired is None
    assert second.reason == WRONG_ZONE
    assert second.hint.zone == "dining"
    assert second.hint.source == FROM_MEMORY


def test_слова_комментария_сильнее_памяти(domain_env: Path) -> None:
    """Порядок бота: `spoken or memory`, а не наоборот (T124)."""
    records = (
        Record(code="CLN06", zone="dining", note=FURNITURE, source="synthetic"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic"),
    )

    _, second = fpm.measure(records)

    assert second.hint.zone == "hot_kitchen"
    assert second.hint.source == FROM_WORDS
    assert second.fired == "CLN05"
    assert second.correct is True


def test_память_не_переходит_из_одной_проверки_в_другую(domain_env: Path) -> None:
    """У каждой проверки свой чат и свои заметки: зона предыдущей сюда не течёт."""
    records = (
        Record(code="CLN06", zone="dining", note=FURNITURE, source="belgrade-1"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="belgrade-2"),
    )

    _, second = fpm.measure(records)

    assert second.hint.source == FROM_NOWHERE
    assert second.reason == NO_ZONE


def test_эталонная_зона_считается_отдельно_как_верхняя_граница(domain_env: Path) -> None:
    """Прежнее число не выброшено, но подписано честно и стоит рядом с боевым.

    Те же две записи: с эталонной зоной «печь» срабатывает, «панель печи»
    отказывается по колонке — а как зовёт бот, не срабатывает ни одна.
    """
    records = (
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="synthetic"),
        Record(
            code="INF09",
            zone="hot_kitchen",
            note="Панель печи: температура 277 °C",
            source="synthetic",
        ),
    )

    fired, rejected = fpm.measure(records, fpm.hints_reference(records))

    assert fired.fired == "CLN05"
    assert fired.correct is True
    assert fired.reason == ""
    assert rejected.fired is None
    assert rejected.correct is None
    assert rejected.reason == NO_COLUMN
    assert fpm.measure(records) == fpm.measure(records, fpm.hints_bot(records))
    assert [o.fired for o in fpm.measure(records)] == [None, None]


def test_доля_и_счёт_неверных_считаются_по_способу(domain_env: Path) -> None:
    """`Mode` отвечает за арифметику отчёта: срабатывания, доля, неверные."""
    records = (
        Record(code="CLN06", zone="dining", note=FURNITURE, source="synthetic"),
        Record(code="TEH05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic"),
        Record(code="CLN05", zone="hot_kitchen", note=OVEN, source="belgrade-2"),
    )

    mode = Mode("проба", fpm.measure(records))

    assert len(mode.fired) == 2
    assert mode.share == pytest.approx(200 / 3)
    # `TEH05` — поломка печи, а слова говорят про нагар: пункт не тот.
    assert [o.record.code for o in mode.wrong] == ["TEH05"]


def test_неверное_срабатывание_даёт_код_возврата_1(
    domain_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Неверное срабатывание опаснее отсутствия: аудитор подтверждает пункт нажатием."""
    record = Record(code="TEH05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic")
    monkeypatch.setattr(fpm, "load_records", lambda _root: (record,))

    rc = fpm.main(["--root", str(tmp_path)])

    assert rc == 1


def test_без_боевых_данных_код_возврата_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустой корень — законный итог (D002: примеры вне git), а не поломка инструмента."""
    rc = fpm.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 2
    assert "нечего" in out
    assert "не поломка" in out


def test_отчёт_несёт_дату_и_отпечаток_карты(domain_env: Path) -> None:
    """Число замера без версии карты через неделю нечем проверить (D066)."""
    records = (Record(code="CLN06", zone="dining", note=FURNITURE, source="synthetic"),)

    text = fpm.render(fpm.modes(records))

    assert fpm.fingerprint() in text
    assert "md5 " in text
    assert "ВЕРХНЯЯ ГРАНИЦА" in text
    assert "Как зовёт бот" in text


@requires_examples
def test_load_records_поднимает_боевые_записи() -> None:
    """Записи поднимаются из боевых проверок `examples/` — потому и метка."""
    records = fpm.load_records(ROOT)

    assert len(records) == 17
    for record in records:
        assert record.code
        assert record.zone
        assert record.note


@requires_data
@requires_examples
def test_живой_замер_на_боевых_данных_зелёный(live_data_env: Path) -> None:
    """Ни одного неверного срабатывания на боевых данных — регрессионный якорь T113.

    **Данных нужно двое, поэтому и меток две (T212, задача #180).** Раньше стояла
    одна `requires_data`, и на копии без `examples/` тест падал: `fpm.main`
    печатал «боевых данных нет — это не поломка инструмента» и отдавал код 2, а
    `assert rc == 0` этого не переживал. Соседний
    `test_load_records_поднимает_боевые_записи` в том же положении спокойно
    пропускался — одно и то же отсутствие данных давало в наборе два разных
    исхода, и один из них выглядел поломкой продукта. Две метки — тот же приём,
    которым живёт `tests/test_engine_regress.py`.

    Ослаблять само утверждение до `rc in (0, 2)` было нельзя: якорь стал бы
    зелёным и на копии без эталонов, не проверяя ничего, — ровно та беда, из
    которой вырос баннер `pytest_terminal_summary` (задача #175). Пропуск в нём
    виден и посчитан, зелёная пустота — нет.

    Этому тесту нужна именно боевая методика (`data/`), а не синтетическая
    (T141/T146): он гоняет `fast_path` по боевым записям
    `examples/*/inspection.json`, и коды с зонами в них — боевые. На
    синтетической методике (`tests/methodology`) те же записи не найдут ни
    одного знакомого пункта — коды, формулировки и названия зон там выдуманы
    и с боевыми записями не совпадают, поэтому регрессионным якорем T113 тут
    служить не может.
    """
    rc = fpm.main(["--root", str(ROOT)])

    assert rc == 0


# --- T195 (#160): второй раздел отчёта — замер защиты от отрицания ---------


def test_раздел_печатается_с_обеими_таблицами(domain_env: Path) -> None:
    """Раздел на месте: заголовок, обе таблицы, все четыре вида, строка про перестраховку.

    Подстроки, а не одна длинная строка целиком: заголовок первой таблицы
    длиннее 100 знаков и в исходнике теста сам разбит форматтером на две
    склеенные строковые константы — сравнивать с ним побайтово значило бы
    зависеть от того, как именно `ruff format` перенёс строку сегодня.
    """
    text = fpm.render_negation_section(load_cues())

    assert "Замер защиты от отрицания (T195)" in text
    assert "Строк с утвердительным срабатыванием" in text
    assert "Отрицаний всего" in text
    assert "Доля пойманных" in text
    assert "| Вид отрицания | Всего | Пропущено |" in text
    for kind in (
        fpm.NEGATION_BEFORE,
        fpm.NEGATION_WITHOUT,
        fpm.NEGATION_AFTER,
        fpm.NEGATION_THROUGH_FUNCTION_WORD,
    ):
        assert f"| {kind} |" in text
    assert "Перестраховка: потеряно" in text


@pytest.fixture
def карта_с_повтором_основы(
    data_copy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Копия синтетической карты с дописанной строкой, где основа повторена дважды.

    «Мебель и мебель участка» — слово «мебель» встречается в строке дважды.
    До T197 отрицание ОДНОГО вхождения срабатывания не снимало: второе
    оставалось сказанным утвердительно, и покрытие строки карты (`_covered`,
    `src/recognize/fastpath.py`) выполнялось. Замер назвал это числом — 8
    случаев из 412 на боевой карте, — после чего дыру закрыли. Дописываем в
    копию: боевую карту сюда тянуть нельзя (D002), а без такой строки набор
    перестал бы стеречь возврат ровно того дефекта, ради которого фикстура и
    заведена.
    """
    карта = data_copy / CUES_FILE
    карта.write_text(
        карта.read_text(encoding="utf-8") + "\n| Мебель и мебель участка | CLN06 |\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    return data_copy


def test_повтор_основы_замер_больше_не_считает_пропуском(
    карта_с_повтором_основы: Path,
) -> None:
    """T197: повтор основы был дырой на 8 случаев из 412 — и её закрыли.

    Строка «Мебель и мебель участка» несёт основу «мебель» дважды. До T197
    отрицание ПЕРВОГО вхождения срабатывания не снимало: второе оставалось
    сказанным утвердительно, покрытие строки карты выполнялось (`_covered`,
    `src/recognize/fastpath.py`), и замер честно называл это пропуском. Теперь
    основа, сказанная и отрицаемая разом, не считается ни сказанной, ни
    отрицаемой, и пропуска здесь больше нет.

    Отрицание уникального слова строки («участка») правило ловило и раньше —
    проверяется рядом, чтобы два случая не слились в одно «всё поймано».
    """
    карта_текст = (карта_с_повтором_основы / CUES_FILE).read_text(encoding="utf-8")
    assert "Мебель и мебель участка" in карта_текст, (
        "строка с повтором основы не попала в саму копию карты — сценарий теста не тот"
    )

    (cue,) = [c for c in load_cues() if c.phrase == "Мебель и мебель участка"]

    base = fpm.find_affirmative_base(cue)
    assert base is not None, "утвердительная база не нашлась — сценарий теста не тот"
    assert base.code == "CLN06"

    outcomes = {o.note: o for o in fpm.negation_outcomes([base])}

    повтор_основы = outcomes["не Мебель и мебель участка"]
    assert повтор_основы.kind == fpm.NEGATION_BEFORE
    assert повтор_основы.missed is False, "повтор основы снова пропуск — дыра T197 вернулась"

    уникальное_слово = outcomes["Мебель и мебель не участка"]
    assert уникальное_слово.kind == fpm.NEGATION_BEFORE
    assert уникальное_слово.missed is False, "уникальное слово строки правило обязано заметить"


def test_пустая_карта_даёт_одну_строку_без_падения() -> None:
    """`render_negation_section(())` — законный итог (D068), а не повод падать.

    Пустой корпус получен без обращения к окружению вовсе: функция принимает
    уже прочитанные строки и сама не трогает `AUDIT_DATA_DIR`, поэтому тест не
    нуждается ни в `domain_env`, ни в какой-либо иной фикстуре методики.
    """
    assert fpm.render_negation_section(()) == "карты нет — отрицание проверять не на чем"


def test_главная_печатает_объясняющую_строку_когда_файла_карты_нет(
    data_copy: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Окружение настроено, а `photo-cues.md` в нём нет (D068) — законное состояние, не поломка.

    В отличие от `test_без_боевых_данных_код_возврата_2` ниже, здесь
    `AUDIT_DATA_DIR` указывает на полноценный каталог методики: не хватает
    только карты слов, остальные обязательные файлы (`checklist.csv` и
    другие) на месте. `load_cues()` в этом случае просто отдаёт пустой
    кортеж без исключения, и раздел печатает свою единственную строку, а не
    падает.
    """
    (data_copy / CUES_FILE).unlink()
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))

    rc = fpm.main(["--root", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 2
    assert "карты нет — отрицание проверять не на чем" in out


def test_код_возврата_не_зависит_от_раздела_про_отрицание(
    карта_с_повтором_основы: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Раздел про отрицание — числа для замера, а не сигнал «неверное срабатывание».

    Карта фикстуры несёт строку с повторённой основой — тот случай, который до
    T197 давал пропуск в разделе 2 (после T197 пропусков на ней нет вовсе).
    Проверяем все три исхода первого раздела (0, 1, 2) на одной и той же карте,
    чтобы исключить именно зависимость кода возврата от второго раздела, а не
    что-то соседнее.
    """
    monkeypatch.setattr(fpm, "load_records", lambda _root: ())
    assert fpm.main(["--root", str(tmp_path)]) == 2

    correct = Record(code="CLN06", zone="dining", note=FURNITURE, source="synthetic")
    monkeypatch.setattr(fpm, "load_records", lambda _root: (correct,))
    assert fpm.main(["--root", str(tmp_path)]) == 0

    wrong = Record(code="TEH05", zone="hot_kitchen", note=OVEN_WITH_ZONE, source="synthetic")
    monkeypatch.setattr(fpm, "load_records", lambda _root: (wrong,))
    assert fpm.main(["--root", str(tmp_path)]) == 1


@requires_data
def test_живой_замер_отрицания_на_боевой_карте(live_data_env: Path) -> None:
    """Якорь T195 на боевой карте: правило не обнуляет быстрый путь безобидным отрицанием.

    Этому тесту нужна именно боевая методика: корпус отрицаний строится из
    карты, а боевая карта — единственная, где строк достаточно, чтобы
    перестраховка была видна (на синтетической их единицы).

    **Числа здесь не закрепляются намеренно.** Карту ведёт управляющая
    компания (D066), правит её в любой момент и без нас — а тест, закрепивший
    сегодняшнюю долю или отпечаток карты, покраснел бы на первой же её правке
    и потребовал бы чинить набор тем же движением. Это ровно то, что убирали в
    T141, и возвращать это нельзя: доля и разбивка живут в `make fastpath`,
    который печатает их вместе с датой и версией карты.

    Закрепляется здесь свойство самого правила, от содержания карты не
    зависящее: отрицание, стоящее в ДРУГОЙ части фразы, не должно снимать ни
    одного срабатывания. Именно этим отличались отброшенные варианты правила —
    один из них терял 30 строк карты из 32, и терял молча.
    """
    bases = fpm.affirmative_bases(load_cues())
    lost = fpm.insurance_lost(bases)

    assert bases, "на боевой карте не нашлось ни одной утвердительно срабатывающей строки"
    assert lost == (), (
        "безобидное отрицание в другой части фразы сняло срабатывание у строк: "
        + ", ".join(b.cue.phrase[:30] for b in lost)
    )
