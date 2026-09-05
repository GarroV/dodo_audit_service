"""T166 (D077): распознавание указания «<описание>, это <процесс>».

Признак ненадёжен — тот же текст бывает и обычным комментарием, — поэтому
тесты проверяют не только срабатывание, но и отказ на каждом условии, которое
признак не выполняет: только связка без описания, лишнее слово в хвосте,
процесс без связки рядом, тире вместо связки.

Методика синтетическая (`domain_env`, `tests/methodology/checklist.csv`):
боевые имена процессов управляющей компании здесь не цитируются. Пример
владельца («ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ЧИСТОТА») переписан на
синтетическое имя процесса «Порядок».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.recognize.config import NO_CHAT
from src.recognize.fastpath import fast_path
from src.recognize.process_hint import ProcessHint, process_hint


def test_описание_и_процесс_через_связку_это_распознаются(domain_env: Path) -> None:
    итог = process_hint("Грязь на полке в горячем цехе, это порядок", chat_id=NO_CHAT)

    assert итог == ProcessHint(
        process="Порядок",
        said="Грязь на полке в горячем цехе",
        connective="это",
    )


def test_имя_процесса_из_двух_слов_распознаётся_целиком(domain_env: Path) -> None:
    итог = process_hint("Аптечка неполная, это охрана труда", chat_id=NO_CHAT)

    assert итог is not None
    assert итог.process == "Охрана труда"
    assert итог.said == "Аптечка неполная"


def test_имя_процесса_из_двух_слов_режимы_хранения(domain_env: Path) -> None:
    итог = process_hint("Шкаф не держит температуру, это режимы хранения", chat_id=NO_CHAT)

    assert итог is not None
    assert итог.process == "Режимы хранения"


def test_регистр_не_важен_капслок_как_у_владельца(domain_env: Path) -> None:
    итог = process_hint("ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ, ЭТО ПОРЯДОК", chat_id=NO_CHAT)

    assert итог is not None
    assert итог.process == "Порядок"
    assert итог.said == "ГРЯЗЬ НА ПОЛКЕ В ГОРЯЧЕМ ЦЕХЕ"
    assert итог.connective == "это"


@pytest.mark.parametrize("связка", ["=", "→"])
def test_связки_равно_и_стрелка_работают_так_же(domain_env: Path, связка: str) -> None:
    итог = process_hint(f"Грязь на полке в горячем цехе {связка} порядок", chat_id=NO_CHAT)

    assert итог is not None
    assert итог.process == "Порядок"
    assert итог.said == "Грязь на полке в горячем цехе"
    assert итог.connective == связка


@pytest.mark.parametrize("тире", ["-", "—", "–"])
def test_тире_связкой_не_считается(domain_env: Path, тире: str) -> None:
    """Хвост здесь — ровно имя процесса, и признак всё равно не срабатывает.

    Проверять надо именно так. Фраза с лишним словом после тире («— порядок
    навести») вернула бы `None` и с тире в списке связок, то есть про тире не
    сказала бы ничего: тест прошёл бы на сломанном правиле (проверено порчей).
    Тире стоит в обычных комментариях сплошь и рядом («нагар — жирные потёки»),
    и связкой оно не считается намеренно.
    """
    итог = process_hint(f"Грязь на полке в горячем цехе {тире} порядок", chat_id=NO_CHAT)

    assert итог is None


def test_упоминание_процесса_без_связки_не_признак(domain_env: Path) -> None:
    итог = process_hint("Плохой порядок на складе, нужно убрать", chat_id=NO_CHAT)

    assert итог is None


def test_лишнее_слово_в_хвосте_снимает_признак(domain_env: Path) -> None:
    итог = process_hint("Грязь на полке, это порядок на полке", chat_id=NO_CHAT)

    assert итог is None


def test_хвост_не_совпавший_ни_с_одним_процессом(domain_env: Path) -> None:
    итог = process_hint("Грязь на полке, это ерунда", chat_id=NO_CHAT)

    assert итог is None


def test_одна_связка_и_процесс_без_описания_это_none(domain_env: Path) -> None:
    итог = process_hint("это порядок", chat_id=NO_CHAT)

    assert итог is None


@pytest.mark.parametrize("note", ["", "   ", "\n\t "])
def test_пустая_строка_и_строка_из_пробелов(domain_env: Path, note: str) -> None:
    итог = process_hint(note, chat_id=NO_CHAT)

    assert итог is None


def test_имена_процессов_читаются_из_методики_а_не_зашиты(
    domain_env: Path, data_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный тест: он ловит попытку зашить список процессов в код.

    Переименовываем процесс «Порядок» в «Опрятность» прямо в CSV методики —
    новое имя обязано начать узнаваться, а старое перестать, без единой правки
    кода.
    """
    checklist = data_copy / "checklist.csv"
    переименованный = checklist.read_text(encoding="utf-8").replace("Порядок", "Опрятность")
    assert "Опрятность" in переименованный, "подмена имени процесса не сработала — правь фикстуру"
    checklist.write_text(переименованный, encoding="utf-8")
    monkeypatch.setenv("AUDIT_DATA_DIR", str(data_copy))

    старое_имя = process_hint("Грязь на полке в горячем цехе, это порядок", chat_id=NO_CHAT)
    новое_имя = process_hint("Грязь на полке в горячем цехе, это опрятность", chat_id=NO_CHAT)

    assert старое_имя is None, "старое имя процесса не должно узнаваться после переименования"
    assert новое_имя is not None
    assert новое_имя.process == "Опрятность"


def test_функция_ничего_не_перехватывает_обычный_разбор_идёт_как_раньше(
    domain_env: Path,
) -> None:
    """Признак — «в дополнение», а не вместо: `fast_path` на той же фразе не падает.

    Комментарий несёт и полноценную боевую находку («Печь: … нагар»), и хвост
    с указанием процесса. `process_hint` должен увидеть признак, а `fast_path` —
    по-прежнему отработать без исключений и вернуть свой обычный ответ (T113).
    """
    note = "Печь: под конвейерной лентой плотный серо-белый нагар, это порядок"

    признак = process_hint(note, chat_id=NO_CHAT)
    итог = fast_path(note, "hot_kitchen", chat_id=NO_CHAT)

    assert признак is not None, "признак на этой фразе должен сработать"
    assert итог.item is not None, итог.reason
    assert итог.item.code == "CLN05"


# --- связка на языке аудитора, а не только по-русски (T196, #161) -----------


def test_английская_связка_распознаётся_так_же_как_русская(domain_env: Path) -> None:
    """«…, this is tidiness» — то же указание словарю, что и «…, это порядок».

    Связка была задана русским выражением, то есть аудитор, ведущий проверку
    по-английски, не мог указать процесс вовсе — признак для него молчал всегда
    и молчал бесшумно, как отсутствие указаний.
    """
    итог = process_hint(
        "Crumbs on the shelf by the mixer, this is tidiness", lang="en", chat_id=NO_CHAT
    )

    assert итог is not None, "английская связка не распознана"
    assert итог.process == "Tidiness"
    assert итог.said == "Crumbs on the shelf by the mixer, this"
    assert итог.connective == "is"


def test_английское_имя_процесса_из_двух_слов(domain_env: Path) -> None:
    """Хвост сверяется по основам целиком — имя из двух слов не должно распадаться."""
    итог = process_hint(
        "The first aid kit is incomplete, this is labour safety", lang="en", chat_id=NO_CHAT
    )

    assert итог is not None
    assert итог.process == "Labour safety"


def test_английская_связка_без_имени_процесса_в_хвосте_не_признак(domain_env: Path) -> None:
    """«is» по-английски частотно — единственный ограничитель здесь хвост.

    Обычное предложение с «is» указанием словарю не является, и признак обязан
    молчать: иначе связка, добавленная ради второго языка, превратила бы
    инструмент сбора предложений для управляющей компании в шум.
    """
    assert process_hint("The oven is covered in soot", lang="en", chat_id=NO_CHAT) is None
    assert process_hint("This is not what we agreed", lang="en", chat_id=NO_CHAT) is None


def test_лишнее_слово_в_английском_хвосте_снимает_признак(domain_env: Path) -> None:
    """Правило хвоста одно на все языки: имя процесса и ничего больше."""
    assert (
        process_hint("Crumbs on the shelf, this is about tidiness", lang="en", chat_id=NO_CHAT)
        is None
    )


def test_русская_связка_продолжает_работать_после_добавления_английской(
    domain_env: Path,
) -> None:
    """Сторож: связки языков складываются, а не выбираются по параметру.

    Выбор по `lang` означал бы, что русский комментарий перестаёт разбираться,
    как только аудитор попросил английский отчёт, — молча и без единого отказа.
    Ровно та же причина, по которой складываются правила разбора слов (T192).
    """
    итог = process_hint("Грязь на полке в горячем цехе, это порядок", chat_id=NO_CHAT)

    assert итог is not None
    assert итог.connective == "это"


def test_связка_ищется_целым_словом_а_не_куском_другого(domain_env: Path) -> None:
    """«Поэтому порядок» — не указание словарю: связки в этой фразе нет вовсе.

    Буквы связки встречаются внутри обычных слов: «поэтому» содержит «это»,
    английское «premises» — «is». Без границ слова признак нашёл бы связку
    внутри слова, отрезал бы хвост посреди него — «му порядок», «es tidiness» —
    и обе фразы дали бы полноценное срабатывание, потому что хвост после
    отсечения совпадает с именем процесса ровно.

    Защита была в коде с самого начала (T166), но проверялась только тем, что
    ни один тест на неё не наступал. Порча при сдаче T196 это показала: снятие
    границ слова не красило ни одного теста.
    """
    assert process_hint("Поэтому порядок", chat_id=NO_CHAT) is None
    assert process_hint("Premises tidiness", lang="en", chat_id=NO_CHAT) is None
