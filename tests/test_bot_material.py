"""Связывание фото с комментарием: подпись, следующее сообщение, ответ (reply).

Без aiogram и без Telegram — только структуры данных и очередь. Три способа
связывания и доставка пачкой после потери связи (T059) — see docs/forge,
контракт блока `bot`.
"""

from __future__ import annotations

from src.bot.material import ChatMaterialQueue, Comment, Material, MaterialStore, PhotoGroup


def _group(
    group_id: str,
    message_ids: tuple[int, ...],
    photo_file_ids: tuple[str, ...] = ("file-1",),
    caption: str | None = None,
) -> PhotoGroup:
    return PhotoGroup(
        group_id=group_id,
        chat_id=1,
        message_ids=message_ids,
        photo_file_ids=photo_file_ids,
        caption=caption,
    )


def test_group_with_caption_resolves_immediately() -> None:
    """Способ 1: подпись — материал готов сразу же, без очереди."""
    queue = ChatMaterialQueue()
    group = _group("g1", (10,), caption="скол на бортике")

    material = queue.add_group(group)

    assert material == Material(
        chat_id=1,
        photo_file_ids=("file-1",),
        comment=Comment(text="скол на бортике"),
    )
    assert not queue.has_pending()


def test_group_without_caption_waits() -> None:
    queue = ChatMaterialQueue()
    group = _group("g1", (10,), caption=None)

    material = queue.add_group(group)

    assert material is None
    assert queue.has_pending()


def test_blank_caption_behaves_like_no_caption() -> None:
    """Подпись из одних пробелов — как её отсутствие: группа уходит в очередь."""
    queue = ChatMaterialQueue()
    group = _group("g1", (10,), caption="   ")

    material = queue.add_group(group)

    assert material is None
    assert queue.has_pending()


def test_resolve_next_returns_oldest_group_first() -> None:
    """FIFO: несколько групп без подписи — комментарии приходят в том же порядке."""
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))
    queue.add_group(_group("g2", (20,), photo_file_ids=("f2",)))

    first = queue.resolve_next(Comment(text="первый комментарий"))
    second = queue.resolve_next(Comment(text="второй комментарий"))

    assert first == Material(
        chat_id=1, photo_file_ids=("f1",), comment=Comment(text="первый комментарий")
    )
    assert second == Material(
        chat_id=1, photo_file_ids=("f2",), comment=Comment(text="второй комментарий")
    )


def test_resolve_next_on_empty_queue_returns_none() -> None:
    queue = ChatMaterialQueue()

    assert queue.resolve_next(Comment(text="без пары")) is None


def test_batched_delivery_after_connection_loss_matches_order() -> None:
    """T059: все фото подряд, затем все комментарии подряд — порядок как при отправке."""
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))
    queue.add_group(_group("g2", (20,), photo_file_ids=("f2",)))
    queue.add_group(_group("g3", (30,), photo_file_ids=("f3",)))

    results = [
        queue.resolve_next(Comment(text="1")),
        queue.resolve_next(Comment(text="2")),
        queue.resolve_next(Comment(text="3")),
    ]

    assert [m.photo_file_ids if m else None for m in results] == [("f1",), ("f2",), ("f3",)]
    assert not queue.has_pending()


def test_resolve_reply_takes_group_out_of_pending_queue() -> None:
    """Способ 3 забирает группу из очереди — resolve_next её больше не отдаст."""
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))
    queue.add_group(_group("g2", (20,), photo_file_ids=("f2",)))

    replied = queue.resolve_reply(10, Comment(text="ответ на первое фото"))
    assert replied == Material(
        chat_id=1, photo_file_ids=("f1",), comment=Comment(text="ответ на первое фото")
    )

    remaining = queue.resolve_next(Comment(text="обычный комментарий"))
    assert remaining == Material(
        chat_id=1, photo_file_ids=("f2",), comment=Comment(text="обычный комментарий")
    )
    assert queue.resolve_next(Comment(text="пусто")) is None


def test_resolve_reply_works_on_already_captioned_group() -> None:
    """Можно ответить и на фото с уже сработавшей подписью — новый независимый материал."""
    queue = ChatMaterialQueue()
    group = _group("g1", (10,), photo_file_ids=("f1",), caption="уже подписано")
    first_material = queue.add_group(group)
    assert first_material is not None

    replied = queue.resolve_reply(10, Comment(text="ещё одно замечание"))

    assert replied == Material(
        chat_id=1, photo_file_ids=("f1",), comment=Comment(text="ещё одно замечание")
    )


def test_resolve_reply_twice_on_same_message_id_both_succeed() -> None:
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))

    first = queue.resolve_reply(10, Comment(text="первый ответ"))
    second = queue.resolve_reply(10, Comment(text="второй ответ"))

    assert first is not None
    assert second is not None
    assert first.comment.text == "первый ответ"
    assert second.comment.text == "второй ответ"


def test_resolve_reply_unknown_message_id_returns_none() -> None:
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))

    assert queue.resolve_reply(999, Comment(text="некуда")) is None


def test_resolve_reply_finds_group_by_any_message_id_in_album() -> None:
    """Альбом: ответ на любой из message_id группы находит её же."""
    queue = ChatMaterialQueue()
    queue.add_group(_group("album-1", (10, 11, 12), photo_file_ids=("f1", "f2", "f3")))

    replied = queue.resolve_reply(11, Comment(text="про весь альбом"))

    assert replied == Material(
        chat_id=1,
        photo_file_ids=("f1", "f2", "f3"),
        comment=Comment(text="про весь альбом"),
    )


def test_material_store_keeps_independent_queues_per_chat() -> None:
    store = MaterialStore()
    queue_a = store.queue(chat_id=1)
    queue_b = store.queue(chat_id=2)

    queue_a.add_group(_group("g1", (10,), photo_file_ids=("f1",)))

    assert queue_a.has_pending()
    assert not queue_b.has_pending()
    assert queue_b.resolve_next(Comment(text="чужой чат")) is None


def test_material_store_returns_same_queue_object_for_same_chat_id() -> None:
    store = MaterialStore()

    first = store.queue(chat_id=42)
    second = store.queue(chat_id=42)

    assert first is second


def test_voice_comment_survives_resolve_next() -> None:
    """Комментарий может быть голосовым — file_id долетает как есть, без потерь."""
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))

    material = queue.resolve_next(Comment(voice_file_id="voice-abc"))

    assert material is not None
    assert material.comment == Comment(voice_file_id="voice-abc")


def test_voice_comment_survives_resolve_reply() -> None:
    queue = ChatMaterialQueue()
    queue.add_group(_group("g1", (10,), photo_file_ids=("f1",)))

    material = queue.resolve_reply(10, Comment(voice_file_id="voice-xyz"))

    assert material is not None
    assert material.comment == Comment(voice_file_id="voice-xyz")
