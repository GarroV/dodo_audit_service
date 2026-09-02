"""T031: единственное место, где блок разговаривает с провайдером модели.

Решение D010: «вызов изолируется за одной функцией, чтобы смена провайдера была
правкой конфига». Поэтому `openai` импортируется только здесь; всё остальное в
блоке работает со словарями и знать про провайдера не обязано.

Любой отказ — сеть, ключ, лимит, таймаут, испорченный JSON — превращается в
`ModelUnavailable`. Проверку это не роняет: бот уходит на ручной выбор пункта
кнопками (задача T034).
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import OpenAI, OpenAIError
from openai.types.responses import (
    EasyInputMessageParam,
    ResponseInputImageParam,
    ResponseInputMessageContentListParam,
    ResponseInputTextParam,
    ResponseTextConfigParam,
)
from openai.types.responses.response_format_text_json_schema_config_param import (
    ResponseFormatTextJSONSchemaConfigParam,
)

from .config import RecognizeSettings
from .errors import ModelUnavailable

#: Имя схемы в запросе. Провайдеру оно нужно, на содержание не влияет.
SCHEMA_NAME = "audit_suggestions"

#: Кадр отдаётся провайдеру как есть, разрешение выбирает он сам. Замер на
#: боевом кадре: `detail=low` на флагмане стоит 315 токенов входа против 1463 у
#: `auto`, но срезает ровно ту мелочь, ради которой кадр и смотрят, — нагар под
#: лентой печи от целого металла отличается на увеличении.
IMAGE_DETAIL: Literal["auto"] = "auto"


@dataclass(frozen=True)
class ModelAnswer:
    """Разобранный ответ модели и расход на запрос."""

    payload: dict[str, Any]
    usage: dict[str, int] = field(default_factory=dict)


def _new_client(settings: RecognizeSettings) -> OpenAI:
    """Клиент провайдера. Отдельная функция — шов, которым тесты подменяют вызов."""
    if not settings.api_key:
        raise ModelUnavailable(
            "Не задан ключ модели (OPENAI_API_KEY). Разбор комментариев не работает, "
            "проверка продолжается ручным выбором пункта"
        )
    return OpenAI(api_key=settings.api_key, timeout=settings.timeout)


def _content(question: str, photo: bytes | None) -> ResponseInputMessageContentListParam:
    content: ResponseInputMessageContentListParam = [
        ResponseInputTextParam(type="input_text", text=question)
    ]
    if photo is not None:
        encoded = base64.b64encode(photo).decode("ascii")
        content.append(
            ResponseInputImageParam(
                type="input_image",
                image_url=f"data:image/jpeg;base64,{encoded}",
                detail=IMAGE_DETAIL,
            )
        )
    return content


def ask_model(
    *,
    instructions: str,
    question: str,
    schema: dict[str, Any],
    photo: bytes | None,
    settings: RecognizeSettings,
    model: str | None = None,
) -> ModelAnswer:
    """Спросить модель со строгой схемой ответа. Кадр уходит тем же запросом.

    `model` перекрывает имя из настроек — это нужно замеру точности (T035),
    который гоняет один и тот же запрос по нескольким моделям. В работе бота
    имя берётся из конфига и никогда из кода.
    """
    client = _new_client(settings)
    text_format = ResponseFormatTextJSONSchemaConfigParam(
        type="json_schema",
        name=SCHEMA_NAME,
        schema=schema,
        strict=True,
    )
    text_config: ResponseTextConfigParam = {"format": text_format}
    message: EasyInputMessageParam = {
        "role": "user",
        "content": _content(question, photo),
    }
    try:
        response = client.responses.create(
            model=model or settings.model,
            instructions=instructions,
            input=[message],
            text=text_config,
        )
    except OpenAIError as exc:
        raise ModelUnavailable(f"Модель не ответила: {exc}") from exc

    raw = response.output_text
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelUnavailable(
            f"Ответ модели не разобран как JSON, хотя запрошена строгая схема: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ModelUnavailable("Ответ модели — не объект, хотя запрошена строгая схема")

    usage = response.usage
    return ModelAnswer(
        payload=payload,
        usage=(
            {} if usage is None else {"input": usage.input_tokens, "output": usage.output_tokens}
        ),
    )
