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
            "Дата: {date}\n\n"
            "Присылайте фотографии с комментариями."
        ),
        "en": (
            "Inspection started.\n"
            "Pizzeria: {unit}\n"
            "Type: {kind}\n"
            "Report language: {lang}\n"
            "Auditor: {auditor}\n"
            "{auditor_note}"
            "Date: {date}\n\n"
            "Send photos with comments."
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
            "Продолжить её — можно дописать и собрать отчёт заново. "
            "Новая начнётся с чистого листа."
        ),
        "en": (
            "The inspection in this chat is already handed over: its report was built "
            "and sent.\n"
            "Pizzeria: {unit}\n"
            "Date: {date}\n"
            "Auditor: {auditor}\n"
            "Records: {findings}\n\n"
            "Continue it — you can add more and rebuild the report. "
            "A new one starts from a clean slate."
        ),
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
    # Запись, легшая по словам аудитора сразу, без подтверждения (T121, D064).
    # Владелец, дословно: «снимаем с текста подтверждение, потом добавим».
    #
    # Всё, что здесь стоит, стоит ВМЕСТО снятой кнопки, и ни одна строка не
    # оформление.
    #
    # `{title}` — вопрос пункта словами. Строка `#1 CLN02 · D1 · Кассовая зона`
    # промах сопоставления не показывает никак: код глазами не читается, а
    # формулировка — читается. Именно так владелец и объяснял риск: «кассовая
    # зона, просрочка чизкейк» напрашивается на CLN02 («оборудование в зоне
    # мойки без загрязнений») вместо верного PRD10, и разницу видно только по
    # формулировке. Это осознанное отступление от «не пересказывать пункт»
    # (`docs/06-mvp-bot.md`, шаг 5): то правило написано для записи, пункт
    # которой аудитор уже прочитал на кнопке подтверждения.
    #
    # `{note}` и `{cue}` — слова аудитора целиком и сработавшая строка карты.
    # Сверка отвечает по ОДНОЙ строке, а в одной фразе бывает два нарушения
    # (правило 11): второе не записано, и заметить это можно только по своим
    # словам. Причину отказа (`FastPath.reason`) здесь не показывают никогда:
    # она для замера, а не для экрана.
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
    # --- надписи на кнопках ---
    "btn.analyze": {"ru": "Разобрать", "en": "Analyze"},
    "btn.manual": {"ru": "Выбрать пункт", "en": "Pick an item"},
    "btn.skip": {"ru": "Не записывать", "en": "Skip"},
    "btn.model": {"ru": "Разобрать моделью", "en": "Analyze with the model"},
    "btn.more": {"ru": "Дальше", "en": "Next"},
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
    "btn.resume_new": {"ru": "Начать новую", "en": "Start a new one"},
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
