"""Инварианты записи нарушений: фиксация, правка, удаление, фотографии.

Методика (`docs/02-domain.md`) строится на том, что пара «пункт + зона» —
это одно нарушение и один вычет. Если движок пропустит дубль этой пары, отчёт
вычтет за одно и то же дважды; если проглотит недопустимый класс или зону —
отчёт сойдётся с методикой, которой не существует. Эти тесты проверяют, что
такие отказы доходят до аудитора текстом, а не тонут, и что после отказа
состояние осталось прежним: списанная запись — то же самое, что и списанная
дважды, если её нельзя откатить.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain import (
    Finding,
    Inspection,
    add_finding,
    attach_photo,
    drop_finding,
    edit_finding,
    get_state,
    start_inspection,
)
from src.domain.errors import EngineError, InspectionNotStarted, ValidationError


def начать(chat_id: int = 42, **kw: str) -> Inspection:
    params: dict[str, str] = {
        "unit": "Белград-1",
        "kind": "Плановая",
        "report_lang": "ru",
    }
    params.update(kw)
    return start_inspection(chat_id, **params)


def запись(chat_id: int, n: int) -> Finding | None:
    """Прочитать запись `n` из текущего состояния чата."""
    состояние = get_state(chat_id)
    assert состояние is not None, "проверка исчезла из состояния между операциями"
    return состояние.finding(n)


# --- Фиксация ---------------------------------------------------------------


def test_дубль_пары_пункт_и_зона_отклонён_с_номером_существующей_записи(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    with pytest.raises(EngineError) as e:
        add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="ещё нагар")
    assert "#1" in str(e.value), (
        f"отказ по дублю не назвал номер существующей записи — аудитор не поймёт, "
        f"какую запись поправить вместо повторной фиксации: {e.value}"
    )


def test_дубль_не_добавляет_вторую_запись(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар на печи")
    with pytest.raises(EngineError):
        add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="ещё нагар")
    состояние = get_state(42)
    assert состояние is not None
    assert len(состояние.findings) == 1, (
        "отказ движка не помешал второй записи попасть в состояние — "
        "отчёт вычтет за одну пару «пункт + зона» дважды"
    )


def test_класс_не_разрешённый_для_пункта_отклонён_с_перечнем_допустимых(domain_env: Path) -> None:
    начать()
    with pytest.raises(EngineError) as e:
        add_finding(42, code="CLN05", level="D2", zone="hot_kitchen", text="x")
    assert "D1" in str(e.value), (
        f"отказ по недопустимому классу не назвал, какие классы у CLN05 разрешены: {e.value}"
    )


def test_несуществующий_класс_отклонён(domain_env: Path) -> None:
    начать()
    with pytest.raises(EngineError):
        add_finding(42, code="CLN05", level="D9", zone="hot_kitchen", text="x")


def test_зона_вне_справочника_отклонена(domain_env: Path) -> None:
    начать()
    with pytest.raises(EngineError):
        add_finding(42, code="CLN05", level="D1", zone="подсобка", text="x")


def test_код_вне_чек_листа_отклонён(domain_env: Path) -> None:
    начать()
    with pytest.raises(EngineError):
        add_finding(42, code="XXX99", level="D1", zone="hot_kitchen", text="x")


def test_тот_же_пункт_в_другой_зоне_отдельное_нарушение(domain_env: Path) -> None:
    """CLN06 разрешён в нескольких зонах — та же пара «пункт + зона» занята только внутри одной."""
    начать()
    первая = add_finding(42, code="CLN06", level="D1", zone="hot_kitchen", text="загрязнение печи")
    вторая = add_finding(42, code="CLN06", level="D1", zone="dining", text="загрязнение зала")
    assert первая.n != вторая.n, "две записи в разных зонах слились в одну — потерян один вычет"
    состояние = get_state(42)
    assert состояние is not None
    assert len(состояние.findings) == 2, "в состоянии не обе записи по CLN06"


def test_информационная_запись_D0_фиксируется_успешно(domain_env: Path) -> None:
    начать()
    finding = add_finding(42, code="INF10", level="D0", zone="fridge", text="-5 °C")
    assert finding.level == "D0", (
        "INF10 должен фиксироваться классом D0 — иначе он попадёт в вычеты"
    )


def test_фиксация_без_начатой_проверки_отклонена(domain_env: Path) -> None:
    with pytest.raises(InspectionNotStarted):
        add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="x")


def test_формулировка_с_дефиса_сохраняется_дословно(domain_env: Path) -> None:
    """Строка, начинающаяся с «-», похожа на флаг командной строки движка — не должна обрезаться."""
    начать()
    finding = add_finding(
        42, code="CLN06", level="D1", zone="hot_kitchen", text="-20 °C на термометре"
    )
    assert finding.text == "-20 °C на термометре", (
        "формулировка исказилась — движок принял начало строки за собственный флаг"
    )
    сохранённая = запись(42, finding.n)
    assert сохранённая is not None
    assert сохранённая.text == "-20 °C на термометре", (
        "в состоянии текст записи разошёлся с тем, что вернул вызов"
    )


def test_нетипичная_зона_проходит_но_помечается_флагом(domain_env: Path) -> None:
    """Движок не отказывает, а помечает запись: CLN05 заведён только для горячего цеха.

    Флаг живёт в состоянии и в аргументы вызова не входит — значит ответ
    `add_finding` собран из того, что записано, а не из того, что попросили.
    """
    начать()
    обычная = add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    assert обычная.zone_unusual is False, "типичная зона помечена как подозрительная"
    странная = add_finding(42, code="CLN05", level="D1", zone="dining", text="нагар в зале")
    assert странная.zone_unusual is True, (
        "нетипичная зона не помечена — аудитор не увидит подсказки перепроверить, "
        "а движок такую запись пропускает молча"
    )


# --- Правка -------------------------------------------------------------


def test_правка_несуществующего_номера_отклонена_с_перечнем_номеров(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    with pytest.raises(EngineError) as e:
        edit_finding(42, 999, text="новый текст")
    assert "#1" in str(e.value), (
        f"отказ по несуществующему номеру не подсказал, какие номера вообще есть: {e.value}"
    )


def test_правка_незнакомого_поля_отклонена_с_перечнем_полей(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    with pytest.raises(ValidationError) as e:
        edit_finding(42, 1, foo="bar")
    сообщение = str(e.value)
    for имя in ("code", "level", "zone", "text", "comment"):
        assert имя in сообщение, (
            f"отказ по незнакомому полю не перечислил допустимое имя {имя!r}: {e.value}"
        )


def test_правка_без_полей_отклонена(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    with pytest.raises(ValidationError):
        edit_finding(42, 1)


def test_смена_зоны_на_занятую_парой_отклонена_запись_осталась_прежней(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN06", level="D1", zone="hot_kitchen", text="a")
    add_finding(42, code="CLN06", level="D1", zone="dining", text="b")
    with pytest.raises(EngineError):
        edit_finding(42, 1, zone="dining")
    первая = запись(42, 1)
    assert первая is not None
    assert первая.zone == "hot_kitchen", "запись переехала в занятую зону вопреки отказу движка"


def test_смена_класса_на_не_разрешённый_отклонена_класс_не_изменился(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN06", level="D1", zone="hot_kitchen", text="a")
    with pytest.raises(EngineError):
        edit_finding(42, 1, level="D2")
    первая = запись(42, 1)
    assert первая is not None
    assert первая.level == "D1", "класс записи сменился на недопустимый вопреки отказу движка"


def test_успешная_правка_класса_зоны_и_формулировки(domain_env: Path) -> None:
    """PRD01 допускает и D1, и D2 — значит класс меняется по-настоящему, а не совпадает."""
    начать()
    add_finding(42, code="PRD01", level="D1", zone="fridge", text="старый текст")
    итог = edit_finding(42, 1, level="D2", zone="cold_kitchen", text="новый текст")
    assert итог.n == 1, "номер записи не должен меняться при правке"
    assert (итог.level, итог.zone, итог.text) == ("D2", "cold_kitchen", "новый текст"), (
        "правка не применилась целиком — часть полей осталась старой"
    )
    в_состоянии = запись(42, 1)
    assert в_состоянии is not None
    assert (в_состоянии.level, в_состоянии.zone, в_состоянии.text) == (
        "D2",
        "cold_kitchen",
        "новый текст",
    ), "возврат edit_finding разошёлся с тем, что реально лежит в состоянии"


def test_правка_одного_поля_возвращает_запись_целиком(domain_env: Path) -> None:
    """Ответ берётся из состояния, а не собирается из просьбы.

    Иначе бот покажет аудитору для подтверждения ровно то, что сам же и
    отправил, — и не заметит, что движок записал другое.
    """
    начать()
    add_finding(42, code="PRD01", level="D2", zone="fridge", text="старый текст")
    итог = edit_finding(42, 1, text="новый текст")
    assert (итог.code, итог.level, итог.zone) == ("PRD01", "D2", "fridge"), (
        "в ответе на правку одного поля пропали остальные — запись собрана из аргументов, "
        "а не прочитана из состояния"
    )
    assert итог.text == "новый текст", "правка не доехала до состояния"


# --- Удаление -------------------------------------------------------------


def test_удаление_несуществующего_номера_отклонено(domain_env: Path) -> None:
    начать()
    with pytest.raises(EngineError):
        drop_finding(42, 999)


def test_удаление_убирает_запись_и_номер_не_переиспользуется(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN06", level="D1", zone="hot_kitchen", text="a")
    add_finding(42, code="CLN06", level="D1", zone="dining", text="b")
    drop_finding(42, 2)
    состояние = get_state(42)
    assert состояние is not None
    assert состояние.finding(2) is None, "удалённая запись всё ещё в состоянии"
    следующая = add_finding(42, code="CLN06", level="D1", zone="staff", text="c")
    assert следующая.n != 2, (
        "удалённый номер выдан заново — сквозная нумерация нарушена, "
        "старые ссылки на #2 (например, в фото) попадут не на ту запись"
    )


# --- Фотографии -------------------------------------------------------------


def test_идентификатор_кадра_с_запятой_отклонён_кадр_не_прикреплён(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    with pytest.raises(ValidationError):
        attach_photo(42, 1, "abc,def")
    первая = запись(42, 1)
    assert первая is not None
    assert первая.photos == [], (
        "движок разрезал бы идентификатор с запятой на два несуществующих кадра"
    )


def test_пустой_идентификатор_кадра_отклонён(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    with pytest.raises(ValidationError):
        attach_photo(42, 1, "   ")


def test_прикрепление_кадра_к_несуществующей_записи_отклонено(domain_env: Path) -> None:
    начать()
    with pytest.raises(EngineError):
        attach_photo(42, 999, "photo1")


def test_повторное_прикрепление_того_же_кадра_не_дублирует(domain_env: Path) -> None:
    начать()
    add_finding(42, code="CLN05", level="D1", zone="hot_kitchen", text="нагар")
    attach_photo(42, 1, "photoA")
    attach_photo(42, 1, "photoB")
    attach_photo(42, 1, "photoA")
    первая = запись(42, 1)
    assert первая is not None
    assert sorted(первая.photos) == ["photoA", "photoB"], (
        f"повтор того же идентификатора должен быть без дублей, получили {первая.photos}"
    )
