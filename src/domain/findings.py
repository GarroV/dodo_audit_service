"""Операции над записями проверки: фиксация, правка, удаление, фотографии.

Инварианты методики — уникальность пары «пункт + зона», допустимые классы,
существование зоны — проверяет движок, и здесь они не повторяются: две копии
одного правила расходятся, и расхождение видно только в отчёте у партнёра. Дело
блока — не дать отказу потеряться и убедиться, что после успешного кода работа
действительно сделана.
"""

from __future__ import annotations

import re

from .config import Settings, check_environment
from .engine import option, run_audit, state_file
from .errors import EngineError, ValidationError
from .models import SOURCES, Finding, Suggestion
from .state import (
    check_confidence,
    forget_source,
    forget_suggestion,
    forget_words,
    read_state,
    remember_source,
    remember_suggestion,
    remember_words,
)

#: Номер записи в ответе движка: «#3 CLN05 D1 / hot_kitchen: …».
NUMBER = re.compile(r"#(\d+)")

#: Имена полей контракта → ключи движка. Формулировка у движка называется
#: `evidence`, код пункта — `qid`; наружу блок отдаёт имена контракта.
FIELD_OPTIONS = {
    "code": "qid",
    "level": "level",
    "zone": "zone",
    "text": "evidence",
    "comment": "comment",
}


def _number(out: str, command: str) -> int:
    hit = NUMBER.search(out)
    if hit is None:
        raise EngineError(
            f"Движок не назвал номер записи. Ответ: {out.strip()!r}",
            code=0,
            command=command,
        )
    return int(hit.group(1))


def _finding_after(chat_id: int, settings: Settings, n: int, what: str) -> Finding:
    """Прочитать запись после операции. Нет её — успех был ненастоящим."""
    state = read_state(chat_id, settings)
    found = state.finding(n) if state is not None else None
    if found is None:
        raise EngineError(
            f"Движок отчитался об успехе, но записи #{n} в состоянии нет ({what})",
            code=0,
            command=what,
        )
    return found


def add_finding(
    chat_id: int,
    code: str,
    level: str,
    zone: str,
    text: str,
    *,
    comment: str = "",
    source: str = "",
    words: str = "",
    suggested: Suggestion | None = None,
) -> Finding:
    """Зафиксировать запись.

    Отказ движка — пара «пункт + зона» уже занята, класс не разрешён для пункта,
    зоны нет в справочнике — уходит наружу `EngineError` с его же текстом:
    аудитору в чате показывают именно его.

    `source` — откуда взялась запись (решение D044): со слов аудитора
    (`SOURCE_COMMENT`) или распознано по кадру (`SOURCE_PHOTO`). Ставится при
    фиксации, а не отдельным вызовом следом: запись без источника, которую
    забыли пометить вторым шагом, выглядит потом как слова аудитора.

    `suggested` — что предложила система ДО того, как аудитор нажал кнопку
    (решение D077, задача T181). Ставится здесь по той же причине, что и
    источник, и по ещё одной: момент фиксации — единственный, когда предложение
    и запись существуют одновременно. Дальше запись правится (`edit_finding`), а
    предложение остаётся прежним — их РАЗНИЦА и есть то, ради чего сигнал
    сохраняется. Отдельной копии «что аудитор поправил» не заводится: она
    разъехалась бы с парой, из которой считается, молча.

    Пусто — система не предлагала ничего (запись заведена вручную). Это не то же
    самое, что «предложила ровно это»: там тройка заполнена и совпадает.

    `words` — сырые слова аудитора, из которых эта запись выросла (задача T183).
    Ставятся здесь и только здесь: слова живут у бота один момент — пока идёт
    разбор материала, — а в текст записи они не попадают намеренно. Правка
    записи их не трогает (`edit_finding`), и это то же правило, что у
    предложения: разница между сказанным и записанным и есть весь сигнал.
    """
    settings = check_environment()
    # До вызова движка: иначе запись есть, а источник у неё неизвестно какой.
    if source and source not in SOURCES:
        raise ValidationError(
            f"Источник записи «{source}» не из {SOURCES}: запись появляется либо со слов "
            f"аудитора, либо распознаванием по кадру"
        )
    # По тому же правилу и в том же месте: разобранное после движка оставило бы
    # запись без предложения, а вызывающий об этом не узнал бы — сигнал о промахе
    # потерялся бы ровно там, где его собирались собирать.
    if suggested is not None:
        if not suggested.code:
            raise ValidationError(
                "В предложении системы не назван пункт методики. Предложение без пункта "
                "сравнивать с записью не с чем, а в базе оно неотличимо от «не предлагала»"
            )
        check_confidence(suggested.confidence, "в предложении к новой записи")
    out = run_audit(
        [
            "add",
            option("qid", code),
            option("level", level),
            option("zone", zone),
            option("evidence", text),
            option("comment", comment),
        ],
        chat_id=chat_id,
        settings=settings,
    )
    n = _number(out, "add")
    path = state_file(chat_id, settings)
    remember_source(path, n, source)
    remember_words(path, n, words)
    remember_suggestion(path, n, suggested)
    return _finding_after(chat_id, settings, n, "add")


def edit_finding(chat_id: int, n: int, **fields: str) -> Finding:
    """Поправить запись: `code`, `level`, `zone`, `text`, `comment`.

    Меняются только переданные поля. Пустой вызов и незнакомое имя поля — отказ:
    молча ничего не сделать здесь хуже всего, аудитор уйдёт с ошибкой в отчёте.
    """
    settings = check_environment()
    unknown = sorted(set(fields) - set(FIELD_OPTIONS))
    if unknown:
        raise ValidationError(
            f"Неизвестные поля записи: {', '.join(unknown)}. "
            f"Менять можно: {', '.join(sorted(FIELD_OPTIONS))}"
        )
    if not fields:
        raise ValidationError(
            f"Нечего менять в записи #{n}: укажите хотя бы одно из "
            f"{', '.join(sorted(FIELD_OPTIONS))}"
        )
    args = ["edit", option("n", str(n))]
    args += [option(FIELD_OPTIONS[name], value) for name, value in sorted(fields.items())]
    run_audit(args, chat_id=chat_id, settings=settings)
    return _finding_after(chat_id, settings, n, "edit")


def drop_finding(chat_id: int, n: int) -> None:
    """Удалить запись. Удалять нечего — отказ, а не тихий успех."""
    settings = check_environment()
    run_audit(["drop", str(n)], chat_id=chat_id, settings=settings)
    path = state_file(chat_id, settings)
    forget_source(path, n)
    # Номер после удаления освобождается, и оставленное предложение досталось бы
    # следующей записи — то есть в базу уехало бы предложение к чужой записи.
    forget_suggestion(path, n)
    # По той же причине снимаются и слова аудитора (T183): в выборке они
    # выглядели бы речью человека о нарушении, которого он не описывал.
    forget_words(path, n)
    state = read_state(chat_id, settings)
    if state is not None and state.finding(n) is not None:
        raise EngineError(
            f"Движок отчитался об удалении, но запись #{n} на месте", code=0, command="drop"
        )


def attach_photo(chat_id: int, n: int, file_id: str) -> None:
    """Прикрепить кадр к записи.

    В боте кадр хранится идентификатором телеграма и скачивается только на
    сборке отчёта. Запятая в идентификаторе запрещена не из вредности: движок
    режет по ней список, и один кадр молча превратился бы в два несуществующих.
    """
    settings = check_environment()
    photo = file_id.strip()
    if not photo:
        raise ValidationError(f"Пустой идентификатор кадра для записи #{n}")
    if "," in photo:
        raise ValidationError(
            f"В идентификаторе кадра запятая: «{photo}». Движок разрежет его по ней на два кадра"
        )
    run_audit(["photo", str(n), option("add", photo)], chat_id=chat_id, settings=settings)
    after = _finding_after(chat_id, settings, n, "photo")
    if photo not in after.photos:
        raise EngineError(
            f"Движок отчитался об успехе, но кадра нет в записи #{n}", code=0, command="photo"
        )
