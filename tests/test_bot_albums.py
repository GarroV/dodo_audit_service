"""Склейка альбома по `media_group_id` (T054): несколько кадров — один материал.

Копилка намеренно синхронная и без таймеров внутри: окно ожидания заводит
роутер, а закрытие альбома происходит одним неделимым действием. Иначе между
«взять накопленное» и «очистить» вклинивался бы следующий кадр — ровно тот
класс ошибок, из-за которого альбом распадается на два нарушения.
"""

from __future__ import annotations

from src.bot.albums import ALBUM_WINDOW_SECONDS, AlbumBuffer, Frame


def frame(message_id: int, file_id: str, caption: str | None = None) -> Frame:
    return Frame(message_id=message_id, file_id=file_id, caption=caption)


def test_window_is_between_one_and_two_seconds() -> None:
    """Требование спеки дословно: окно 1–2 секунды."""
    assert 1.0 <= ALBUM_WINDOW_SECONDS <= 2.0


def test_frames_of_one_group_become_one_photo_group() -> None:
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))
    buffer.add(7, "album-1", frame(11, "BBB"))
    buffer.add(7, "album-1", frame(12, "CCC"))

    group = buffer.close_open(7)

    assert group is not None
    assert group.group_id == "album-1"
    assert group.chat_id == 7
    assert group.photo_file_ids == ("AAA", "BBB", "CCC")
    assert group.message_ids == (10, 11, 12)


def test_only_first_frame_opens_the_window() -> None:
    """Таймер закрытия заводится один раз на альбом, а не на каждый кадр."""
    buffer = AlbumBuffer()
    assert buffer.add(7, "album-1", frame(10, "AAA")) is True
    assert buffer.add(7, "album-1", frame(11, "BBB")) is False


def test_caption_is_taken_from_whichever_frame_carries_it() -> None:
    """Telegram кладёт подпись на один кадр альбома, не обязательно на первый."""
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))
    buffer.add(7, "album-1", frame(11, "BBB", caption="пол в горячем цеху грязный"))

    group = buffer.close_open(7)

    assert group is not None
    assert group.caption == "пол в горячем цеху грязный"


def test_first_non_empty_caption_wins_over_later_one() -> None:
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA", caption="первая"))
    buffer.add(7, "album-1", frame(11, "BBB", caption="вторая"))

    group = buffer.close_open(7)

    assert group is not None
    assert group.caption == "первая"


def test_close_open_on_empty_chat_returns_none() -> None:
    assert AlbumBuffer().close_open(7) is None


def test_close_open_twice_gives_nothing_the_second_time() -> None:
    """Закрытие неделимо: второй вызов не должен выдать тот же альбом ещё раз."""
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))

    assert buffer.close_open(7) is not None
    assert buffer.close_open(7) is None


def test_close_other_leaves_the_same_album_open() -> None:
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))

    assert buffer.close_other(7, "album-1") is None
    assert buffer.close_open(7) is not None


def test_frame_of_another_group_closes_the_previous_album() -> None:
    """Пришёл кадр другого альбома раньше таймера — предыдущий закрывается сразу."""
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))

    closed = buffer.close_other(7, "album-2")

    assert closed is not None
    assert closed.group_id == "album-1"
    assert buffer.close_open(7) is None


def test_late_timer_does_not_steal_the_next_album() -> None:
    """Таймер альбома, который уже закрылся приходом следующего, не трогает новый."""
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))
    buffer.close_other(7, "album-2")
    buffer.add(7, "album-2", frame(11, "BBB"))

    assert buffer.close_group(7, "album-1") is None

    still_open = buffer.close_group(7, "album-2")
    assert still_open is not None
    assert still_open.group_id == "album-2"


def test_chats_do_not_share_albums() -> None:
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))
    buffer.add(8, "album-2", frame(20, "BBB"))

    first = buffer.close_open(7)
    second = buffer.close_open(8)

    assert first is not None and first.photo_file_ids == ("AAA",)
    assert second is not None and second.photo_file_ids == ("BBB",)


def test_chat_can_open_a_new_album_after_closing() -> None:
    buffer = AlbumBuffer()
    buffer.add(7, "album-1", frame(10, "AAA"))
    buffer.close_open(7)

    assert buffer.add(7, "album-2", frame(11, "BBB")) is True
    group = buffer.close_open(7)
    assert group is not None and group.group_id == "album-2"


def test_single_photo_is_a_group_of_one() -> None:
    """Одиночный кадр идёт тем же путём, что альбом, — роутеру не нужна вторая ветка."""
    buffer = AlbumBuffer()
    buffer.add(7, "single-10", frame(10, "AAA", caption="подпись"))

    group = buffer.close_group(7, "single-10")

    assert group is not None
    assert group.photo_file_ids == ("AAA",)
    assert group.caption == "подпись"
