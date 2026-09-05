"""Настройки разбора. Имя модели — параметр, никогда не константа в коде.

Смена провайдера или модели должна быть правкой `.env`, а не рефакторингом
(решение D010). Поэтому всё, что относится к вызову, собрано здесь, а сам вызов
живёт в единственном модуле `client.py`.

Значения по умолчанию заданы для тех параметров, у которых есть осмысленный
дефолт (модель, таймаут, пороги). Ключ по умолчанию не подставляется: без него
разбор обязан честно сказать, что не работает, а не молча вернуть пустоту.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import RecognizeConfigError

MODEL_VAR = "RECOGNIZE_MODEL"
TRANSCRIBE_MODEL_VAR = "RECOGNIZE_TRANSCRIBE_MODEL"
API_KEY_VAR = "OPENAI_API_KEY"
TIMEOUT_VAR = "RECOGNIZE_TIMEOUT"
MIN_CONFIDENCE_VAR = "RECOGNIZE_MIN_CONFIDENCE"
MAX_CANDIDATES_VAR = "RECOGNIZE_MAX_CANDIDATES"
FFMPEG_VAR = "FFMPEG_BIN"

#: Флагман линейки — решение D013. Дешёвая модель экономит 93 цента на проверке,
#: которая занимает у человека четыре часа; цена ошибки несопоставима. Выбор
#: подтверждён замером на боевых данных — таблица в `docs/forge/blocks/recognize.md`.
DEFAULT_MODEL = "gpt-5.6-sol"

#: Транскрипция голосовых — решение D008.
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"

DEFAULT_TIMEOUT = 90.0

#: Ниже этого — предложение показывается, но помечается «нужен человек».
#: Разведка дала 0.55 там, где правильного кода в перечне не было, и 0.99 там,
#: где он был: уверенность различает эти случаи и потому используется как сигнал.
DEFAULT_MIN_CONFIDENCE = 0.6

#: Больше пяти кнопок аудитор на телефоне не разберёт — контракт блока.
DEFAULT_MAX_CANDIDATES = 5

#: Язык формулировок и текста пунктов по умолчанию. Параметр, а не константа
#: смысла: у проверки язык отчёта хранится отдельно от языка интерфейса и языка
#: речи (принцип проекта «язык — параметр»), это лишь запасное значение, когда
#: вызывающий код его не передал.
DEFAULT_LANG = "ru"

#: «Проверки нет» — законное значение `chat_id` там, где справочники и карта
#: слов читаются вне выезда: замеры по выгрузкам `examples/`, команды методики
#: MCP, справочники до начала проверки. Правильный ответ там — действующий
#: каталог, и сказать это надо вслух.
#:
#: Умолчанием это НЕ является и становиться не должно (T226): `chat_id` во всём
#: блоке обязателен, потому что молчаливый откат на действующую методику и есть
#: тот дефект, ради которого всё делается. Забытый параметр обязан не
#: собираться, а «проверки нет» — быть названо. Приём взят у `src/bot/zones.py`
#: (T225), где та же константа стоила блоку десяти упавших тестов замера, пока
#: разница между двумя случаями не была названа явно.
NO_CHAT: int | None = None


@dataclass(frozen=True)
class RecognizeSettings:
    """Разобранное окружение блока разбора."""

    model: str
    transcribe_model: str
    api_key: str
    timeout: float
    min_confidence: float
    max_candidates: int
    ffmpeg: str


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RecognizeConfigError(
            f"Переменная {name} должна быть числом, а в окружении «{raw}». "
            f"Молчаливый откат на {default} означал бы, что разбор работает не на тех "
            f"настройках, которые человек задал"
        ) from exc


def load_recognize_settings(env: Mapping[str, str] | None = None) -> RecognizeSettings:
    """Собрать настройки. Отсутствующий ключ — не отказ здесь, а отказ при вызове."""
    src = os.environ if env is None else env
    return RecognizeSettings(
        model=(src.get(MODEL_VAR) or "").strip() or DEFAULT_MODEL,
        transcribe_model=(src.get(TRANSCRIBE_MODEL_VAR) or "").strip() or DEFAULT_TRANSCRIBE_MODEL,
        api_key=(src.get(API_KEY_VAR) or "").strip(),
        timeout=_number(src, TIMEOUT_VAR, DEFAULT_TIMEOUT),
        min_confidence=_number(src, MIN_CONFIDENCE_VAR, DEFAULT_MIN_CONFIDENCE),
        max_candidates=int(_number(src, MAX_CANDIDATES_VAR, DEFAULT_MAX_CANDIDATES)),
        ffmpeg=(src.get(FFMPEG_VAR) or "").strip() or "ffmpeg",
    )
