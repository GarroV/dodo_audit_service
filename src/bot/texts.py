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
        "ru": "Кадр принят. Комментарий — следом сообщением или ответом на этот кадр.",
        "en": "Photo received. Comment it in the next message or as a reply to this photo.",
    },
    "material.album_taken": {
        "ru": "Альбом принят. Кадров: {count}. Комментарий — следом сообщением или ответом.",
        "en": "Album received. Photos: {count}. Comment in the next message or as a reply.",
    },
    "material.linked": {
        "ru": "Связано. Кадров: {count}. Комментарий: «{comment}».",
        "en": "Linked. Photos: {count}. Comment: “{comment}”.",
    },
    "material.linked_voice": {
        "ru": "Связано. Кадров: {count}. Комментарий голосовой.",
        "en": "Linked. Photos: {count}. Voice comment.",
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
}


def t(key: str, lang: str, **params: object) -> str:
    """Взять текст по ключу на нужном языке и подставить параметры.

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
