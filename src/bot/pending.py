"""Что в этом чате ждёт нажатия кнопки: вопрос «Разобрать?» и показанные предложения.

Хранится только в памяти, и это осознанный выбор, а не упрощение. Всё, что
переживает перезапуск, уже лежит на диске: сама проверка — в `inspection.json`
(блок `domain`), кадры, источники записей и последняя зона — в заметках бота
(`src/bot/sidecar.py`). Здесь же живёт разговор в моменте: кадр, на который
задан вопрос, и пять кандидатов, показанных кнопками. Переживи они перезапуск,
аудитор нажал бы кнопку под ответом модели, которого больше нет ни у кого в
памяти, и получил бы запись, собранную по устаревшему разбору.

Поэтому нажатие на кнопку, которой бот уже не помнит, — это не ошибка, а
нормальный исход: он отвечает «предложение устарело», и аудитор присылает кадр
заново. Молчание в этом месте читалось бы как «бот завис».
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.recognize.manual import ManualCandidate
from src.recognize.models import Candidate


@dataclass(frozen=True)
class Offer:
    """Кадры, на которые задан вопрос «Разобрать?» и ждём нажатия (T067, D046).

    `anchor_id` — первый `message_id` группы кадров: он уходит в `callback_data`
    и по нему же группа снимается с очереди ожидания комментария. Кодом, а не
    формулировкой — принцип проекта.

    `question_id` — сообщение самого бота с кнопкой. Нужен, чтобы снять кнопку,
    когда аудитор вместо нажатия прислал комментарий: слова аудитора сильнее
    догадки по картинке (D046), и висящая кнопка предлагала бы разобрать кадр,
    по которому запись уже сделана.
    """

    anchor_id: int
    file_ids: tuple[str, ...]
    question_id: int | None = None


@dataclass(frozen=True)
class Proposal:
    """Показанные кнопками кандидаты и всё, что нужно для фиксации (T055).

    `source` — откуда взялась запись: со слов аудитора или разбором кадра по
    нажатию (D044). Хранится здесь, потому что в момент нажатия кнопки другого
    места узнать это уже нет.

    `manual` не пуст, когда модель недоступна и перечень собран справочником
    (`recognize.manual_candidates`, T034). Тогда `candidates` пуст: это не
    предложения модели, а полный список для осознанного выбора человеком, и
    показывается он страницами.
    """

    file_ids: tuple[str, ...]
    source: str
    note: str = ""
    candidates: tuple[Candidate, ...] = ()
    manual: tuple[ManualCandidate, ...] = ()
    question: str = ""
    zone_hint: str = ""
    #: Кандидат, который аудитор выбрал, но зафиксировать пока нечем: модель не
    #: назвала зону, и её спрашивают кнопкой. Хранится здесь, потому что в
    #: `callback_data` кнопки зоны едет зона, а не выбор.
    picked: int | None = None


@dataclass
class ChatPending:
    """Ожидания одного чата."""

    offers: dict[int, Offer] = field(default_factory=dict)
    proposal: Proposal | None = None


class PendingStore:
    """Ожидания по чатам, лениво создаются."""

    def __init__(self) -> None:
        self._chats: dict[int, ChatPending] = {}

    def _chat(self, chat_id: int) -> ChatPending:
        if chat_id not in self._chats:
            self._chats[chat_id] = ChatPending()
        return self._chats[chat_id]

    # --- вопрос «Разобрать?» (T067) ---

    def offer(self, chat_id: int, offer: Offer) -> None:
        self._chat(chat_id).offers[offer.anchor_id] = offer

    def take_offer(self, chat_id: int, anchor_id: int) -> Offer | None:
        """Забрать вопрос по нажатию. Второе нажатие той же кнопки — уже `None`."""
        return self._chat(chat_id).offers.pop(anchor_id, None)

    def take_offer_for(self, chat_id: int, file_ids: tuple[str, ...]) -> Offer | None:
        """Забрать вопрос, заданный по этим кадрам: комментарий их забрал.

        Ищем по кадрам, а не по номеру сообщения, потому что комментарий
        связывается с материалом (`src/bot/material.py`), а тот знает кадры, а
        не сообщения, которыми они пришли.
        """
        chat = self._chat(chat_id)
        wanted = set(file_ids)
        for anchor_id, offer in list(chat.offers.items()):
            if wanted.intersection(offer.file_ids):
                del chat.offers[anchor_id]
                return offer
        return None

    # --- показанные предложения (T055) ---

    def propose(self, chat_id: int, proposal: Proposal) -> None:
        """Запомнить показанное. Предыдущее предложение вытесняется: кнопки под
        ним уже не отвечают ничему живому, и держать их означало бы дать
        аудитору зафиксировать разбор позапрошлого кадра."""
        self._chat(chat_id).proposal = proposal

    def proposal(self, chat_id: int) -> Proposal | None:
        """Посмотреть, не забирая: перелистывание страниц ручного перечня не
        должно съедать предложение."""
        return self._chat(chat_id).proposal

    def take_proposal(self, chat_id: int) -> Proposal | None:
        """Забрать предложение: запись по нему уже сделана, второй раз нельзя."""
        chat = self._chat(chat_id)
        taken = chat.proposal
        chat.proposal = None
        return taken

    def forget(self, chat_id: int) -> None:
        """Забыть всё: началась новая проверка, старые кнопки не должны стрелять."""
        self._chats.pop(chat_id, None)
