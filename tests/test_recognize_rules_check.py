"""T032: второй слой после модели — код-уровневая проверка формулировки.

Промпт запрещает масштаб за кадром, додуманное повреждение и похвалу без
основания (`docs/03-recording-rules.md`, правила 2–4), но у текста
формулировки нет `enum`, который гарантировал бы соблюдение так же, как схема
гарантирует код. Здесь проверяется код-уровневая страховка — на тех же
формулировках, что породили правила (боевые ошибки из документа), а не на
придуманных фразах.
"""

from __future__ import annotations

from src.recognize.rules_check import (
    DAMAGE_WITHOUT_BASIS,
    SCALE_BEYOND_FRAME,
    UNWARRANTED_PRAISE,
    check_wording,
)


def test_масштаб_за_кадром_из_боевого_отчёта() -> None:
    # Правило 2, дословная формулировка боевой ошибки
    wording = "Обои и стена над плинтусом в затирках и потёртостях на протяжении нескольких метров"

    flags = check_wording(wording, reason="видно на кадре")

    assert SCALE_BEYOND_FRAME in flags


def test_другие_обороты_масштаба_тоже_ловятся() -> None:
    assert SCALE_BEYOND_FRAME in check_wording("грязь в нескольких местах", "")
    assert SCALE_BEYOND_FRAME in check_wording("мусор по всей ширине зала", "")
    assert SCALE_BEYOND_FRAME in check_wording("не убирали в течение всей смены", "")


def test_повреждение_без_металла_из_боевого_отчёта() -> None:
    # Правило 3: «в плотных загрязнениях с коррозией» на фото, где под грязью
    # целый металл — ошибка повторилась дважды в двух отчётах
    wording = "Решётка в плотных загрязнениях с коррозией"

    flags = check_wording(wording, reason="")

    assert DAMAGE_WITHOUT_BASIS in flags


def test_повреждение_с_упомянутым_металлом_не_помечается() -> None:
    # Металл виден и назван — ровно то условие, при котором правило 3
    # разрешает писать «коррозия»
    wording = "На видимом металле кронштейна — коррозия"

    flags = check_wording(wording, reason="")

    assert DAMAGE_WITHOUT_BASIS not in flags


def test_похвала_без_основания_из_боевого_отчёта() -> None:
    # Правило 4, дословная формулировка боевой ошибки. Модель не проверяла
    # то, что хвалит, — reason пуст, основания в ответе нет
    wording = (
        "Борт равномерный, пропёк без белых участков, начинка распределена ровно. Нареканий нет"
    )

    flags = check_wording(wording, reason="")

    assert UNWARRANTED_PRAISE in flags


def test_похвала_с_основанием_не_помечается() -> None:
    # Основание есть — это и есть то, чего требует правило 4
    wording = "Нареканий нет"

    flags = check_wording(wording, reason="осмотрены все четыре борта, дефектов не видно")

    assert UNWARRANTED_PRAISE not in flags


def test_дисциплинированная_формулировка_из_разведки_без_пометок() -> None:
    # docs/forge/research/recognize-probe.md: формулировка, которую разведка
    # прямо называет дисциплинированной — без масштаба и без додуманного
    # повреждения
    wording = "Под конвейерной лентой печи видны нагар и пригоревшие крошки"

    flags = check_wording(wording, reason="видно на кадре")

    assert flags == ()


def test_пометки_не_исключают_друг_друга() -> None:
    wording = "Коррозия по всей ширине оборудования. Нареканий нет"

    flags = check_wording(wording, reason="")

    assert SCALE_BEYOND_FRAME in flags
    assert DAMAGE_WITHOUT_BASIS in flags
    assert UNWARRANTED_PRAISE in flags
