"""T033: голосовое → текст. Перекодировка ffmpeg + вызов транскрипции.

Сеть и внешний `ffmpeg` в этих тестах не участвуют: `_reencode` подменяется
там, где нужен только факт вызова API, а `OpenAI.audio.transcriptions.create`
подменяется там, где проверяется именно перекодировка. Оба реальных шага —
живой `ffmpeg` и настоящий API — прогнаны вручную один раз (см. журнал блока):
голос, синтезированный `say`, закодирован в OGG/Opus (формат Telegram) и
успешно распознан.

Любой отказ на любом шаге обязан стать `TranscriptionFailed`, а не уронить
проверку — контракт блока: «сбой транскрипции не роняет проверку, а просит
написать текстом».
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any

import pytest
from openai import OpenAIError
from openai.types.audio.transcription import Transcription

from src.recognize.config import RecognizeSettings
from src.recognize.errors import TranscriptionFailed
from src.recognize.transcribe import transcribe


def _settings(**overrides: Any) -> RecognizeSettings:
    base = dict(
        model="gpt-5.6-sol",
        transcribe_model="gpt-4o-mini-transcribe",
        api_key="sk-test",
        timeout=90.0,
        min_confidence=0.6,
        max_candidates=5,
        ffmpeg="ffmpeg",
    )
    base.update(overrides)
    return RecognizeSettings(**base)


@dataclass
class _FakeTranscriptions:
    result: Transcription | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> Transcription:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class _FakeAudio:
    transcriptions: _FakeTranscriptions


@dataclass
class _FakeClient:
    audio: _FakeAudio


class _FakeProviderError(OpenAIError):
    pass


def _patch_openai(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("src.recognize.transcribe.OpenAI", lambda **_kw: fake)


def _patch_reencode(monkeypatch: pytest.MonkeyPatch, wav: bytes = b"RIFF....WAVEfmt ") -> None:
    monkeypatch.setattr("src.recognize.transcribe._reencode", lambda audio, ffmpeg: wav)


def test_ключ_не_задан_отказ_без_перекодировки(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: `_reencode` не подменён — значит его позвали бы, если бы
    # отказ по ключу не сработал раньше него
    called = False

    def _boom(audio: bytes, ffmpeg: str) -> bytes:
        nonlocal called
        called = True
        return b""

    monkeypatch.setattr("src.recognize.transcribe._reencode", _boom)

    # Act / Assert
    with pytest.raises(TranscriptionFailed, match="OPENAI_API_KEY"):
        transcribe(b"audio", settings=_settings(api_key=""))
    assert called is False


def test_успешная_транскрипция_возвращает_текст(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _patch_reencode(monkeypatch)
    fake_transcriptions = _FakeTranscriptions(result=Transcription(text="  пол грязный  "))
    _patch_openai(monkeypatch, _FakeClient(audio=_FakeAudio(transcriptions=fake_transcriptions)))

    # Act
    text = transcribe(b"ogg-bytes", settings=_settings())

    # Assert: текст обрезан от пробелов
    assert text == "пол грязный"
    assert fake_transcriptions.calls[0]["model"] == "gpt-4o-mini-transcribe"


def test_модель_транскрипции_берётся_из_настроек(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _patch_reencode(monkeypatch)
    fake_transcriptions = _FakeTranscriptions(result=Transcription(text="текст"))
    _patch_openai(monkeypatch, _FakeClient(audio=_FakeAudio(transcriptions=fake_transcriptions)))

    # Act
    transcribe(b"ogg", settings=_settings(transcribe_model="whisper-1"))

    # Assert
    assert fake_transcriptions.calls[0]["model"] == "whisper-1"


def test_пустой_текст_это_отказ(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: голосовое без разборчивой речи — валидный, но бесполезный ответ
    _patch_reencode(monkeypatch)
    fake_transcriptions = _FakeTranscriptions(result=Transcription(text="   "))
    _patch_openai(monkeypatch, _FakeClient(audio=_FakeAudio(transcriptions=fake_transcriptions)))

    # Act / Assert
    with pytest.raises(TranscriptionFailed, match="пустой текст"):
        transcribe(b"ogg", settings=_settings())


def test_отказ_провайдера_это_TranscriptionFailed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _patch_reencode(monkeypatch)
    fake_transcriptions = _FakeTranscriptions(error=_FakeProviderError("лимит запросов"))
    _patch_openai(monkeypatch, _FakeClient(audio=_FakeAudio(transcriptions=fake_transcriptions)))

    # Act / Assert
    with pytest.raises(TranscriptionFailed, match="лимит запросов"):
        transcribe(b"ogg", settings=_settings())


# --- _reencode: перекодировка ffmpeg (мок subprocess) -----------------------


def test_ffmpeg_не_найден_это_TranscriptionFailed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    def _missing(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr("subprocess.run", _missing)

    # Act / Assert
    with pytest.raises(TranscriptionFailed, match="Не найден ffmpeg"):
        transcribe(b"audio", settings=_settings(ffmpeg="/no/such/ffmpeg"))


def test_ffmpeg_таймаут_это_TranscriptionFailed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    def _slow(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60)

    monkeypatch.setattr("subprocess.run", _slow)

    # Act / Assert
    with pytest.raises(TranscriptionFailed, match="не уложилась"):
        transcribe(b"audio", settings=_settings())


def test_ffmpeg_падает_с_кодом_ошибки_это_TranscriptionFailed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: испорченный файл — ffmpeg отказывается его разбирать
    def _bad_codec(*args: Any, **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(
            returncode=1, cmd="ffmpeg", stderr=b"Invalid data found when processing input"
        )

    monkeypatch.setattr("subprocess.run", _bad_codec)

    # Act / Assert
    with pytest.raises(TranscriptionFailed, match="не смог перекодировать"):
        transcribe(b"corrupted-not-really-audio", settings=_settings())


def test_реальный_ffmpeg_перекодирует_синтезированный_голос(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """Живой прогон `_reencode` настоящим `ffmpeg` — без сети и без API.

    Пропускается, если на машине нет `ffmpeg`: перекодировка — часть образа
    бота (T070), а не гарантия окружения тестов.
    """
    import shutil

    from src.recognize.transcribe import _reencode

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg недоступен на этой машине")

    # Arrange: минимальный валидный OGG/Opus, который ffmpeg точно разберёт —
    # генерируем его тем же ffmpeg (синус, полсекунды), а не тащим бинарник
    # в репозиторий как тестовые данные
    src = subprocess.run(
        [  # noqa: S607 — команда захардкожена здесь, ввода извне нет
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.5",
            "-c:a",
            "libopus",
            "-f",
            "ogg",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout

    # Act
    wav = _reencode(src, "ffmpeg")

    # Assert: валидный WAV-заголовок, не пустой файл
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
