"""T164 (D077): предложение модели сохраняется рядом с итоговой записью.

Владелец, дословно: «при несостыковках, или если пользователь добавит что-то в
духе "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА" то мы долполняем наш список
терминов». Пополняет список человек (D077 запрещает автоматическое пополнение:
быстрый путь показывает пункт без подтверждения, и неверное слово уехало бы в
отчёт партнёру). Но пополнять нечего, пока промах не сохранён: сегодня
предложение модели не живёт нигде — ни код, ни уверенность, ни то, что аудитор
поменял.

Здесь хранится ПРЕДЛОЖЕНИЕ, а «что аудитор изменил» выводится сравнением с
итоговой записью, которая лежит в той же строке. Второй копии этого факта в
базе нет намеренно: записанная отдельно, она разъехалась бы с парой, из которой
считается, и разъехалась бы молча.

**Чем эти тесты отличаются от остальных в блоке.** Домен предложение пока не
отдаёт — задача T164 закладывает форму со стороны базы, как `findings.source`
был заложен в `0001` за задачу до появления значения. Поэтому находка с
предложением собирается здесь наследником `domain.models.Finding`, ровно тем
способом, каким домен добавит поля у себя, а всё остальное — оценка, точка,
транзакция, печать проверки — идёт настоящее.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from conftest import requires_db

psycopg = pytest.importorskip("psycopg")

from src.db.errors import PushError  # noqa: E402 — после importorskip намеренно
from src.db.fingerprint import compute_fingerprint  # noqa: E402
from src.db.push import push_inspection  # noqa: E402
from src.db.queries import findings_by_unit, get_inspection  # noqa: E402
from src.domain import add_finding, get_state, start_inspection  # noqa: E402
from src.domain import score as domain_score  # noqa: E402
from src.domain.models import Finding, Inspection  # noqa: E402

pytestmark = requires_db

АРЕНДАТОР = "предложения"
ТОЧКА = "Белград-1"


@dataclass(frozen=True)
class СПредложением(Finding):
    """Находка, к которой домен однажды приложит предложение модели.

    Форма выбрана по образцу `source`: плоские поля со значением по умолчанию,
    чтобы слой записи брал их через `getattr` и работал одинаково и с находкой,
    у которой предложения нет, и с той, у которой оно есть.
    """

    suggested_code: str | None = None
    suggested_level: str | None = None
    suggested_zone: str | None = None
    suggested_confidence: float | None = None


def _строки(dsn: str, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _проверка(chat_id: int) -> Inspection:
    """Настоящая завершённая проверка с одной записью."""
    start_inspection(chat_id, unit=ТОЧКА, kind="planned", report_lang="ru", tenant=АРЕНДАТОР)
    add_finding(chat_id, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    состояние = get_state(chat_id)
    assert состояние is not None
    return состояние


def _с_предложением(
    monkeypatch: pytest.MonkeyPatch, состояние: Inspection, **предложение: object
) -> None:
    """Подложить сливу ту же проверку, но с предложением модели у её записи.

    Подменяется ровно одно — то, чего домен ещё не отдаёт. Оценка, разрешение
    точки, транзакция и печать проверки остаются настоящими: подмена целого
    состояния проверила бы только фантазию этого файла о домене.
    """
    находки = [СПредложением(**находка.__dict__, **предложение) for находка in состояние.findings]
    monkeypatch.setattr(
        "src.db.push.get_state", lambda chat_id: replace(состояние, findings=находки)
    )


def test_предложение_модели_ложится_рядом_с_записью(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главная проверка задачи: сигнал о промахе перестал теряться.

    Аудитор записал `CLN05`/`D1`/`hot_kitchen`, а модель предлагала другой
    пункт с другой зоной и была в этом уверена на 0.82 — вот это расхождение и
    есть то, ради чего задача заводилась (D077).
    """
    состояние = _проверка(901)
    _с_предложением(
        monkeypatch,
        состояние,
        suggested_code="CLN06",
        suggested_level="D2",
        suggested_zone="dough",
        suggested_confidence=0.82,
    )

    ident = push_inspection(901)

    (запись,) = _строки(
        db_env,
        "select code, level, zone, suggested_code, suggested_level, suggested_zone, "
        "suggested_confidence from findings where inspection_id = %s",
        (ident,),
    )
    assert запись[:3] == ("CLN05", "D1", "hot_kitchen"), "итоговая запись не та, что зафиксировали"
    assert запись[3:6] == ("CLN06", "D2", "dough"), "предложение модели не сохранилось"
    assert float(запись[6]) == pytest.approx(0.82), "уверенность модели не сохранилась"


def test_чтение_отдаёт_предложение_и_говорит_что_поправил_аудитор(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Читающему нужна не пара строк, а ответ: что именно аудитор поменял.

    Ответ выводится из двух троек в одной строке, а не хранится третьей копией:
    записанный отдельно, он разъехался бы с ними и не покраснел бы.
    """
    состояние = _проверка(902)
    _с_предложением(
        monkeypatch,
        состояние,
        suggested_code="CLN06",
        suggested_level="D1",
        suggested_zone="dough",
        suggested_confidence=0.5,
    )
    ident = push_inspection(902)

    подробно = get_inspection(ident, tenant=АРЕНДАТОР)

    assert подробно is not None
    (находка,) = подробно.findings
    assert находка.suggested_code == "CLN06"
    assert находка.suggested_level == "D1"
    assert находка.suggested_zone == "dough"
    assert находка.suggested_confidence == pytest.approx(0.5)
    assert находка.corrections() == ("code", "zone"), (
        "не назван ровно тот набор, который аудитор поправил: класс модель угадала, "
        "а пункт и зону — нет"
    )


def test_совпавшее_предложение_не_считается_правкой(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Модель попала — правки нет, и в предложения для УК это не идёт.

    Без этой проверки «что поправил аудитор» легко оказалось бы «всё, где есть
    предложение», и список терминов пополнялся бы удачными попаданиями.
    """
    состояние = _проверка(903)
    _с_предложением(
        monkeypatch,
        состояние,
        suggested_code="CLN05",
        suggested_level="D1",
        suggested_zone="hot_kitchen",
        suggested_confidence=0.97,
    )
    ident = push_inspection(903)

    подробно = get_inspection(ident, tenant=АРЕНДАТОР)

    assert подробно is not None
    (находка,) = подробно.findings
    assert находка.corrections() == ()


def test_запись_без_предложения_не_выглядит_исправленной(domain_env: Path, db_env: str) -> None:
    """Записи заводят и без модели — вручную, и такими же они лежат до T164.

    Здесь важнее всего не перепутать «модель не спрашивали» с «модель промахнулась
    по всем трём полям»: второе — сигнал для управляющей компании, первое —
    обычная ручная запись, и попав в один список, они утопили бы друг друга.
    """
    _проверка(904)

    ident = push_inspection(904)

    подробно = get_inspection(ident, tenant=АРЕНДАТОР)
    assert подробно is not None
    (находка,) = подробно.findings
    assert находка.suggested_code is None
    assert находка.suggested_level is None
    assert находка.suggested_zone is None
    assert находка.suggested_confidence is None
    assert находка.corrections() == (), "запись без предложения выглядит исправленной"


def test_пустая_строка_не_выдаётся_за_ответ_модели(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пусто — это «модель не называла пункта», и в базе оно `NULL`, а не «».

    Пустая строка в колонке выглядела бы ответом модели: она не совпала бы с
    записанным пунктом, и запись попала бы в выборку промахов для управляющей
    компании — притом что модели тут не было вовсе.
    """
    состояние = _проверка(910)
    _с_предложением(monkeypatch, состояние, suggested_code="  ", suggested_zone="")

    ident = push_inspection(910)

    (запись,) = _строки(
        db_env,
        "select suggested_code, suggested_zone from findings where inspection_id = %s",
        (ident,),
    )
    assert запись == (None, None), "пустая строка легла в базу как ответ модели"

    подробно = get_inspection(ident, tenant=АРЕНДАТОР)
    assert подробно is not None
    assert подробно.findings[0].corrections() == ()


def test_находки_точки_тоже_несут_предложение(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Второй запрос слоя чтения — свой текст, своя возможность его забыть.

    Именно по этому пути T165 и пойдёт собирать предложения для управляющей
    компании: «что у этой точки модель называет не так».
    """
    состояние = _проверка(905)
    _с_предложением(
        monkeypatch,
        состояние,
        suggested_code="CLN06",
        suggested_level="D1",
        suggested_zone="hot_kitchen",
        suggested_confidence=0.31,
    )
    push_inspection(905)

    (находка,) = findings_by_unit(tenant=АРЕНДАТОР, unit=ТОЧКА)

    assert находка.suggested_code == "CLN06"
    assert находка.suggested_confidence == pytest.approx(0.31)
    assert находка.corrections() == ("code",)


def test_предложение_не_меняет_отпечаток_проверки(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отпечаток — это содержимое проверки, а предложение содержимым не является.

    Попади оно в отпечаток, проверка, слитая до появления предложения и слитая
    после, получила бы два разных отпечатка — то есть две строки в истории
    точки вместо одной. Ровно то, от чего отпечаток и стоит.
    """
    состояние = _проверка(906)
    оценка = domain_score(906)
    без = compute_fingerprint(состояние, оценка, tenant_code=АРЕНДАТОР)
    _с_предложением(
        monkeypatch,
        состояние,
        suggested_code="CLN06",
        suggested_level="D2",
        suggested_zone="dough",
        suggested_confidence=0.4,
    )
    с_предложением = compute_fingerprint(
        replace(
            состояние,
            findings=[
                СПредложением(**f.__dict__, suggested_code="CLN06") for f in состояние.findings
            ],
        ),
        оценка,
        tenant_code=АРЕНДАТОР,
    )

    assert без == с_предложением, (
        "предложение модели попало в отпечаток — повторный слив даст дубль"
    )


@pytest.mark.parametrize(
    ("значение", "чат"),
    [("почти", 907), (True, 911), (["0.8"], 912)],
    ids=["словом", "да-нет", "списком"],
)
def test_нечисловая_уверенность_это_отказ_а_не_молчаливый_ноль(
    domain_env: Path,
    db_env: str,
    monkeypatch: pytest.MonkeyPatch,
    значение: object,
    чат: int,
) -> None:
    """Непонятное значение не записывается: записанное, оно неотличимо от настоящего.

    Тот же довод, по которому `domain.read_sources` отказывается разбирать
    незнакомый источник записи. Ноль здесь был бы худшим исходом: «модель ни в
    чём не уверена» — осмысленное утверждение, и оно неверно.

    `True` в этом списке не для полноты: булево значение — подкласс числа, и
    `float(True)` тихо даёт 1.0, то есть «модель уверена полностью». Из всех
    молчаливых подстановок эта самая дорогая.
    """
    состояние = _проверка(чат)
    _с_предложением(monkeypatch, состояние, suggested_code="CLN06", suggested_confidence=значение)

    with pytest.raises(PushError, match="Уверенность модели"):
        push_inspection(чат)


def test_уверенность_вне_диапазона_это_отказ(
    domain_env: Path, db_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уверенность — доля от нуля до единицы, и всё остальное — чужая шкала.

    Проценты вместо доли (82 вместо 0.82) — самый вероятный способ ошибиться, и
    записанные молча, они сделали бы порог отбора для управляющей компании
    бессмысленным на всей выборке разом.
    """
    состояние = _проверка(908)
    _с_предложением(monkeypatch, состояние, suggested_code="CLN06", suggested_confidence=82.0)

    with pytest.raises(PushError, match="Уверенность модели"):
        push_inspection(908)


def test_схема_сама_не_пускает_чужую_шкалу_уверенности(
    domain_env: Path, pg_dsn: str, db_env: str
) -> None:
    """Диапазон стережёт не только слив, но и сама схема (миграция `0007`).

    Заслон в коде снимается вместе с кодом: историю за прошлые годы зальют
    программно (D035), и та заливка пойдёт своим путём. Поэтому проверка идёт
    под привилегированной ролью и прямым `UPDATE` — так, как эту базу и
    наполнит любая выгрузка мимо `push_inspection`.

    Заслонов в схеме два, и они разные. Проценты (82) не проходят уже по типу
    колонки: `numeric(4, 3)` не вмещает двузначное число. А `5` в тип
    помещается — и его останавливает ограничение диапазона, ради которого оно
    и заведено.
    """
    _проверка(909)
    ident = push_inspection(909)

    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        with pytest.raises(psycopg.errors.NumericValueOutOfRange):
            cur.execute(
                "update findings set suggested_confidence = 82 where inspection_id = %s",
                (ident,),
            )
        conn.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "update findings set suggested_confidence = 5 where inspection_id = %s",
                (ident,),
            )
        conn.rollback()
        cur.execute(
            "update findings set suggested_confidence = 0.82 where inspection_id = %s",
            (ident,),
        )
        assert cur.rowcount == 1, "доля в границах тоже не прошла — отказы выше были не о шкале"
