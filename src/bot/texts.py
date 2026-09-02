"""Тексты интерфейса бота: каталог по ключам, язык — параметр.

Принцип проекта «язык — параметр, никогда не константа» держится не обещанием,
а формой: в хендлерах строк нет вовсе, есть `t(key, lang)`. Три языка разведены
(`docs/06-mvp-bot.md`): язык интерфейса живёт здесь, язык речи аудитора — в
разборе, язык отчёта — в шапке движка. Сейчас интерфейс русский, но добавление
языка — строки в каталоге, а не правка хендлеров.

Неизвестный язык — отказ, а не откат на русский: тем же правилом живёт методика
(`src/domain/models.py: pick_text`), и расхождение поведения было бы хуже
самого отказа.

Множественное число нарочно обходится формой фразы («Кадров: 3», а не
«3 кадра»): правила согласования у русского и английского разные, и таблица
склонений ради двух строк — это движок локализации, которого в блоке не должно
быть.
"""

from __future__ import annotations

from .errors import BotTextError

#: Языки интерфейса. Третий добавляется строками в каталоге ниже, не кодом.
UI_LANGS = ("ru", "en")

#: Язык интерфейса по умолчанию — до того, как проверка начата и у неё
#: появилось собственное поле `ui_lang`.
DEFAULT_UI_LANG = "ru"

TEXTS: dict[str, dict[str, str]] = {
    # --- вход и мастер начала проверки (T050, T051, T063) ---
    "start.greeting": {
        "ru": "Бот выездных проверок. Нажмите «Новая проверка», чтобы начать.",
        "en": "Field audit bot. Tap “New inspection” to begin.",
    },
    "start.ask_unit": {
        "ru": "Название пиццерии? Введите текстом.",
        "en": "Which pizzeria? Type the name.",
    },
    "start.unit_empty": {
        "ru": "Название пиццерии пустое. Введите текстом, как оно должно стоять в отчёте.",
        "en": "The name is empty. Type it as it should appear in the report.",
    },
    "start.unit_expected": {
        "ru": "Жду название пиццерии текстом.",
        "en": "Waiting for the pizzeria name as text.",
    },
    "start.ask_kind": {
        "ru": "Вид проверки?",
        "en": "Inspection type?",
    },
    "start.ask_lang": {
        "ru": "Язык отчёта?",
        "en": "Report language?",
    },
    "start.started": {
        "ru": (
            "Проверка начата.\n"
            "Пиццерия: {unit}\n"
            "Вид: {kind}\n"
            "Язык отчёта: {lang}\n"
            "Проверяющий: {auditor}\n"
            "Дата: {date}\n\n"
            "Присылайте фотографии с комментариями."
        ),
        "en": (
            "Inspection started.\n"
            "Pizzeria: {unit}\n"
            "Type: {kind}\n"
            "Report language: {lang}\n"
            "Auditor: {auditor}\n"
            "Date: {date}\n\n"
            "Send photos with comments."
        ),
    },
    "start.failed": {
        "ru": "Не получилось начать проверку: {reason}",
        "en": "Could not start the inspection: {reason}",
    },
    # --- незавершённая проверка (T052) ---
    "start.resume_found": {
        "ru": (
            "В этом чате есть незавершённая проверка.\n"
            "Пиццерия: {unit}\n"
            "Дата: {date}\n"
            "Проверяющий: {auditor}\n"
            "Записей: {findings}\n\n"
            "Продолжить её или начать новую? Новая сотрёт эту."
        ),
        "en": (
            "This chat has an unfinished inspection.\n"
            "Pizzeria: {unit}\n"
            "Date: {date}\n"
            "Auditor: {auditor}\n"
            "Records: {findings}\n\n"
            "Continue it or start a new one? A new one erases this."
        ),
    },
    "start.resumed": {
        "ru": "Продолжаем: {unit}, {date}. Записей: {findings}.",
        "en": "Continuing: {unit}, {date}. Records: {findings}.",
    },
    "start.resume_gone": {
        "ru": "Продолжать нечего: проверки в этом чате уже нет. Начните новую.",
        "en": "Nothing to continue: this chat has no inspection any more. Start a new one.",
    },
    # --- приём материала (T053, T054, T059) ---
    "material.no_inspection": {
        "ru": "Проверка не начата. Нажмите «Новая проверка».",
        "en": "No inspection started. Tap “New inspection”.",
    },
    "material.photo_taken": {
        "ru": "Кадр принят. Разобрать? Или пришлите комментарий — по словам точнее.",
        "en": "Photo received. Analyze it? Or send a comment — your words are more precise.",
    },
    "material.album_taken": {
        "ru": "Альбом принят. Кадров: {count}. Разобрать? Или пришлите комментарий.",
        "en": "Album received. Photos: {count}. Analyze? Or send a comment.",
    },
    "material.no_photo": {
        "ru": (
            "Не вижу кадра, к которому это относится. "
            "Пришлите фотографию, а комментарий — следом или ответом на неё."
        ),
        "en": (
            "I see no photo this refers to. "
            "Send a photo, then the comment — next message or as a reply to it."
        ),
    },
    # --- разбор и фиксация (T055, T057, T067) ---
    # Текст покрывает оба случая: кадр забрал комментарий (D046) и второе
    # нажатие той же кнопки. Называть причину «комментарий» нельзя — при
    # двойном нажатии это была бы неправда.
    "record.analyze_gone": {
        "ru": "Этот кадр уже разбирается — вопрос по нему снят.",
        "en": "This photo is already being analyzed — the question is off.",
    },
    "record.thinking": {
        "ru": "Разбираю…",
        "en": "Analyzing…",
    },
    "record.heard": {
        "ru": "Услышал: «{note}»",
        "en": "Heard: “{note}”",
    },
    "record.voice_failed": {
        "ru": "Голосовое не разобрал: {reason} Напишите комментарий текстом.",
        "en": "Could not transcribe the voice message: {reason} Please type the comment.",
    },
    "record.candidates": {
        "ru": "Что записать? Кадров: {count}.\n\n{lines}",
        "en": "What should I record? Photos: {count}.\n\n{lines}",
    },
    "record.candidate_line": {
        "ru": "{index}. {code} · {level} · {zone}\n   {wording}",
        "en": "{index}. {code} · {level} · {zone}\n   {wording}",
    },
    "record.candidate_flagged": {
        "ru": "{index}. {code} · {level} · {zone}  ⚠ проверьте формулировку\n   {wording}",
        "en": "{index}. {code} · {level} · {zone}  ⚠ check the wording\n   {wording}",
    },
    "record.question": {
        "ru": "Уточните: {question}",
        "en": "Please clarify: {question}",
    },
    "record.nothing_found": {
        "ru": "По этому материалу пункт не подобрался. Выберите сами или уточните словами.",
        "en": "No item matched this material. Pick one yourself or clarify in words.",
    },
    "record.degraded": {
        "ru": "Модель недоступна ({reason}) — проверка продолжается, пункт выберите сами.",
        "en": "The model is unavailable ({reason}) — the inspection goes on, pick the item.",
    },
    "record.unavailable": {
        "ru": "Разбор недоступен: {reason}",
        "en": "Analysis is unavailable: {reason}",
    },
    "record.manual_page": {
        "ru": "Пункты чек-листа, страница {page} из {pages}:",
        "en": "Checklist items, page {page} of {pages}:",
    },
    "record.ask_level": {
        "ru": "Какой класс для {code}?",
        "en": "Which class for {code}?",
    },
    "record.ask_zone": {
        "ru": "В какой зоне это? Из слов зону не видно — назовите её кнопкой.",
        "en": "Which zone is this? Your words do not name it — pick it with a button.",
    },
    "record.stale": {
        "ru": "Предложение устарело — бот перезапускался. Пришлите кадр заново.",
        "en": "This suggestion is stale — the bot restarted. Send the photo again.",
    },
    "record.skipped": {
        "ru": "Не записал. Кадр не потеряется — покажу его при завершении проверки.",
        "en": "Not recorded. The photo is not lost — I will list it when you finish.",
    },
    "record.saved": {
        "ru": "#{n} {code} · {level} · {zone} · {pct}%",
        "en": "#{n} {code} · {level} · {zone} · {pct}%",
    },
    "record.saved_info": {
        "ru": "#{n} {code} · {level} замер · {zone} · {pct}%",
        "en": "#{n} {code} · {level} measurement · {zone} · {pct}%",
    },
    "record.zone_unknown": {
        "ru": "зона не названа",
        "en": "zone not named",
    },
    "record.voice_not_downloaded": {
        "ru": "Голосовое не скачалось. Повторите или напишите комментарий текстом.",
        "en": "The voice message did not download. Retry or type the comment.",
    },
    "record.zone_unusual": {
        "ru": " ⚠ зона не из списка пункта",
        "en": " ⚠ zone is not on the item’s list",
    },
    "record.failed": {
        "ru": "Не записал: {reason}",
        "en": "Not recorded: {reason}",
    },
    # --- правки записи прямо в чате (T056) ---
    "edit.ask_zone": {
        "ru": "Новая зона для записи #{n}?",
        "en": "New zone for record #{n}?",
    },
    "edit.ask_level": {
        "ru": "Новый класс для записи #{n}?",
        "en": "New class for record #{n}?",
    },
    "edit.ask_text": {
        "ru": "Пришлите новую формулировку для записи #{n} одним сообщением.",
        "en": "Send the new wording for record #{n} in one message.",
    },
    "edit.changed": {
        "ru": "Поправлено. #{n} {code} · {level} · {zone} · {pct}%",
        "en": "Updated. #{n} {code} · {level} · {zone} · {pct}%",
    },
    "edit.changed_info": {
        "ru": "Поправлено. #{n} {code} · {level} замер · {zone} · {pct}%",
        "en": "Updated. #{n} {code} · {level} measurement · {zone} · {pct}%",
    },
    "edit.dropped": {
        "ru": "Запись #{n} удалена. Накоплено: {pct}%",
        "en": "Record #{n} deleted. Score now: {pct}%",
    },
    "edit.text_dropped": {
        "ru": "Вопрос про формулировку записи #{n} снят — вы вернулись к работе.",
        "en": "The wording question for record #{n} is off — you are back to work.",
    },
    "edit.nothing_to_undo": {
        "ru": "Удалять нечего: в проверке ни одной записи.",
        "en": "Nothing to undo: the inspection has no records.",
    },
    "edit.gone": {
        "ru": "Записи #{n} уже нет.",
        "en": "Record #{n} no longer exists.",
    },
    "edit.failed": {
        "ru": "Не поправил: {reason}",
        "en": "Not updated: {reason}",
    },
    # --- завершение проверки (T058, T068) ---
    "finish.summary": {
        "ru": "Итог: {pct}% — {grade}, {label}.\nЗаписей: {total}. {counts}",
        "en": "Result: {pct}% — {grade}, {label}.\nRecords: {total}. {counts}",
    },
    "finish.records": {
        "ru": "Зафиксировано:\n{lines}",
        "en": "Recorded:\n{lines}",
    },
    "finish.record_line": {
        "ru": "#{n} {code} · {level} · {zone}{source} — {text}",
        "en": "#{n} {code} · {level} · {zone}{source} — {text}",
    },
    "finish.source_photo": {
        "ru": " · по кадру",
        "en": " · from the photo",
    },
    "finish.empty": {
        "ru": "Ни одной записи не зафиксировано.",
        "en": "No records have been made.",
    },
    "finish.unclaimed": {
        "ru": "Кадры без записи — {count}. Они никуда не пропали, но и в отчёт не войдут:\n{lines}",
        "en": "Photos with no record — {count}. Not lost, but they will not be in the report:\n"
        "{lines}",
    },
    "finish.unclaimed_line": {
        "ru": "— кадр из сообщения {message_id}",
        "en": "— photo from message {message_id}",
    },
    "finish.ask": {
        "ru": "Поправить запись или собирать отчёт?",
        "en": "Edit a record, or build the report?",
    },
    "finish.pick_edit": {
        "ru": "Какую запись поправить?",
        "en": "Which record do you want to edit?",
    },
    "finish.building": {
        "ru": "Собираю отчёт…",
        "en": "Building the report…",
    },
    "finish.letter": {
        "ru": "Письмо партнёру:\n\n{letter}",
        "en": "Letter to the partner:\n\n{letter}",
    },
    "finish.photos_missing": {
        "ru": "{reason}",
        "en": "{reason}",
    },
    "finish.pdf_failed": {
        "ru": "Отчёт не собрался: {reason}",
        "en": "The report was not built: {reason}",
    },
    "finish.resumed": {
        "ru": "Продолжаем проверку. Присылайте кадры.",
        "en": "Back to the inspection. Send photos.",
    },
    # --- надписи на кнопках ---
    "btn.analyze": {"ru": "Разобрать", "en": "Analyze"},
    "btn.manual": {"ru": "Выбрать пункт", "en": "Pick an item"},
    "btn.skip": {"ru": "Не записывать", "en": "Skip"},
    "btn.more": {"ru": "Дальше", "en": "Next"},
    "btn.back": {"ru": "Назад", "en": "Back"},
    "btn.zone": {"ru": "Зона", "en": "Zone"},
    "btn.level": {"ru": "Класс", "en": "Class"},
    "btn.text": {"ru": "Формулировка", "en": "Wording"},
    "btn.drop": {"ru": "Удалить", "en": "Delete"},
    "btn.build": {"ru": "Собрать отчёт", "en": "Build the report"},
    "btn.build_without_photos": {"ru": "Собрать без кадров", "en": "Build without photos"},
    "btn.edit": {"ru": "Поправить запись", "en": "Edit a record"},
    "btn.resume": {"ru": "Продолжить проверку", "en": "Continue the inspection"},
}


def t(key: str, lang: str, /, **params: object) -> str:
    """Взять текст по ключу на нужном языке и подставить параметры.

    Ключ и язык — только позиционные: у текстов есть параметр `{lang}` (язык
    отчёта в подтверждении старта), и без этого он столкнулся бы с языком
    интерфейса на ровном месте.

    Отказ вместо подстановки по умолчанию во всех трёх случаях: нет ключа, нет
    языка, не хватает параметра. Показать аудитору «Записей: {findings}» хуже,
    чем упасть на тесте.
    """
    entry = TEXTS.get(key)
    if entry is None:
        raise BotTextError(f"Текста «{key}» нет в каталоге src/bot/texts.py")
    if lang not in UI_LANGS:
        raise BotTextError(f"Язык интерфейса «{lang}» не заведён. Доступны: {', '.join(UI_LANGS)}")
    template = entry[lang]
    try:
        return template.format(**params)
    except KeyError as exc:
        raise BotTextError(f"Тексту «{key}» не передан параметр {exc.args[0]}") from exc


def ui_lang_or_default(lang: str | None) -> str:
    """Язык интерфейса проверки, если он известен и поддержан, иначе умолчание.

    Нужно ровно там, где проверки ещё нет (мастер начала): подставлять язык
    несуществующего состояния неоткуда, а падать на приветствии нельзя.
    """
    return lang if lang in UI_LANGS else DEFAULT_UI_LANG
