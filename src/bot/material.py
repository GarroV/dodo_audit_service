"""Связывание фото нарушения с комментарием аудитора.

Комментарий к фото можно дать тремя способами: подписью к самому фото, обычным
сообщением сразу следом, или ответом (reply) на любое из ранее отправленных
фото. Несколько кадров одного альбома (`media_group_id`) — один материал.
Фото без комментария не теряется: оно ждёт в очереди, и по нему задаётся
уточняющий вопрос — эта часть (T053/T054) вне модуля, здесь только структура
данных и правила связывания.

Модуль намеренно не знает про aiogram и Telegram — операции на реальных
объектах превращает в вызовы отсюда отдельный роутер. Так логику связывания
можно проверить юнит-тестами без aiogram-типов и без сети.

Способ 2 (обычное сообщение) обязан быть устойчив к доставке пачкой: телефон
аудитора теряет связь, и все отложенные сообщения приходят разом. Telegram
сохраняет порядок отправки внутри чата, поэтому FIFO-очередь по чату даёт
правильный результат что при живом вводе, что при пачке (T059) — обработка
идёт по порядку прихода, а не по промежуткам между сообщениями.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class PhotoGroup:
    """Один снимок или альбом, ещё не связанный с комментарием."""

    group_id: str  # media_group_id или "single-<message_id>"
    chat_id: int
    message_ids: tuple[int, ...]  # все id сообщений группы — цель для ответа (reply)
    photo_file_ids: tuple[str, ...]  # file_id самого крупного размера на каждый кадр
    caption: str | None  # подпись, если она была у группы


@dataclass(frozen=True)
class Comment:
    """Комментарий: текст или голос — ровно один, не оба и не ни одного.

    Модуль это не проверяет — какой из способов ввода дал аудитор, знает
    вызывающий код бота, а не очередь материалов.
    """

    text: str | None = None
    voice_file_id: str | None = None


@dataclass(frozen=True)
class Material:
    """Фото плюс комментарий — готово для передачи в recognize (блок появится второй волной)."""

    chat_id: int
    photo_file_ids: tuple[str, ...]
    comment: Comment


class ChatMaterialQueue:
    """Незавершённые группы фото одного чата.

    FIFO для способа 2 (комментарий отдельным сообщением следом): несколько
    кадров без подписи, снятых подряд, получают комментарии в том же порядке,
    в каком были сняты. Это работает одинаково и при живом вводе, и при пачке
    сообщений, пришедшей разом после потери связи (Telegram порядок не меняет).

    Помимо очереди ожидания класс держит постоянный индекс «id сообщения →
    группа» — по нему находит группу способ 3 (reply), причём даже для группы,
    которая уже получила комментарий подписью или через resolve_next: ответить
    можно и на прокомментированное фото, это создаст независимый материал
    с теми же снимками.
    """

    def __init__(self) -> None:
        self._pending: deque[PhotoGroup] = deque()
        self._by_message_id: dict[int, PhotoGroup] = {}

    def add_group(self, group: PhotoGroup) -> Material | None:
        """Зарегистрировать группу.

        Если у группы была подпись (`caption` не пусто и не пробелы) — материал
        готов немедленно, возвращается он, группа никуда не встаёт в очередь
        ожидания, но всё равно навсегда индексируется по всем `message_ids` —
        чтобы позже на неё можно было ответить (см. `resolve_reply`).
        Без подписи — группа встаёт в конец очереди ожидания, возвращается `None`.
        """
        for message_id in group.message_ids:
            self._by_message_id[message_id] = group

        caption = (group.caption or "").strip()
        if caption:
            return Material(
                chat_id=group.chat_id,
                photo_file_ids=group.photo_file_ids,
                comment=Comment(text=caption),
            )

        self._pending.append(group)
        return None

    def resolve_next(self, comment: Comment) -> Material | None:
        """Способ 2: обычное сообщение (не ответ) — комментарий к старейшей
        незавершённой группе очереди (FIFO, `popleft`). Очередь пуста — `None`,
        привязывать не к чему."""
        if not self._pending:
            return None
        group = self._pending.popleft()
        return Material(
            chat_id=group.chat_id,
            photo_file_ids=group.photo_file_ids,
            comment=comment,
        )

    def resolve_reply(self, reply_to_message_id: int, comment: Comment) -> Material | None:
        """Способ 3: ответ на конкретное сообщение с фото — по любому id из
        `message_ids` любой когда-либо зарегистрированной группы (не только
        ожидающей: ответить можно и на фото, у которого уже была подпись —
        это будет отдельный, независимый материал с тем же набором фото).
        Если группа при этом ещё стояла в очереди ожидания — убрать её оттуда
        (комментарий получен, повторно через `resolve_next` она попадаться не
        должна). id не найден среди известных сообщений с фото — `None`."""
        group = self._by_message_id.get(reply_to_message_id)
        if group is None:
            return None

        try:
            self._pending.remove(group)
        except ValueError:
            pass  # группа уже не в очереди — резолвнута подпиской или другим reply

        return Material(
            chat_id=group.chat_id,
            photo_file_ids=group.photo_file_ids,
            comment=comment,
        )

    def has_pending(self) -> bool:
        """Есть ли хоть одна группа, ожидающая комментарий."""
        return bool(self._pending)


class MaterialStore:
    """Очереди по чатам (`ChatMaterialQueue` на каждый chat_id, лениво создаются)."""

    def __init__(self) -> None:
        self._queues: dict[int, ChatMaterialQueue] = {}

    def queue(self, chat_id: int) -> ChatMaterialQueue:
        """Очередь этого чата, создать при первом обращении."""
        if chat_id not in self._queues:
            self._queues[chat_id] = ChatMaterialQueue()
        return self._queues[chat_id]
