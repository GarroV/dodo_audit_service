"""T113: быстрый путь — пункт без вызова модели, когда слова аудитора однозначны.

Опасность здесь ровно та же, что у сужения перечня (`recognize-probe.md`), только
без модели: показать аудитору готовый пункт, которого он не имел в виду, — и он
подтвердит его нажатием. Поэтому тесты проверяют не «сработало на боевой записи»,
а **отказ срабатывать** на всех случаях, где слова допускают второе прочтение.

Критерий однозначности (все условия сразу):

1. зона названа человеком — правило 6 из `docs/03-recording-rules.md`;
2. слова покрывают строку карты кадров **целиком**, а не задевают её одним общим
   словом;
3. строка после разбора колонок «Грязь | Поломка» даёт **ровно один** код;
4. у пункта **единственный допустимый класс** — иначе класс выбирает аудитор;
5. пункт применим к названной зоне и не служебный.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.domain import allowed_levels, get_item, list_items
from src.domain.errors import ValidationError
from src.recognize.config import NO_CHAT
from src.recognize.cues import load_cues
from src.recognize.fastpath import (
    NO_COLUMN,
    NO_CUE,
    NO_ZONE,
    NOT_OFFERED,
    SEVERAL_ITEMS,
    SEVERAL_LEVELS,
    WRONG_ZONE,
    fast_path,
)

# --- карта кадров помнит колонки -------------------------------------------


def test_строка_карты_помнит_колонки_грязь_и_поломка(domain_env: Path) -> None:
    """«Печь | CLN05 | TEH05» — это не два равноправных кандидата.

    Карта сама объясняет разницу: «грязь — это CLN*, поломка — TEH*». Пока коды
    склеены в один список, строка неотличима от строки «PRD09, PRD11», где
    выбирать действительно нечем.
    """
    печь = [c for c in load_cues(chat_id=NO_CHAT) if c.phrase == "Печь"]

    assert len(печь) == 1, "в карте кадров должна быть строка объекта «Печь»"
    assert печь[0].by_column == (("Грязь", ("CLN05",)), ("Поломка", ("TEH05",)))
    assert печь[0].codes == ("CLN05", "TEH05"), "склеенный список остаётся для сужения перечня"


def test_прочерк_в_колонке_не_становится_колонкой(domain_env: Path) -> None:
    раковина = [
        c for c in load_cues(chat_id=NO_CHAT) if c.phrase.startswith("Раковина и смеситель")
    ]

    assert len(раковина) == 1
    assert раковина[0].by_column == (("Грязь", ("CLN02",)),), "у «поломки» стоит прочерк"


def test_строка_с_одной_колонкой_кандидатов(domain_env: Path) -> None:
    тара = [c for c in load_cues(chat_id=NO_CHAT) if c.phrase.startswith("Тара без ярлыка")]

    assert len(тара) == 1
    assert тара[0].by_column == (("Пункты", ("PRD09", "PRD11")),)


# --- быстрый путь срабатывает ----------------------------------------------


def test_названный_объект_и_грязь_дают_один_пункт(domain_env: Path) -> None:
    """Боевая запись Белград-1: «Печь: … плотный серо-белый нагар …»."""
    итог = fast_path(
        "Печь: под конвейерной лентой плотный серо-белый нагар", "hot_kitchen", chat_id=NO_CHAT
    )

    assert итог.item is not None, итог.reason
    assert итог.item.code == "CLN05"
    assert итог.item.level == "D1"
    assert итог.item.zone == "hot_kitchen"
    assert итог.item.cue == "Печь", "аудитору показывается, какая строка карты сработала"


def test_названный_объект_и_поломка_дают_другой_пункт(domain_env: Path) -> None:
    итог = fast_path("печь сломана, дверца треснула", "hot_kitchen", chat_id=NO_CHAT)

    assert итог.item is not None, итог.reason
    assert итог.item.code == "TEH05", "поломка того же объекта — другой пункт методики"


def test_текст_пункта_берётся_из_методики_а_не_сочиняется(domain_env: Path) -> None:
    """Быстрый путь не пишет формулировок вообще.

    Правила 2–4 (масштаб за кадром, характер повреждения, похвала без основания)
    нарушить нечем: на кнопке стоит вопрос чек-листа, а текст записи остаётся
    словами самого аудитора.
    """
    итог = fast_path(
        "Мебель участка в зале не протёрта: мелкие крошки на диванах", "dining", chat_id=NO_CHAT
    )

    assert итог.item is not None, итог.reason
    assert итог.item.code == "CLN06"
    assert итог.item.title == get_item("CLN06").question("ru")


# --- быстрый путь отказывается ---------------------------------------------


def test_без_зоны_быстрый_путь_не_срабатывает(domain_env: Path) -> None:
    итог = fast_path("Печь: под лентой нагар", None, chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == NO_ZONE, "зону называет человек — правило 6"


def test_характер_проблемы_не_назван_не_однозначно(domain_env: Path) -> None:
    """Боевая запись Белград-2: «Панель печи Senoven: температура 277 °C».

    Строка «Печь» покрыта целиком, но запись про печь — не про грязь и не про
    поломку, а замер (`INF09`). Верного кода в строке карты нет вовсе — ровно тот
    случай, ради которого критерий строгий.
    """
    итог = fast_path(
        "Панель печи: температура 277 °C, время выпекания 00:40", "hot_kitchen", chat_id=NO_CHAT
    )

    assert итог.item is None
    assert итог.reason == NO_COLUMN


def test_грязь_и_поломка_вместе_не_однозначно(domain_env: Path) -> None:
    итог = fast_path("печь в нагаре, и дверца треснула", "hot_kitchen", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == NO_COLUMN, "две записи, а не одна — правило 11"


def test_две_строки_карты_дают_разные_пункты(domain_env: Path) -> None:
    итог = fast_path("Печь в нагаре, мебель участка в крошках", "hot_kitchen", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == SEVERAL_ITEMS


def test_одна_строка_с_несколькими_кандидатами_не_однозначна(domain_env: Path) -> None:
    """«Тара без ярлыка, открытая упаковка → PRD09, PRD11».

    Карта сама предлагает выбор из двух пунктов — быстрому пути выбирать нечем.
    """
    итог = fast_path("Тара без ярлыка, открытая упаковка", "fridge", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == SEVERAL_ITEMS


def test_общее_слово_не_поднимает_строку_карты(domain_env: Path) -> None:
    """«Пол в курьерской зоне: пыль и мусор» задевает строку одним словом из двух.

    Строка карты — «Пол участка». Для сужения перечня такого касания
    достаточно, для решения за аудитора — нет.
    """
    итог = fast_path(
        "Пол в курьерской зоне: скопление пыли и мусора в углу", "staff", chat_id=NO_CHAT
    )

    assert итог.item is None
    assert итог.reason == NO_CUE


def test_пустой_комментарий_не_срабатывает(domain_env: Path) -> None:
    assert fast_path("", "hot_kitchen", chat_id=NO_CHAT).reason == NO_CUE
    assert fast_path("   ", "hot_kitchen", chat_id=NO_CHAT).reason == NO_CUE


def test_класс_с_выбором_остаётся_за_аудитором(domain_env: Path) -> None:
    """«Ярлык на таре → PRD09», классы D1–D3.

    Код один, но D1 это или D3 — зависит от массовости, которую видел человек, а
    не карта. Показать кнопку с готовым классом значило бы решить за него.
    """
    assert len(allowed_levels("PRD09")) > 1, "тест бессмыслен, если у пункта один класс"

    итог = fast_path("Ярлык на таре нечитаемый, дата стёрлась", "dry_storage", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == SEVERAL_LEVELS


def test_пункт_не_применим_к_названной_зоне(domain_env: Path) -> None:
    """Печь в зале не проверяют. Расхождение слов и места — работа для модели."""
    assert "CLN05" not in {i.code for i in list_items(zone="dining")}

    итог = fast_path("печь в нагаре", "dining", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == WRONG_ZONE


def test_служебный_пункт_быстрым_путём_не_предлагается(
    domain_env: Path, data_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Правило 8: `kind=aggregate` и `kind=info` не предлагать.

    В боевой карте таких строк нет, поэтому проверка идёт на копии методики:
    отсутствие строки сегодня не гарантирует, что её не впишут завтра.
    """
    assert get_item("PRD16").kind != "violation", "PRD16 должен быть служебным пунктом"
    карта = data_copy / "photo-cues.md"
    карта.write_text(
        карта.read_text(encoding="utf-8")
        + "\n## Проверка\n\n| Что на кадре | Пункты |\n|---|---|\n"
        + "| Служебная строка карты | PRD16 |\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))

    итог = fast_path("служебная строка карты", "hot_kitchen", chat_id=NO_CHAT)

    assert итог.item is None
    assert итог.reason == NOT_OFFERED


# --- словарь колонок --------------------------------------------------------


# Тесты «словарь колонок разбирается тем же стеммером» и «словари колонок не
# пересекаются» переехали в `tests/test_recognize_language.py` (T192): слова
# колонок стали данными и объявляются на каждый язык, поэтому проверять их
# по-русски мало — там они проверяются по всем объявленным языкам сразу.


def test_отрицание_рядом_снимает_слово_колонки(domain_env: Path) -> None:
    """«Печь без нагара» — это не нагар, а «нагара нет» — тем более.

    Совпадение идёт по основам слов и частицу не видит, поэтому слово рядом с
    отрицанием в выборе колонки не участвует вовсе. Отказ здесь стоит четырёх
    секунд и вызова модели, а срабатывание — записи в отчёте партнёру, которую
    аудитор подтвердил нажатием, не читая.
    """
    assert (
        fast_path("печь без нагара, всё чисто", "hot_kitchen", chat_id=NO_CHAT).reason == NO_COLUMN
    )
    assert fast_path("Печь: нагара нет", "hot_kitchen", chat_id=NO_CHAT).reason == NO_COLUMN
    assert fast_path("печь не сломана", "hot_kitchen", chat_id=NO_CHAT).reason == NO_COLUMN


def test_отрицание_не_мешает_настоящему_срабатыванию(domain_env: Path) -> None:
    """Проверка от перестраховки: «не протёрта» отрицает не то слово, что решает.

    Колонку выбирают «крошки», а «не» стоит при «протёрта» — слове, которого в
    словарях нет намеренно, потому что оно переворачивается отрицанием.
    """
    итог = fast_path(
        "Мебель участка в зале не протёрта: мелкие крошки на диванах", "dining", chat_id=NO_CHAT
    )

    assert итог.item is not None, итог.reason
    assert итог.item.code == "CLN06"


def test_неизвестная_зона_отвергается_всегда(domain_env: Path) -> None:
    """Зона проверяется до слов, а не после.

    Иначе отказ методики зависел бы от того, задел ли комментарий строку карты:
    на «печь в нагаре» вызов падал бы, а на «всё хорошо» тихо возвращал «не
    однозначно» — и опечатка в коде зоны нашлась бы через месяц.
    """
    with pytest.raises(ValidationError):
        fast_path("всё хорошо", "кухня", chat_id=NO_CHAT)
    with pytest.raises(ValidationError):
        fast_path("печь в нагаре", "кухня", chat_id=NO_CHAT)
