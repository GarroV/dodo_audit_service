"""T033: голосовое сообщение аудитора → текст.

Telegram отдаёт голосовые в OGG/Opus; API транскрипции формат не отвергает
впрямую, но в бою нам нужен ровно тот путь, что уже проверен вручную — WAV,
16 кГц, моно — поэтому перед вызовом файл перекодируется `ffmpeg`. Он вызван
подпроцессом, а не библиотекой: `ffmpeg` уже выбран решением D008 и живёт в
образе бота (задача T070), тянуть для этого же ещё и python-обвязку незачем.

Любой отказ — не найден `ffmpeg`, кодек не разобрал файл, сеть, пустой текст —
превращается в `TranscriptionFailed`. Проверку это не роняет: аудитора просят
написать текстом, а не останавливают фиксацию (контракт блока).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from openai import OpenAI, OpenAIError

from .config import RecognizeSettings, load_recognize_settings
from .errors import TranscriptionFailed

#: Таймаут перекодировки. Голосовые аудитора короткие (секунды-десятки
#: секунд); минута — щедрый запас на медленный старт ffmpeg в контейнере.
REENCODE_TIMEOUT = 60.0

#: Частота и число каналов на выходе перекодировки. API транскрипции сам
#: понижает частоту при необходимости, но заранее — меньше данных на вход.
_SAMPLE_RATE = "16000"
_CHANNELS = "1"


def _reencode(audio: bytes, ffmpeg: str) -> bytes:
    """OGG/Opus (или что угодно, что понимает ffmpeg) → WAV."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.audio"
        dst = Path(tmp) / "out.wav"
        src.write_bytes(audio)
        try:
            subprocess.run(  # noqa: S603 — путь к бинарю и флаги собраны здесь, ввода извне нет
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(src),
                    "-ar",
                    _SAMPLE_RATE,
                    "-ac",
                    _CHANNELS,
                    str(dst),
                ],
                check=True,
                capture_output=True,
                timeout=REENCODE_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise TranscriptionFailed(
                f"Не найден ffmpeg ({ffmpeg}). Голосовое не перекодировано, "
                "попросите аудитора написать текстом"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionFailed(
                f"Перекодировка не уложилась в {REENCODE_TIMEOUT:.0f} с. "
                "Голосовое не разобрано, попросите написать текстом"
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
            raise TranscriptionFailed(
                f"ffmpeg не смог перекодировать голосовое: {stderr.strip() or exc}. "
                "Попросите написать текстом"
            ) from exc
        data = dst.read_bytes()
        if not data:
            raise TranscriptionFailed(
                "Перекодировка дала пустой файл. Голосовое не разобрано, попросите написать текстом"
            )
        return data


def transcribe(audio: bytes, *, settings: RecognizeSettings | None = None) -> str:
    """Голосовое → текст. Пустая расшифровка — тоже отказ: разбирать нечего.

    `settings` — тот же шов, что и у `classify`: тесты подменяют настройки,
    бот вызывает без аргумента и получает конфиг из окружения.
    """
    cfg = settings or load_recognize_settings()
    if not cfg.api_key:
        raise TranscriptionFailed(
            "Не задан ключ модели (OPENAI_API_KEY). Голосовое не распознано, "
            "попросите аудитора написать текстом"
        )
    wav = _reencode(audio, cfg.ffmpeg)

    client = OpenAI(api_key=cfg.api_key, timeout=cfg.timeout)
    try:
        result = client.audio.transcriptions.create(
            model=cfg.transcribe_model,
            file=("audio.wav", wav, "audio/wav"),
        )
    except OpenAIError as exc:
        raise TranscriptionFailed(f"Транскрипция не ответила: {exc}") from exc

    text = result.text.strip()
    if not text:
        raise TranscriptionFailed(
            "Транскрипция вернула пустой текст. Голосовое, возможно, без речи — "
            "попросите написать текстом"
        )
    return text
