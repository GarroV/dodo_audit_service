"""Склейка альбома по `media_group_id` в один материал (задача T054).

Telegram присылает альбом отдельными сообщениями, у которых совпадает
`media_group_id`. Несколько кадров одного объекта должны стать **одним**
нарушением с несколькими фотографиями, иначе оценка проседает на ровном месте
(`docs/forge/spec.md`, раздел «Фиксация»).

Копилка синхронная и без таймеров внутри — это главное её свойство. Окно
ожидания заводит роутер, а закрытие альбома здесь происходит одним неделимым
действием: «взять накопленное и очистить» без единого `await` посередине. Между
двумя `await` планировщик asyncio вставляет чужую задачу, и следующий кадр
успел бы попасть в уже отданный альбом — на глаз это выглядит как случайно
распадающийся на два нарушения альбом.

Закрыть альбом может любое из трёх событий, и у каждого свой метод:

* пришёл кадр другого альбома — `close_other`;
* пришёл комментарий или что угодно ещё — `close_open`;
* истекло окно ожидания — `close_group`, и только если открыт всё ещё **тот
  самый** альбом: опоздавший таймер не должен утащить следующий.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .material import PhotoGroup

#: Окно ожидания кадров альбома. Спека требует 1–2 секунды
#: (`docs/06-mvp-bot.md`, шаг 3); полтора — середина, а не край диапазона:
#: на краю любая задержка сети выносит последний кадр за окно.
ALBUM_WINDOW_SECONDS = 1.5


@dataclass(frozen=True)
class Frame:
    """Один кадр: сообщение, самый крупный размер фото и подпись, если была."""

    message_id: int
    file_id: str
    caption: str | None = None


@dataclass
class _OpenAlbum:
    """Альбом чата, у которого ещё идёт окно ожидания."""

    group_key: str
    chat_id: int
    frames: list[Frame] = field(default_factory=list)

    def to_group(self) -> PhotoGroup:
        """Подпись берётся первая непустая: Telegram кладёт её на один кадр из всех."""
        captions = (f.caption.strip() for f in self.frames if f.caption and f.caption.strip())
        caption = next(captions, None)
        return PhotoGroup(
            group_id=self.group_key,
            chat_id=self.chat_id,
            message_ids=tuple(f.message_id for f in self.frames),
            photo_file_ids=tuple(f.file_id for f in self.frames),
            caption=caption,
        )


class AlbumBuffer:
    """Открытые альбомы по чатам: не больше одного на чат одновременно.

    Больше одного и не нужно: кадры альбома приходят подряд, а приход кадра
    другой группы означает, что предыдущий альбом кончился.
    """

    def __init__(self) -> None:
        self._open: dict[int, _OpenAlbum] = {}

    def add(self, chat_id: int, group_key: str, frame: Frame) -> bool:
        """Положить кадр в альбом чата. `True` — окно открылось этим кадром.

        По `True` вызывающий заводит таймер закрытия ровно один раз на альбом.
        Кадр чужой группы сюда попадать не должен — роутер обязан сначала
        закрыть предыдущий альбом через `close_other`.
        """
        album = self._open.get(chat_id)
        if album is not None and album.group_key == group_key:
            album.frames.append(frame)
            return False
        self._open[chat_id] = _OpenAlbum(group_key=group_key, chat_id=chat_id, frames=[frame])
        return True

    def close_open(self, chat_id: int) -> PhotoGroup | None:
        """Закрыть открытый альбом чата, какой бы он ни был. Нет открытого — `None`."""
        album = self._open.pop(chat_id, None)
        return None if album is None else album.to_group()

    def close_other(self, chat_id: int, group_key: str) -> PhotoGroup | None:
        """Закрыть открытый альбом, если он **не** `group_key`. Тот же — оставить открытым."""
        album = self._open.get(chat_id)
        if album is None or album.group_key == group_key:
            return None
        del self._open[chat_id]
        return album.to_group()

    def close_group(self, chat_id: int, group_key: str) -> PhotoGroup | None:
        """Закрыть альбом по истечении окна — только если открыт всё ещё он.

        Проверка ключа и есть защита от опоздавшего таймера: альбом мог
        закрыться раньше приходом следующего кадра, и на его месте уже стоит
        другой, который этот таймер закрывать не должен.
        """
        album = self._open.get(chat_id)
        if album is None or album.group_key != group_key:
            return None
        del self._open[chat_id]
        return album.to_group()
