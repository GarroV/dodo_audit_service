"""Тексты интерфейса бота: каталог по ключам, язык — параметр.

Принцип проекта «язык — параметр, никогда не константа» держится не обещанием,
а формой: в хендлерах строк нет вовсе, есть `t(key, lang)`. Три языка разведены
(`docs/06-mvp-bot.md`): язык интерфейса живёт здесь, язык речи аудитора — в
разборе, язык отчёта — в шапке движка. Добавление языка — строки в каталоге, а
не правка хендлеров.

Начатая проверка язык интерфейса знает сама (`Inspection.ui_lang`), а до её
начала брать его неоткуда — и до T131 он был здесь зашит русским. Теперь это
параметр развёртывания, `BOT_UI_LANG`: боевой стенд молчит и получает прежний
русский, демо ставит `en` и становится английским целиком, включая приветствие
и мастер. Переменная читается по месту, а не раздаётся аргументом через все
роутеры: язык до начала проверки — свойство стенда, один на процесс, и
протаскивать его пятью параметрами значило бы делать вид, что он бывает разным
у разных чатов одного бота.

Неизвестный язык — отказ, а не откат на русский: тем же правилом живёт методика
(`src/domain/models.py: pick_text`), и расхождение поведения было бы хуже
самого отказа. Молчаливый откат здесь стоил бы дороже всего: опечатка в
переменной демо-стенда вернула бы показ на русский, и заметили бы это на самом
показе.

Множественное число нарочно обходится формой фразы («Кадров: 3», а не
«3 кадра»): правила согласования у русского и английского разные, и таблица
склонений ради двух строк — это движок локализации, которого в блоке не должно
быть.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from .errors import BotTextError

#: Языки интерфейса. Третий добавляется строками в каталоге ниже, не кодом.
UI_LANGS = ("ru", "en")

#: Язык интерфейса стенда — до того, как проверка начата и у неё появилось
#: собственное поле `ui_lang` (T131). Имя в духе соседнего `BOT_MODE`.
UI_LANG_VAR = "BOT_UI_LANG"

#: Предел текста всплывающего окна Telegram: `answerCallbackQuery`, поле `text`,
#: 0-200 ЗНАКОВ по документации Bot API (не байтов — здесь Telegram считает
#: иначе, чем ext4 в пределах имени файла отчёта, и путать их нельзя).
#:
#: Окно — единственный канал ответа на нажатие старше 48 часов (T134): сообщения
#: у такого нажатия нет, отвечать в чат некуда. Режет Telegram молча, поэтому
#: предел стережётся тестом, а не памятью того, кто правит текст.
ALERT_TEXT_LIMIT = 200

#: Чем стенд говорит, пока переменная не задана. Это не «язык продукта», а
#: совместимость: боевой бот работает по-русски со дня T050, и менять ему язык
#: молча, задним числом, не просил никто.
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
    # Предел не вкусовой, а замеренный (T128): имя файла отчёта собирается как
    # «Аудит <точка> - <аудитор> - <дата>.pdf», кириллица в UTF-8 стоит два
    # байта на знак, а имя файла на ext4 (площадка продукта, D053) — 255 байт.
    # На 100 знаках названия имя уже 258 байт, и сборка отчёта падает с «File
    # name too long» в самом конце проверки, когда исправлять поздно.
    "start.unit_too_long": {
        "ru": (
            "Название длиннее {limit} знаков не влезет ни в шапку отчёта, ни в имя файла. "
            "Пришлите короче — так, как оно должно стоять в отчёте."
        ),
        "en": (
            "A name longer than {limit} characters fits neither the report header nor the "
            "file name. Send a shorter one — as it should appear in the report."
        ),
    },
    # Тот же предел, но по факту, а не по знакам (T128, issue #103): 60
    # эмодзи — те же 60 знаков (в предел выше укладываются), но уже 240 байт,
    # то есть одно название съедает больше, чем весь бюджет имени файла.
    # Аудитору говорим не про байты — про то, что делать: короче название,
    # меньше «тяжёлых» знаков.
    "start.unit_too_long_bytes": {
        "ru": (
            "Название слишком длинное для имени файла отчёта — в нём слишком много "
            "непростых знаков (например, эмодзи). Пришлите короче или замените часть "
            "знаков обычными буквами."
        ),
        "en": (
            "The name is too long for the report file name — it has too many heavy "
            "characters (emoji, for example). Send a shorter one, or replace some "
            "characters with plain letters."
        ),
    },
    "start.unit_expected": {
        "ru": "Жду название пиццерии текстом.",
        "en": "Waiting for the pizzeria name as text.",
    },
    "start.ask_kind": {
        "ru": "Вид проверки?",
        "en": "Inspection type?",
    },
    # Вопрос один, а языков в проверке три (`docs/06-mvp-bot.md`). Ответ
    # ложится во все три, и сказать об этом надо здесь: аудитор выбирает не
    # только язык документа для партнёра, но и язык, на котором с ним говорят.
    "start.ask_lang": {
        "ru": "Язык проверки? На нём будут и отчёт, и наш разговор.",
        "en": "Inspection language? Both the report and this chat will use it.",
    },
    "start.started": {
        "ru": (
            "Проверка начата.\n"
            "Пиццерия: {unit}\n"
            "Вид: {kind}\n"
            "Язык отчёта: {lang}\n"
            "Проверяющий: {auditor}\n"
            "{auditor_note}"
            # Хвоста «Присылайте фотографии с комментариями» здесь больше нет:
            # его место занял абзац `material.photo_required` (T160, D078), и
            # он говорит то же самое, но правилом — с порядком и с тем, что без
            # кадра записи не будет. Дописывает его `with_photo_rule`.
            "Дата: {date}"
        ),
        "en": (
            "Inspection started.\n"
            "Pizzeria: {unit}\n"
            "Type: {kind}\n"
            "Report language: {lang}\n"
            "Auditor: {auditor}\n"
            "{auditor_note}"
            "Date: {date}"
        ),
    },
    # Обрезка имени аудитора по байтам молча не уезжает (T128, issue #103):
    # имя приходит из профиля Telegram, а не от аудитора в этот момент, и
    # отказать нельзя — значит, о подмене надо сказать отдельной строкой в
    # `start.started`. `{auditor_note}` рядом пуст, когда обрезки не было —
    # перевод строки этот текст несёт с собой сам (см. `routers/start.py`),
    # каталог им не заведует.
    "start.auditor_name_shortened": {
        "ru": "Имя в профиле длиннее — для отчёта и имени файла оно сокращено.",
        "en": "The profile name is longer — it was shortened for the report and the file name.",
    },
    # Тот же запрет, что и у отказа сборки, только на входе в проверку:
    # движок отвечает вызывающему из командной строки и присылает полный стек
    # с путями к своим файлам, а пересказ отдавал его аудитору дословно.
    "start.failed": {
        "ru": (
            "Не получилось начать проверку. Попробуйте ещё раз командой /start. Если не "
            "выйдет и со второго раза, скажите администратору: подробности в журнале."
        ),
        "en": (
            "Could not start the inspection. Try again with /start. If it fails a second "
            "time, tell the administrator: the details are in the log."
        ),
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
    # Та же развилка, но по сданной проверке (T153). Прежняя фраза утверждала
    # о ней две неправды разом: что она незавершённая и что новая её сотрёт.
    # Признака «завершена» у движка нет, и выдумывать его бот не вправе — зато
    # он помнит, что сам собрал и отдал отчёт, и говорит ровно это.
    "start.resume_handed_over": {
        "ru": (
            "Проверка в этом чате уже сдана: отчёт по ней собран и отправлен.\n"
            "Пиццерия: {unit}\n"
            "Дата: {date}\n"
            "Проверяющий: {auditor}\n"
            "Записей: {findings}\n\n"
            "Дописать в неё нельзя: отчёт уже у получателя и в истории точки, "
            "а дописанное в него не попадёт. Начните новую проверку — или "
            "уберите эту из чата, если она тут больше не нужна."
        ),
        "en": (
            "The inspection in this chat is already handed over: its report was built "
            "and sent.\n"
            "Pizzeria: {unit}\n"
            "Date: {date}\n"
            "Auditor: {auditor}\n"
            "Records: {findings}\n\n"
            "It can no longer be added to: the report is already with its recipient "
            "and in the unit’s history, and anything added now would not reach it. "
            "Start a new inspection — or remove this one from the chat if you no "
            "longer need it here."
        ),
    },
    # Отказ на любую попытку изменить сданную проверку (T201, D080). Один текст
    # на все входы: аудитор упирается в запрет то кадром, то кнопкой, и разные
    # слова об одном и том же читались бы как разные запреты.
    "sealed.blocked": {
        "ru": (
            "Эта проверка сдана — отчёт по ней собран, отправлен и уехал в историю точки. "
            "Дописывать и править её нельзя: у получателя на руках другой документ, "
            "и та же проверка встала бы в историю второй строкой.\n\n"
            "Начните новую проверку — или уберите сданную из чата."
        ),
        "en": (
            "This inspection is handed over — its report was built, sent and archived in "
            "the unit’s history. It can be neither extended nor edited: the recipient "
            "holds a different document, and the same inspection would land in the "
            "history a second time.\n\n"
            "Start a new inspection — or remove the handed-over one from the chat."
        ),
    },
    # Проверка убрана из чата. Говорится ровно то, что произошло: убрана копия
    # в чате, а не отчёт и не строка в истории — их бот удалять не умеет, и
    # промолчать об этом значило бы дать понять, что удалено всё.
    "sealed.dropped": {
        "ru": (
            "Убрал сданную проверку из чата. Отданный отчёт и её строка в истории точки "
            "остаются — их бот не удаляет; если нужно убрать и оттуда, скажите "
            "администратору.\n\n"
            "Чат свободен: можно начинать новую проверку."
        ),
        "en": (
            "The handed-over inspection is removed from this chat. The delivered report "
            "and its row in the unit’s history stay — the bot does not delete those; if "
            "they must go too, tell the administrator.\n\n"
            "The chat is free: you can start a new inspection."
        ),
    },
    "sealed.drop_gone": {
        "ru": "Убирать нечего: проверки в этом чате уже нет. Можно начинать новую.",
        "en": "Nothing to remove: this chat has no inspection any more. You can start a new one.",
    },
    "start.resumed": {
        "ru": "Продолжаем: {unit}, {date}. Записей: {findings}.",
        "en": "Continuing: {unit}, {date}. Records: {findings}.",
    },
    # Состояние проверки не читается (T126). Причина и выход — в чат, разбор с
    # путями и текстом исключения — в журнал: аудитору на точке он ничего не
    # объясняет, а путь к файлу партнёрской проверки в переписке ему не место.
    "start.state_broken": {
        "ru": (
            "Не могу прочитать проверку этого чата — файл состояния повреждён, "
            "и продолжить её нечем. Подробности записаны в журнал.\n\n"
            "«Новая проверка» начнёт с чистого листа. Всё, что было в повреждённой, "
            "бот вернуть не сможет — если она важна, скажите об этом до того, как начнёте."
        ),
        "en": (
            "I cannot read this chat’s inspection — the state file is damaged, and there "
            "is nothing to continue. The details are in the log.\n\n"
            "“New inspection” starts from scratch. Whatever was in the damaged one is "
            "beyond the bot’s reach — if it matters, say so before you start."
        ),
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
    # Фотофиксация обязательна ВСЕГДА (T160, решение D078, отменившее вчерашнее
    # D071). Правило живёт отдельной строкой, потому что звучит в двух местах:
    # в начале проверки — заранее, и в отказе — по факту. Две копии одной мысли
    # разъезжаются молча: в отказе поправили, в приветствии забыли, и человек
    # читает в одном продукте два разных правила. Собирает обе `with_photo_rule`.
    "material.photo_required": {
        "ru": (
            "Фотофиксация обязательна: записи без кадра не бывает. "
            "Сначала фотография, следом комментарий к ней — подписью, "
            "отдельным сообщением или ответом на кадр."
        ),
        "en": (
            "A photo is required: there is no record without one. "
            "The photo comes first, the comment for it follows — as a caption, "
            "as a separate message, or as a reply to the photo."
        ),
    },
    # Отказ на комментарий без кадра. Раньше он начинался с «Не вижу кадра, к
    # которому это относится» — с жалобы продукта на себя, — и человек на точке
    # выносил из неё «бот сломался», а не «так устроена проверка» (T160). Теперь
    # это факт о записи, а правило под ним объясняет порядок.
    "material.no_photo": {
        "ru": "Один комментарий записью не станет — к нему нужен кадр.",
        "en": "A comment on its own does not become a record — it needs a photo.",
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
    # Тот же перечень, но собранный по ПРАВКЕ записи (T204). Спрашивать «что
    # записать» здесь нельзя: аудитор ответил на запись, чтобы её поправить, а
    # прочитал бы вопрос о новой — и, нажав кнопку, ждал бы второй строки в
    # отчёте. Число кадров тут тоже ни при чём: кадры остались у записи.
    "record.candidates_correcting": {
        "ru": "Чем поправить запись #{n}?\n\n{lines}",
        "en": "What should record #{n} become?\n\n{lines}",
    },
    "record.manual_page_correcting": {
        "ru": "Пункты чек-листа для записи #{n}, страница {page} из {pages}:",
        "en": "Checklist items for record #{n}, page {page} of {pages}:",
    },
    # Запись, легшая по словам аудитора сразу, без подтверждения (T121, D064).
    # Владелец, дословно: «снимаем с текста подтверждение, потом добавим».
    #
    # Всё, что здесь стоит, стоит ВМЕСТО снятой кнопки, и ни одна строка не
    # оформление.
    #
    # `{title}` — вопрос пункта словами. Строка `#1 CLN02 · D1 · Кассовая зона`
    # промах сопоставления не показывает никак: код глазами не читается, а
    # формулировка — читается. Именно так владелец и объяснял риск: «кассовая
    # зона, просрочка чизкейк» напрашивается на CLN02 (пункт другой зоны)
    # вместо верного PRD10, и разницу видно только по
    # формулировке. Это осознанное отступление от «не пересказывать пункт»
    # (`docs/06-mvp-bot.md`, шаг 5): то правило написано для записи, пункт
    # которой аудитор уже прочитал на кнопке подтверждения.
    #
    # `{note}` и `{cue}` — слова аудитора целиком и сработавшая строка карты.
    # Сверка отвечает по ОДНОЙ строке, а в одной фразе бывает два нарушения
    # (правило 11): второе не записано, и заметить это можно только по своим
    # словам. Причину отказа (`FastPath.reason`) здесь не показывают никогда:
    # она для замера, а не для экрана.
    # Показ ПОДТВЕРЖДЁННОЙ записи (T135, issue #106). Строкой `record.saved` он
    # и ограничивался, а строка эта не читается: код глазами не проверяется —
    # тот же довод, по которому вопрос пункта попал в блок быстрого пути ниже.
    # Асимметрия выходила обратной здравому смыслу: подробно там, где человек
    # ничего не подтверждал, скупо там, где подтверждал.
    #
    # Добавок ровно две, и обе — то, чего в строке нет. Вопрос пункта словами:
    # по нему видно промах сопоставления. И то, что уйдёт в отчёт партнёру:
    # формулировку аудитор обязан прочитать глазами до отправки, а не после.
    # Строки карты и «ваших слов» здесь нет — их у подтверждённой записи не
    # бывает: текстом стала формулировка модели, и звать её словами аудитора
    # было бы враньём. Таблицы после каждого кадра нет (`docs/06-mvp-bot.md`).
    "record.confirmed": {
        "ru": "{line}{guess}\n{title}\n\nВ отчёт: «{note}»",
        "en": "{line}{guess}\n{title}\n\nInto the report: “{note}”",
    },
    # Тот же показ, когда формулировкой записи стал сам вопрос пункта: так
    # ложится ручной выбор пункта по кадру без комментария (`_save_manual`).
    # Показать одно и то же дважды — выдать за две вещи одну.
    "record.confirmed_plain": {
        "ru": "{line}{guess}\n{title}",
        "en": "{line}{guess}\n{title}",
    },
    # Правка ответом на сообщение бота (T204, D081). Показ собран из тех же
    # частей, что у записи по словам: строка записи, вопрос пункта, что уйдёт в
    # отчёт. Отличается заголовком — иначе аудитор читает «записал» там, где
    # запись не появилась, а изменилась, и ищет в переписке вторую.
    # Ответ пустой (одни пробелы или расшифровка ни во что). Молчать нельзя:
    # аудитор ждёт правки и не узнает, что её не случилось.
    "correct.empty": {
        "ru": "Не разобрал, что поправить в записи #{n}. Напишите словами, что там на самом деле.",
        "en": (
            "I could not tell what to change in record #{n}. Say in words what is actually there."
        ),
    },
    "record.corrected": {
        "ru": (
            "✏️ Поправил запись #{n} по вашему ответу.\n\n"
            "{line}{guess}\n"
            "{title}\n\n"
            "В отчёт: «{note}»{cue}\n\n"
            "Снова не то — ответьте на это сообщение ещё раз."
        ),
        "en": (
            "✏️ Record #{n} updated from your reply.\n\n"
            "{line}{guess}\n"
            "{title}\n\n"
            "Into the report: “{note}”{cue}\n\n"
            "Still wrong — reply to this message again."
        ),
    },
    # Строка карты нарушений показывается только тогда, когда пункт нашла
    # сверка: у ответа модели её нет вовсе, и пустая подпись выглядела бы
    # потерянными данными.
    "record.corrected_cue": {
        "ru": "\nСтрока карты: «{cue}»",
        "en": "\nMap line: “{cue}”",
    },
    "record.fixed": {
        "ru": (
            "✅ Записал сразу, по вашим словам — подтверждать не нужно.\n\n"
            "{line}{guess}\n"
            "{title}\n\n"
            "Ваши слова: «{note}»\n"
            "Строка карты: «{cue}»\n\n"
            "Пункт не тот или в словах есть ещё нарушение — «Разобрать моделью»; "
            "лишнюю запись уберите кнопкой «Удалить»."
        ),
        "en": (
            "✅ Recorded straight away, from your words — no confirmation needed.\n\n"
            "{line}{guess}\n"
            "{title}\n\n"
            "Your words: “{note}”\n"
            "Map line: “{cue}”\n\n"
            "Wrong item, or your words name another violation — “Analyze with the model”; "
            "drop a record you do not need with “Delete”."
        ),
    },
    "record.candidate_line": {
        "ru": "{index}. {code} · {level} · {zone}\n   {wording}",
        "en": "{index}. {code} · {level} · {zone}\n   {wording}",
    },
    # Пометка занятой пары «пункт + зона» в перечне предложений (T137, issue
    # #108). Ставится ПОД строкой кандидата, а не внутрь неё: строк у кандидата
    # уже две (шапка и формулировка), и третья читается, а вставка в шапку
    # разъезжается на телефоне.
    #
    # Пара, а не код: тот же пункт в другой зоне — законная и частая запись,
    # движок отказывает именно на паре. Номер записи назван потому, что им
    # аудитор её и находит: без номера пометка была бы тупиком.
    "record.candidate_taken": {
        "ru": "\n   \u26d4 Уже записано — #{n}: нажатие даст отказ.",
        "en": "\n   \u26d4 Already recorded — #{n}: tapping this will be refused.",
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
    # Причина не называется — тем же принципом, что у отказа движка (T127) и у
    # «предложение устарело» (T128): текст исключения разбора несёт то, что
    # аудитору не место показывать — пути на диске, имена внутренних
    # документов, а на английском стенде ещё и русские слова. Он уходит в
    # журнал целиком (`logger.warning` в вызывающем коде), а в чат — то, что
    # нужно человеку на точке: что случилось и что делать.
    "record.degraded": {
        "ru": "Модель недоступна — проверка продолжается, пункт выберите сами.",
        "en": "The model is unavailable — the inspection goes on, pick the item.",
    },
    # Тот же принцип, что у `record.degraded` выше. Разбор здесь не смог
    # начаться вовсе (не сеть, а настройка стенда), и ни кандидатов, ни ручного
    # перечня показать нечем — отсюда и совет позвать администратора, а не
    # просто «пункт выберите сами».
    "record.unavailable": {
        "ru": (
            "Разбор не работает: стенд настроен не полностью. Материал не записан — "
            "скажите администратору, подробности в журнале."
        ),
        "en": (
            "Analysis is not working: the stand is not fully configured. Nothing was "
            "recorded — tell the administrator, the details are in the log."
        ),
    },
    "record.manual_page": {
        "ru": "Пункты чек-листа, страница {page} из {pages}:",
        "en": "Checklist items, page {page} of {pages}:",
    },
    # Занятая пара «пункт + зона» в РУЧНОМ перечне (T173). Пометка живёт в
    # тексте, а не на кнопке: на кнопку отведено 34 знака, и там она съела бы
    # формулировку пункта — то есть отняла бы у аудитора ровно то, ради чего он
    # перечень и открыл. Связывает строку с кнопкой КОД: он стоит на кнопке
    # первым, и считать позиции не приходится.
    #
    # Значок и оборот те же, что в перечне модели (`record.candidate_taken`):
    # случай один и тот же, и вторая формулировка одного и того же разошлась бы
    # с первой молча.
    "record.manual_taken": {
        "ru": "\n\n\u26d4 Уже записано в этой зоне: {items}. Нажатие даст отказ.",
        "en": "\n\n\u26d4 Already recorded in this zone: {items}. Tapping one will be refused.",
    },
    "record.manual_taken_item": {
        "ru": "{code} — #{n}",
        "en": "{code} — #{n}",
    },
    "record.ask_level": {
        "ru": "Какой класс для {code}?",
        "en": "Which class for {code}?",
    },
    "record.ask_zone": {
        "ru": "В какой зоне это? Из слов зону не видно — назовите её кнопкой.",
        "en": "Which zone is this? Your words do not name it — pick it with a button.",
    },
    # Причина не называется: их несколько, и выбрать одну наугад — соврать
    # (T128). Предложение забирается сразу после фиксации, гасится началом новой
    # проверки, исчезает вместе с перезапуском и не переживает нажатие на кнопку
    # из старого сообщения. «Бот перезапускался» посылало человека искать
    # поломку, которой обычно не было.
    "record.stale": {
        "ru": (
            "Это предложение уже неактуально — выбирать по нему нечего. Пришлите материал заново."
        ),
        "en": (
            "This suggestion is no longer live — there is nothing to pick. Send the material again."
        ),
    },
    "record.skipped": {
        "ru": "Не записал. Кадр не потеряется — покажу его при завершении проверки.",
        "en": "Not recorded. The photo is not lost — I will list it when you finish.",
    },
    "record.saved": {
        "ru": "#{n} {code} · {level} · {zone}",
        "en": "#{n} {code} · {level} · {zone}",
    },
    "record.saved_info": {
        "ru": "#{n} {code} · {level} замер · {zone}",
        "en": "#{n} {code} · {level} measurement · {zone}",
    },
    "record.zone_unknown": {
        "ru": "зона не названа",
        "en": "zone not named",
    },
    "record.voice_not_downloaded": {
        "ru": "Голосовое не скачалось. Повторите или напишите комментарий текстом.",
        "en": "The voice message did not download. Retry or type the comment.",
    },
    # Зона взята из памяти о прошлой записи (D048), а не из этих слов. Сказать
    # обязательно: запись уже сделана и подтверждать её не будут, а «по вашим
    # словам» про зону в этом случае неправда — и промах памяти остаётся без
    # единого читателя (T124).
    "record.fixed_zone_guess": {
        "ru": "\n⚠ Зону в этих словах вы не называли — поставил прошлую. Не та — кнопка «Зона».",
        "en": "\n⚠ These words name no zone — I kept the previous one. Wrong? Use “Zone”.",
    },
    "record.zone_unusual": {
        "ru": " ⚠ зона не из списка пункта",
        "en": " ⚠ zone is not on the item’s list",
    },
    # Отказ движка разобран, а не пересказан (T127). Движок отвечает тому, кто
    # зовёт его из командной строки: «CLN05 в зоне hot_kitchen уже зафиксировано
    # — запись #1. Доснимите фото (audit.py photo 1 --add ...)». У аудитора на
    # точке командной строки нет, `hot_kitchen` он читать не обязан, а язык
    # интерфейса у него может быть не русский. Сам текст движка уходит в журнал.
    "record.duplicate": {
        "ru": (
            "Не записал: это уже зафиксировано.\n\n"
            "#{n} · {item}\n"
            "Зона: {zone}\n\n"
            "Если нашли что-то ещё — поправьте запись #{n} кнопками ниже."
        ),
        "en": (
            "Not recorded: this is already on the list.\n\n"
            "#{n} · {item}\n"
            "Zone: {zone}\n\n"
            "Found something else — fix record #{n} with the buttons below."
        ),
    },
    "record.failed": {
        "ru": "Не записал: {item} · {zone}. Сбой на моей стороне, подробности в журнале.",
        "en": "Not recorded: {item} · {zone}. Something broke on my side, details are in the log.",
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
        "ru": "Поправлено. #{n} {code} · {level} · {zone}",
        "en": "Updated. #{n} {code} · {level} · {zone}",
    },
    "edit.changed_info": {
        "ru": "Поправлено. #{n} {code} · {level} замер · {zone}",
        "en": "Updated. #{n} {code} · {level} measurement · {zone}",
    },
    "edit.dropped": {
        "ru": "Запись #{n} удалена.",
        "en": "Record #{n} deleted.",
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
    "edit.duplicate": {
        "ru": (
            "Не поправил: {item} в зоне «{zone}» уже записано — #{n}.\n\n"
            "Поправьте её кнопками ниже или выберите другую зону."
        ),
        "en": (
            "Not updated: {item} in “{zone}” is already recorded — #{n}.\n\n"
            "Fix that one with the buttons below, or pick another zone."
        ),
    },
    "edit.failed": {
        "ru": (
            "Не поправил запись #{n}: {item} · {zone}. Сбой на моей стороне, подробности в журнале."
        ),
        "en": (
            "Record #{n} not updated: {item} · {zone}. "
            "Something broke on my side, details are in the log."
        ),
    },
    "edit.drop_failed": {
        "ru": "Не удалил запись #{n}: сбой на моей стороне, подробности в журнале.",
        "en": "Record #{n} not deleted: something broke on my side, details are in the log.",
    },
    # --- завершение проверки (T058, T068) ---
    "finish.summary": {
        "ru": "Итог: {pct}% — {grade}, {label}.\nЗаписей: {total}. {counts}",
        "en": "Result: {pct}% — {grade}, {label}.\nRecords: {total}. {counts}",
    },
    # Расхождение версии методики (T167, задача #135). Отказ движка написан
    # тому, кто зовёт блок из кода; здесь то же самое сказано человеку, который
    # стоит на точке. Обе версии названы обязательно: по ним и видно, что
    # методику переиздали под идущей проверкой. Выхода два, оба решает он.
    "finish.version_mismatch": {
        "ru": (
            "⚠ Методику переиздали, пока шла проверка, и посчитать её сейчас нечем.\n\n"
            "Проверка помечена версией: {recorded}\n"
            "Сейчас на диске версия: {current}\n\n"
            "Посчитать по новой методике под старой отметкой нельзя: оценка вышла бы "
            "несравнимой с соседними проверками, а разницы в отчёте видно не будет.\n\n"
            "Выхода два, и выбираете вы:\n"
            "• перевести проверку на действующую методику — оценка будет по ней, "
            "а перевод останется в самой проверке следом;\n"
            "• вернуть прежнюю версию методики на место и досчитать по ней — это "
            "не в боте, это к тому, кто её переиздал."
        ),
        "en": (
            "⚠ The methodology was republished while the inspection was running, so it "
            "cannot be scored as it stands.\n\n"
            "The inspection is marked with version: {recorded}\n"
            "The version on disk now is: {current}\n\n"
            "Scoring by the new methodology under the old mark is not allowed: the grade "
            "would not be comparable with neighbouring inspections, and the report would "
            "not show the difference.\n\n"
            "There are two ways out, and the choice is yours:\n"
            "• move the inspection to the current methodology — it will be scored by it, "
            "and the move stays recorded inside the inspection;\n"
            "• put the previous version of the methodology back and score by it — that is "
            "not done in the bot, it is done by whoever republished it."
        ),
    },
    "finish.version_synced": {
        "ru": "Проверка переведена на методику {current}. Перевод записан в саму проверку.",
        "en": "The inspection now runs on methodology {current}. The move is recorded in it.",
    },
    "finish.version_kept": {
        "ru": (
            "Оставил как есть: проверка по-прежнему помечена версией {recorded}.\n"
            "Считать её будет нечем, пока эта версия методики не вернётся на диск. "
            "Записи никуда не делись — «Завершить» можно нажать снова."
        ),
        "en": (
            "Left as it is: the inspection is still marked with version {recorded}.\n"
            "It cannot be scored until that version of the methodology is back on disk. "
            "The records are intact — you can press “Finish” again."
        ),
    },
    "finish.version_sync_failed": {
        "ru": "Перевести проверку на действующую методику не вышло. Попробуйте ещё раз.",
        "en": "Moving the inspection to the current methodology failed. Please try again.",
    },
    "finish.records": {
        "ru": "Зафиксировано:\n{lines}",
        "en": "Recorded:\n{lines}",
    },
    "finish.record_line": {
        "ru": "#{n} {code} · {level} · {zone}{source} — {text}{unusual}",
        "en": "#{n} {code} · {level} · {zone}{source} — {text}{unusual}",
    },
    "finish.source_photo": {
        "ru": " · по кадру",
        "en": " · from the photo",
    },
    "finish.empty": {
        "ru": "Ни одной записи не зафиксировано.",
        "en": "No records have been made.",
    },
    # Номера сообщения здесь больше нет (T138, задача #109): в телеграме он
    # человеку не показывается, и назвать кадр им — то же, что промолчать.
    # Кадры идут следом сами, ответом на свои же сообщения.
    "finish.unclaimed": {
        "ru": "Кадры без записи — {count}. Они никуда не пропали, но и в отчёт не войдут. "
        "Показываю их ниже — каждый ответом на сообщение, которым он пришёл.",
        "en": "Photos with no record — {count}. Not lost, but they will not be in the report. "
        "They follow below — each as a reply to the message it arrived in.",
    },
    "finish.unclaimed_frame": {
        "ru": "Этот кадр остался без записи.",
        "en": "This photo has no record.",
    },
    "finish.unclaimed_rest": {
        "ru": "Ещё {rest} — показываю по одной пачке: разберите эти и вызовите /records, "
        "покажу следующие.",
        "en": "And {rest} more — shown one batch at a time: deal with these and call /records "
        "for the next ones.",
    },
    "finish.unclaimed_failed": {
        "ru": "Кадров показать не удалось: {failed}. Телеграм их не отдал — они остались "
        "в переписке выше.",
        "en": "Photos that could not be shown: {failed}. Telegram refused them — they are "
        "still in the chat above.",
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
    # Часть методики заполнена не на языке отчёта (T186). Отчёт при этом собран
    # и отдан: непереведённое справочное поле — не повод оставить аудитора на
    # точке без документа, а править данные всё равно не ему. Названы коды, а не
    # формулировки: коды не переводятся, и с ними идут в управляющую компанию.
    # Что именно стоит в поле — в журнале стенда, где это прочитает тот, кто
    # понесёт правку.
    "finish.untranslated": {
        "ru": (
            "⚠ В методике не переведено то, что печатается в отчёте ({lang}): {codes}. "
            "Партнёр увидит эти строки на чужом языке. Отчёт менять не нужно — "
            "передайте коды управляющей компании."
        ),
        "en": (
            "⚠ The methodology is untranslated where the report ({lang}) prints it: {codes}. "
            "The partner will see those lines in another language. The report needs no change "
            "— pass the codes on to the management company."
        ),
    },
    # Причина не называется — тот же принцип, что у отказа движка (T127) и у
    # отказа разбора (T154). Отказ сборки пересказывался дословно, и человек в
    # пиццерии читал разом три чужие вещи: удвоенный служебный префикс («не
    # собрался» + «не собран»), абсолютный путь во временный каталог и
    # инструкцию поставить системные библиотеки. Сделать с этим на точке нельзя
    # ничего. Текст движка уходит в журнал целиком, в чат — что случилось, что
    # с записями и как попробовать снова.
    "finish.pdf_failed": {
        "ru": (
            "Отчёт не собрался. Записи проверки целы — попробуйте ещё раз командой "
            "/finish. Если не выйдет и со второго раза, скажите администратору: "
            "подробности в журнале."
        ),
        "en": (
            "The report was not built. The inspection records are safe — try again with "
            "/finish. If it fails a second time, tell the administrator: the details are "
            "in the log."
        ),
    },
    # Отдельный текст, а не тот же самый: письмо собирается вторым вызовом
    # движка, уже ПОСЛЕ того, как PDF отдан аудитору. «Отчёт не собрался» здесь
    # было прямой неправдой — документ у человека в руках.
    "finish.letter_failed": {
        "ru": (
            "Отчёт готов и отправлен, а письмо партнёру не собралось. Скажите "
            "администратору: подробности в журнале."
        ),
        "en": (
            "The report is ready and sent, but the letter to the partner was not built. "
            "Tell the administrator: the details are in the log."
        ),
    },
    "finish.resumed": {
        "ru": "Продолжаем проверку. Присылайте кадры.",
        "en": "Back to the inspection. Send photos.",
    },
    # --- слив завершённой проверки в историю (T123) ---
    #
    # Оба сообщения приходят ПОСЛЕ отчёта и письма: они у аудитора на руках, и
    # ничего из сделанного не отменяется. Сказать всё равно надо — молчание
    # оставило бы человека уверенным, что история сохранена. Причина отказа
    # (адрес базы, текст драйвера) сюда не попадает: аудитору она ничего не
    # объясняет, её место в журнале.
    "finish.not_archived": {
        "ru": (
            "Отчёт и письмо на месте, но в историю проверок эта проверка не записалась — "
            "база не ответила. Не начинайте новую: пока проверка лежит здесь, её ещё можно "
            "сохранить, а новая её сотрёт."
        ),
        "en": (
            "The report and the letter are yours, but this inspection did not reach the "
            "history — the database did not answer. Do not start a new one yet: while this "
            "inspection is still here it can be saved, and a new one erases it."
        ),
    },
    "finish.photos_not_archived": {
        "ru": (
            "Проверка в историю записана, а кадры в хранилище не уехали. Отчёта это не "
            "задевает — в нём они уже есть."
        ),
        "en": (
            "The inspection reached the history, but its photos did not reach the storage. "
            "The report is unaffected — they are already in it."
        ),
    },
    # --- сбой, который не поймал никто (T126) ---
    #
    # Текст исключения сюда не попадает никогда. Он написан для того, кто чинит:
    # в нём пути к файлам и внутренние подробности, а аудитору нужно другое —
    # что случилось и что делать дальше. Выход назван прямо, потому что до этой
    # задачи выхода не было: испорченное состояние роняло и `/start` тоже, и
    # аудитор оставался в чате, где не работает ни одна команда.
    "error.unexpected": {
        "ru": (
            "Сбой на моей стороне — это сообщение я обработать не смог. "
            "Подробности записаны в журнал.\n\n"
            "Попробуйте ещё раз. Если повторяется — /start: он работает всегда."
        ),
        "en": (
            "Something broke on my side — I could not handle this message. "
            "The details are in the log.\n\n"
            "Try again. If it keeps happening — /start: it always works."
        ),
    },
    # Кнопка, которую некому обработать (T134, issue #105). Уходит и сообщением
    # в чат, и текстом всплывающего окна — у нажатия старше 48 часов сообщения
    # нет вовсе, и окно остаётся единственным каналом. Отсюда предел
    # `ALERT_BYTE_LIMIT`: текст окна Telegram режет молча, а обрезанное
    # объяснение хуже отсутствующего.
    #
    # Причина не называется категорично («старая версия») намеренно: кнопка
    # бывает и своя, и текущей версии — из уже пройденного шага. Названы обе
    # возможности и один надёжный выход.
    "error.button_gone": {
        "ru": (
            "Эта кнопка больше не работает — она из прошлого шага или из старой версии бота. "
            "Нажмите /start: оттуда можно продолжить проверку или начать новую."
        ),
        "en": (
            "This button no longer works — it is from an earlier step or an older version. "
            "Send /start: from there you can resume the inspection or start a new one."
        ),
    },
    # --- надписи на кнопках ---
    "btn.analyze": {"ru": "Разобрать", "en": "Analyze"},
    # Кнопка выбора кандидата (T136, issue #107). Была подписана голой цифрой, и
    # ряд читался как «1 // Выбрать пункт // Не записывать»: номер рядом с двумя
    # глаголами. Место в списке едет в `callback_data`, а в надписи оно нужно
    # только чтобы связать кнопку со строкой показанного перечня, — то есть лишь
    # когда кандидатов несколько. При одном кандидате номера нет вовсе: номер,
    # у которого нет второго, не связывает ни с чем.
    "btn.pick_single": {"ru": "Записать", "en": "Record it"},
    "btn.pick_numbered": {"ru": "Записать №{index}", "en": "Record #{index}"},
    # Те же кнопки под правкой (T204): глагол другой, потому что и действие
    # другое — записи не прибавится. «Записать» здесь читалось бы как вторая
    # запись о том же нарушении, ради избавления от которой аудитор и отвечал.
    "btn.fix_single": {"ru": "Поправить на это", "en": "Change it to this"},
    "btn.fix_numbered": {"ru": "Поправить на №{index}", "en": "Change to #{index}"},
    "btn.fix_skip": {"ru": "Оставить как есть", "en": "Leave as is"},
    "btn.manual": {"ru": "Выбрать пункт", "en": "Pick an item"},
    "btn.skip": {"ru": "Не записывать", "en": "Skip"},
    "btn.model": {"ru": "Разобрать моделью", "en": "Analyze with the model"},
    "btn.more": {"ru": "Дальше", "en": "Next"},
    # --- информационная часть в конце проверки (T158, D069, D070) ---
    #
    # Спрашивается она после подтверждения завершения и ДО сборки отчёта:
    # собранный раньше документ этих полей уже не содержит. На оценку поля не
    # влияют, но печатаются партнёру, и один из них (срок плана действий)
    # читает письмо.
    "info.intro": {
        "ru": (
            "Проверка завершена. Осталась информационная часть — она попадёт в отчёт "
            "партнёру. Любой вопрос можно пропустить, после них соберу отчёт."
        ),
        "en": (
            "The inspection is complete. What is left is the additional information — it goes "
            "into the partner's report. Any question can be skipped; the report follows."
        ),
    },
    # Вопрос — формулировкой методики (её же увидит партнёр в отчёте), подсказка
    # — про способ ответа. Разделены пустой строкой: на телефоне это два абзаца,
    # а не одна длинная строка.
    "info.ask": {
        "ru": "{n} из {total}. {question}\n{hint}",
        "en": "{n} of {total}. {question}\n{hint}",
    },
    "info.hint_text": {
        "ru": "Напишите или наговорите ответ.",
        "en": "Type or dictate your answer.",
    },
    "info.hint_yes_no": {
        "ru": "Ответьте кнопкой — или напишите своими словами.",
        "en": "Answer with a button — or write it in your own words.",
    },
    "info.hint_date": {
        "ru": "Дата в виде 14.09.2026, можно со временем: 14.09.2026 18:30.",
        "en": "A date like 14.09.2026, optionally with a time: 14.09.2026 18:30.",
    },
    "info.saved": {
        "ru": "Записал: {value}",
        "en": "Recorded: {value}",
    },
    "info.not_saved": {
        "ru": (
            "Не записалось — подробности в журнале. Пришлите ответ ещё раз или пропустите "
            "вопрос: остальная проверка от этого не пострадает."
        ),
        "en": (
            "It was not recorded — details are in the log. Send the answer again or skip the "
            "question: the rest of the inspection is unaffected."
        ),
    },
    # Расшифровка показывается ДО записи и правится (D069). Это не противоречит
    # D064: тот снял подтверждение с текста, потому что человек написал сам, а
    # расшифровка может ослышаться.
    "info.heard": {
        "ru": "Услышал: {note}\n\nЗаписать так? Если не так — просто пришлите текстом.",
        "en": "I heard: {note}\n\nRecord it as is? If not — just send the text instead.",
    },
    "info.bad_date": {
        "ru": (
            "Не понял дату в «{text}». Пришлите в виде 14.09.2026 (можно со временем) "
            "или пропустите вопрос."
        ),
        "en": (
            "I could not read a date in “{text}”. Send it as 14.09.2026 (a time may follow) "
            "or skip the question."
        ),
    },
    # Кадр в информационной части (T179). Прежний текст обещал, что в отчёт
    # попадёт только текст, — с T172 это неправда: движок печатает кадр под
    # текстом своего поля. Кадр печатается РЯДОМ С ОТВЕТОМ, поэтому без ответа
    # его печатать некуда: он ждёт слов на тот же вопрос.
    "info.photo_taken": {
        "ru": (
            "Кадр принял — он уйдёт в отчёт рядом с ответом на этот вопрос. "
            "Ответ напишите или наговорите."
        ),
        "en": (
            "Photo received — it goes into the report next to your answer to this question. "
            "Type or dictate the answer."
        ),
    },
    # Число, а не согласование: «Кадров при поле — 1» читается одинаково при
    # любом количестве, и русский текст не приходится склонять (тот же приём,
    # что в `finish.unclaimed`).
    "info.photo_attached": {
        "ru": "Кадров при этом поле — {count}: они напечатаются в отчёте рядом с ответом.",
        "en": "Photos on this field — {count}: they print in the report next to the answer.",
    },
    "info.photo_dropped": {
        "ru": (
            "Вопрос пропущен, а кадров к нему было — {count}. В отчёт они не уйдут: "
            "в информационной части кадр печатается рядом с ответом, а ответа нет."
        ),
        "en": (
            "The question was skipped, and photos attached to it — {count}. They will not "
            "reach the report: here a photo prints next to an answer, and there is none."
        ),
    },
    # Значение поля «да/нет», уезжающее в отчёт. Язык здесь — язык ОТЧЁТА, а не
    # интерфейса: строку читает партнёр, а не аудитор.
    "info.value_yes": {"ru": "Да", "en": "Yes"},
    "info.value_no": {"ru": "Нет", "en": "No"},
    "info.finished": {
        "ru": "Информационная часть готова.",
        "en": "Additional information is complete.",
    },
    # Описания команд в меню телеграма (T139). Меню — единственное место, где
    # аудитор увидит команду, не зная о ней заранее: набирать `/records` по
    # памяти на точке никто не будет.
    "cmd.start": {"ru": "Начать проверку", "en": "Start an inspection"},
    "cmd.records": {"ru": "Что записано", "en": "What is recorded"},
    "cmd.undo": {"ru": "Снять последнюю запись", "en": "Undo the last record"},
    "cmd.finish": {"ru": "Завершить и собрать отчёт", "en": "Finish and build the report"},
    # Кнопки мастера начала проверки (T131). До T131 их надписи стояли строками
    # в `keyboards.py` — единственные строки интерфейса мимо каталога, и потому
    # единственные, которые язык стенда не мог перекрасить.
    "btn.new_inspection": {"ru": "Новая проверка", "en": "New inspection"},
    "btn.resume_continue": {"ru": "Продолжить", "en": "Continue"},
    "btn.sealed_drop": {"ru": "Убрать из чата", "en": "Remove from chat"},
    "btn.resume_new": {"ru": "Начать новую", "en": "Start a new one"},
    "btn.back": {"ru": "Назад", "en": "Back"},
    "btn.zone": {"ru": "Зона", "en": "Zone"},
    "btn.level": {"ru": "Класс", "en": "Class"},
    "btn.text": {"ru": "Формулировка", "en": "Wording"},
    "btn.drop": {"ru": "Удалить", "en": "Delete"},
    # Кнопки информационной части (T158).
    "btn.yes": {"ru": "Да", "en": "Yes"},
    "btn.no": {"ru": "Нет", "en": "No"},
    "btn.info_skip": {"ru": "Пропустить", "en": "Skip"},
    "btn.info_done": {"ru": "Дальше к отчёту", "en": "On to the report"},
    "btn.info_save": {"ru": "Записать так", "en": "Record as is"},
    "btn.build": {"ru": "Собрать отчёт", "en": "Build the report"},
    "btn.build_without_photos": {"ru": "Собрать без кадров", "en": "Build without photos"},
    "btn.edit": {"ru": "Поправить запись", "en": "Edit a record"},
    "btn.resume": {"ru": "Продолжить проверку", "en": "Continue the inspection"},
    "btn.version_sync": {
        "ru": "Перевести на действующую методику",
        "en": "Move to the current methodology",
    },
    "btn.version_keep": {
        "ru": "Оставить как есть, разберусь с методикой",
        "en": "Leave it, I will sort the methodology out",
    },
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


def with_photo_rule(text: str, lang: str) -> str:
    """Дописать под сообщением правило фотофиксации отдельным абзацем (T160, D078).

    Сборка вынесена сюда, а не повторена в роутерах, по той же причине, по
    которой само правило — один ключ каталога: мест, где оно звучит, три (старт
    проверки, её продолжение, отказ на комментарий без кадра), и разъехаться им
    нечего стоит. Здесь же держится и отступ между сообщением и правилом —
    иначе в одном месте оно оказалось бы абзацем, а в другом хвостом строки.

    Правило звучит и ЗАРАНЕЕ, и по факту отказа намеренно. Владелец: «надо
    чтобы человек понял что фото должно быть», — а понимает человек то, что
    прочитал до ошибки, а не только то, чем ему на неё ответили.
    """
    return f"{text}\n\n{t('material.photo_required', lang)}"


def default_ui_lang(env: Mapping[str, str] | None = None) -> str:
    """Язык интерфейса этого стенда: `BOT_UI_LANG`, иначе умолчание продукта.

    Пусто — не ошибка, а обычный боевой стенд: он говорит по-русски, как и до
    появления переменной. Неизвестный язык — отказ: молчаливый откат на русский
    означал бы, что опечатка в переменной демо-стенда обнаружится на показе.
    Отказ приходит раньше — на старте бота (`config.load_bot_settings`), а не
    первой строкой аудитору.
    """
    src = os.environ if env is None else env
    value = (src.get(UI_LANG_VAR) or "").strip()
    if not value:
        return DEFAULT_UI_LANG
    if value not in UI_LANGS:
        raise BotTextError(
            f"Язык интерфейса «{value}» ({UI_LANG_VAR}) не заведён. Доступны: {', '.join(UI_LANGS)}"
        )
    return value


def ui_lang_or_default(lang: str | None) -> str:
    """Язык интерфейса проверки, если он известен и поддержан, иначе язык стенда.

    Нужно ровно там, где проверки ещё нет (мастер начала): подставлять язык
    несуществующего состояния неоткуда, а падать на приветствии нельзя.
    """
    return lang if lang in UI_LANGS else default_ui_lang()
