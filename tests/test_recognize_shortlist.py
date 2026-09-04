"""T030: сужение перечня пунктов до кандидатов ДО вызова модели.

Главный урок разведки (`docs/forge/research/recognize-probe.md`): когда правильного
кода среди кандидатов нет, модель не отказывается — она уверенно предлагает похожий
пункт с осмысленной формулировкой. Ошибка сужения не выглядит как ошибка, поэтому
зональный перечень здесь — база, которую нельзя резать, а карта кадров
(`data/photo-cues.md`) только добавляет коды и переставляет их в начало.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.domain import allowed_levels, list_items
from src.domain.errors import ValidationError
from src.recognize.cues import class_thresholds, load_cues, match_cues
from src.recognize.shortlist import MANUAL_ONLY, shortlist


def test_перечень_зоны_это_база_и_она_не_режется(domain_env: Path) -> None:
    зональные = {i.code for i in list_items(zone="hot_kitchen") if i.kind == "violation"}
    зональные -= set(MANUAL_ONLY)

    итог = shortlist("под конвейерной лентой печи нагар", zone_hint="hot_kitchen")

    assert зональные <= set(итог.codes), (
        "карта кадров обязана добавлять коды к зональному перечню, а не заменять его"
    )
    # Страховка от вырождения: если бы зоне досталась пара пунктов, включение
    # «зональные <= коды» выполнялось бы само собой и ничего не стерегло. Порог
    # выводится из методики, а не вписан числом: вписанное число — то самое,
    # что ломалось от чужой правки данных (T141).
    предлагаемые = {i.code for i in list_items() if i.kind == "violation"} - set(MANUAL_ONLY)
    assert len(зональные) > len(предлагаемые) // 2, (
        "перечень зоны выродился — проверка «карта не режет базу» стала бы пустой"
    )


def test_служебные_пункты_не_предлагаются(domain_env: Path) -> None:
    служебные = {i.code for i in list_items() if i.kind in ("aggregate", "info")}

    итог = shortlist("температура в холодильнике +9", zone_hint="fridge")

    assert not (служебные & set(итог.codes)), (
        "правило 8 из docs/03-recording-rules.md: kind=aggregate и kind=info не предлагать"
    )


def test_пункты_ручного_решения_аудитора_не_предлагаются(domain_env: Path) -> None:
    итог = shortlist("массовые мелкие нарушения по всей точке", zone_hint="dining")

    assert not (set(MANUAL_ONLY) & set(итог.codes)), (
        "MGM22 и MGM23 выставляет аудитор сам — data/photo-cues.md, последний раздел"
    )


def test_карта_кадров_поднимает_свои_коды_в_начало(domain_env: Path) -> None:
    итог = shortlist("печь грязная, под лентой нагар", zone_hint="hot_kitchen")

    assert "CLN05" in итог.cue_hits, "печь → CLN05 по карте кадров"
    assert итог.codes[: len(итог.cue_hits)] == итог.cue_hits, "подсказки идут первыми"
    assert итог.codes.index("CLN05") < итог.codes.index("PRD01")


def test_карта_кадров_добавляет_код_которого_нет_в_зоне(domain_env: Path) -> None:
    зона_зала = {i.code for i in list_items(zone="dining")}
    assert "CLN05" not in зона_зала, "печь в зале не проверяют — иначе тест ничего не ловит"

    итог = shortlist("печь в нагаре", zone_hint="dining")

    assert "CLN05" in итог.codes, (
        "слова аудитора важнее вида зоны: пункт добавляется, а не теряется"
    )
    assert "CLN12" in итог.codes, "зональная база при этом остаётся целой"


def test_без_подсказки_зоны_берутся_все_нарушения(domain_env: Path) -> None:
    все = {i.code for i in list_items() if i.kind == "violation"} - set(MANUAL_ONLY)

    итог = shortlist("грязно", zone_hint=None)

    assert set(итог.codes) == все
    assert итог.zone is None


def test_неизвестная_зона_отвергается(domain_env: Path) -> None:
    with pytest.raises(ValidationError):
        shortlist("грязно", zone_hint="кухня")


def test_без_карты_кадров_перечень_не_режется_и_отказа_нет(
    domain_env: Path,
    data_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Карты нет — работаем дальше, а не отказываем (T157, D068).

    Раньше здесь стоял обратный тест: блок отказывался, и довод был в том, что
    без карты сужение перечня превращается в догадку, а усечённый перечень
    модель не отвергает — она уверенно предлагает похожий пункт. Довод снят
    измерением, а не мнением: перечень с картой и без неё ОДИНАКОВ, потому что
    карта его не режет, а только дополняет кодами и переставляет их вперёд.

    Цена прежнего поведения была высокой: файл числился необязательным на
    старте и оказывался обязательным в работе, поэтому отказ всплывал не при
    подъёме стенда, а в чате у аудитора на первом же комментарии.
    """
    с_картой = shortlist("печь в нагаре", zone_hint="hot_kitchen")

    (data_copy / "photo-cues.md").unlink()
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))
    with caplog.at_level(logging.WARNING):
        без_карты = shortlist("печь в нагаре", zone_hint="hot_kitchen")

    assert len(без_карты) == len(с_картой), "без карты перечень пунктов не должен резаться"
    assert "photo-cues.md" in caplog.text, "отсутствие карты обязано быть названо в журнале"


def test_подсказка_срабатывает_на_словах_а_не_на_точном_совпадении(domain_env: Path) -> None:
    подсказки = load_cues()

    # Слева — форма из комментария аудитора, в карте кадров стоит другая:
    # «ярлыки», «неукомплектована», «неисправны», «печи».
    assert "PRD09" in match_cues("неверные ярлыки на таре", подсказки)
    assert "SFT08" in match_cues("аптечка неукомплектована", подсказки)
    assert "TEH04" in match_cues("весы неисправны", подсказки)
    assert "CLN05" in match_cues("печи не мыли", подсказки)
    assert match_cues("всё хорошо", подсказки) == ()


def test_пороги_классов_вынимаются_для_промпта(domain_env: Path) -> None:
    пороги = class_thresholds()

    assert "PRD09" in пороги and "FSB05" in пороги
    assert "Печь" not in пороги, "в промпт идёт только таблица порогов, а не весь документ"


def test_информационные_пункты_остаются_в_перечне(domain_env: Path) -> None:
    """`INF09`, `INF10`, `INF11` заведены в методике как `kind=violation` с классом `D0`.

    Это не нарушения, а замеры и фотографии продукта — записи, которые в отчёте
    нужны (`docs/02-domain.md`). Убрать их из перечня значило бы заставить модель
    натягивать на кадр с дисплеем термометра какое-нибудь настоящее нарушение.
    """
    итог = shortlist("на дисплее холодильной камеры +6", zone_hint="fridge")

    assert "INF10" in итог.codes
    assert allowed_levels("INF10") == ["D0"], "класс такой записи задан методикой, не кодом"


def test_раздел_порогов_не_становится_подсказкой(tmp_path: Path) -> None:
    """В разделе порогов коды стоят рядом с условиями класса, а не с описанием кадра.

    Строка «PRD09 нет ярлыка → одна-две тары» отвечает на вопрос «какой
    класс», а не «что видно». Попав в подсказки, она подняла бы PRD11 на любое
    упоминание тары и перемешала бы порядок кандидатов.
    """
    карта = tmp_path / "photo-cues.md"
    карта.write_text(
        "# Карта\n\n"
        "## Пороги классов\n\n"
        "| Пункт | D1 | D2 |\n"
        "|---|---|---|\n"
        "| PRD09 нет ярлыка | одна-две тары | три-четыре тары, см. PRD11 |\n\n"
        "## Чистота\n\n"
        "| Объект | Грязь | Поломка |\n"
        "|---|---|---|\n"
        "| Печь | CLN05 | TEH05 |\n",
        encoding="utf-8",
    )

    подсказки = load_cues(карта)

    assert [c.phrase for c in подсказки] == ["Печь"]
    assert подсказки[0].codes == ("CLN05", "TEH05")
    assert "PRD11" not in {code for c in подсказки for code in c.codes}
    assert class_thresholds(карта).splitlines()[-1].startswith("| PRD09")
