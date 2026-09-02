"""T031: единственная точка вызова провайдера — `client.ask_model`.

`_new_client` — «шов, которым тесты подменяют вызов» (см. докстринг модуля):
здесь он подменяется на фальшивый клиент, у которого `.responses.create`
возвращает заранее заданный ответ или бросает ошибку. Сеть в тестах не
участвует ни разу — это отдельная (дорогая, оплачиваемая) проверка вручную.

Любой отказ провайдера — сеть, испорченный JSON, ответ не-объектом — обязан
превращаться в `ModelUnavailable`: контракт блока требует, чтобы недоступная
модель не роняла проверку, а уводила бота на ручной выбор кнопками.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from openai import OpenAIError

from src.recognize.client import ask_model
from src.recognize.config import RecognizeSettings
from src.recognize.errors import ModelUnavailable

_SCHEMA = {"type": "object", "properties": {}, "required": []}


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
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class _FakeResponse:
    output_text: str
    usage: _FakeUsage | None = None


@dataclass
class _FakeResponses:
    """Подменяет `client.responses`. `calls` копит переданные аргументы для проверки."""

    answer: _FakeResponse | None = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.answer is not None
        return self.answer


@dataclass
class _FakeClient:
    responses: _FakeResponses


class _FakeProviderError(OpenAIError):
    """Любой отказ провайдера — сеть, ключ, лимит — это подкласс `OpenAIError`."""


def _patch_client(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr("src.recognize.client._new_client", lambda settings: fake)


def test_ключ_не_задан_даёт_отказ_без_обращения_к_сети() -> None:
    # Act / Assert: `_new_client` не подменён — значит дошли бы до настоящего
    # OpenAI-клиента, если бы отказ не случился раньше.
    with pytest.raises(ModelUnavailable, match="OPENAI_API_KEY"):
        ask_model(
            instructions="сис",
            question="вопрос",
            schema=_SCHEMA,
            photo=None,
            settings=_settings(api_key=""),
        )


def test_успешный_ответ_разбирается_и_расход_возвращается(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    fake_responses = _FakeResponses(
        answer=_FakeResponse(
            output_text='{"records": [], "question": ""}',
            usage=_FakeUsage(input_tokens=100, output_tokens=20),
        )
    )
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act
    answer = ask_model(
        instructions="сис",
        question="вопрос",
        schema=_SCHEMA,
        photo=None,
        settings=_settings(),
    )

    # Assert
    assert answer.payload == {"records": [], "question": ""}
    assert answer.usage == {"input": 100, "output": 20}


def test_usage_отсутствует_даёт_пустой_словарь(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    fake_responses = _FakeResponses(
        answer=_FakeResponse(output_text='{"records": [], "question": ""}', usage=None)
    )
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act
    answer = ask_model(
        instructions="сис", question="вопрос", schema=_SCHEMA, photo=None, settings=_settings()
    )

    # Assert
    assert answer.usage == {}


def test_кадр_передан_только_когда_есть_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    fake_responses = _FakeResponses(
        answer=_FakeResponse(output_text='{"records": [], "question": ""}')
    )
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act: без фото
    ask_model(
        instructions="сис", question="вопрос", schema=_SCHEMA, photo=None, settings=_settings()
    )
    # Act: с фото
    ask_model(
        instructions="сис", question="вопрос", schema=_SCHEMA, photo=b"jpeg", settings=_settings()
    )

    # Assert
    без_фото, с_фото = fake_responses.calls
    assert len(без_фото["input"][0]["content"]) == 1, "без фото — только текст"
    assert len(с_фото["input"][0]["content"]) == 2, "с фото — текст и кадр"
    assert с_фото["input"][0]["content"][1]["type"] == "input_image"


def test_model_из_аргумента_перекрывает_настройки(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: T035 гоняет один и тот же запрос по нескольким моделям
    fake_responses = _FakeResponses(
        answer=_FakeResponse(output_text='{"records": [], "question": ""}')
    )
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act
    ask_model(
        instructions="сис",
        question="вопрос",
        schema=_SCHEMA,
        photo=None,
        settings=_settings(model="gpt-5.6-sol"),
        model="gpt-4o-mini",
    )

    # Assert
    assert fake_responses.calls[0]["model"] == "gpt-4o-mini"


def test_отказ_провайдера_это_ModelUnavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    fake_responses = _FakeResponses(error=_FakeProviderError("лимит запросов"))
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act / Assert
    with pytest.raises(ModelUnavailable, match="лимит запросов"):
        ask_model(
            instructions="сис", question="вопрос", schema=_SCHEMA, photo=None, settings=_settings()
        )


def test_испорченный_json_это_ModelUnavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    fake_responses = _FakeResponses(answer=_FakeResponse(output_text="не json{"))
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act / Assert
    with pytest.raises(ModelUnavailable, match="строгая схема"):
        ask_model(
            instructions="сис", question="вопрос", schema=_SCHEMA, photo=None, settings=_settings()
        )


def test_ответ_не_объект_это_ModelUnavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: строгая схема просила объект, а не список
    fake_responses = _FakeResponses(answer=_FakeResponse(output_text="[]"))
    _patch_client(monkeypatch, _FakeClient(responses=fake_responses))

    # Act / Assert
    with pytest.raises(ModelUnavailable, match="не объект"):
        ask_model(
            instructions="сис", question="вопрос", schema=_SCHEMA, photo=None, settings=_settings()
        )
