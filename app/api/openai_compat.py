"""OpenAI Chat Completions compatibility contracts and wire rendering."""

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from time import time
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models import ChatResult

MODEL_ID = "fangtai-meal-agent"
IdentityValue = TypeVar("IdentityValue", int, str)


class OpenAIMessage(BaseModel):
    """Supported incremental-mode message."""

    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["user"]
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """Reject messages that become empty after whitespace normalization."""
        if not value.strip():
            raise ValueError("message content cannot be blank")
        return value.strip()


class FangtaiContext(BaseModel):
    """Optional competition-specific extensions passed through ``extra_body``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    user_id: int | None = Field(default=None, ge=1)
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    client_turn_id: str | None = Field(default=None, min_length=1, max_length=128)


class StreamOptions(BaseModel):
    """Explicit supported subset of OpenAI stream options."""

    model_config = ConfigDict(extra="forbid", strict=True)

    include_usage: Literal[False] = False


class ChatCompletionsRequest(BaseModel):
    """Text-only, incremental subset of the OpenAI Chat Completions request."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "examples": [
                {
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": "1人晚餐，没有其他忌口"}],
                    "stream": True,
                    "user": "900001",
                }
            ]
        },
    )

    model: Literal["fangtai-meal-agent"]
    messages: list[OpenAIMessage] = Field(min_length=1, max_length=1)
    stream: bool = False
    user: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$", max_length=20)
    stream_options: StreamOptions | None = None
    context: FangtaiContext | None = None
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def stream_options_require_streaming(self) -> "ChatCompletionsRequest":
        """Match the official constraint that stream options require streaming."""
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
        return self


class OpenAIRequestError(Exception):
    """Safe API error with OpenAI-compatible public fields."""

    def __init__(
        self,
        status_code: int,
        message: str,
        code: str,
        param: str | None = None,
        error_type: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.param = param
        self.error_type = error_type


@dataclass(frozen=True)
class AgentRequest:
    """Resolved business request after comparing all supported identity sources."""

    user_id: int
    message: str
    session_id: str | None
    request_id: str | None


def resolve_agent_request(
    request: ChatCompletionsRequest,
    header_user_id: str | None,
    header_session_id: str | None,
    header_request_id: str | None,
) -> AgentRequest:
    """Resolve extensions deterministically and reject conflicting duplicate values."""
    header_user = _positive_int(header_user_id, "X-User-ID")
    body_user = int(request.user) if request.user is not None else None
    context_user = request.context.user_id if request.context else None
    user_id = _one_value(
        [("user", body_user), ("context.user_id", context_user), ("X-User-ID", header_user)],
        required=True,
        missing_message="缺少用户标识；请提供 user、context.user_id 或 X-User-ID。",
    )

    context_session = request.context.session_id if request.context else None
    session_id = _one_value(
        [
            ("session_id", request.session_id),
            ("context.session_id", context_session),
            ("X-Session-ID", _session_id(header_session_id, "X-Session-ID")),
        ]
    )

    context_request = request.context.client_turn_id if request.context else None
    request_id = _one_value(
        [
            ("request_id", request.request_id),
            ("context.client_turn_id", context_request),
            ("X-Client-Request-Id", _request_id(header_request_id, "X-Client-Request-Id")),
        ]
    )
    return AgentRequest(
        user_id=user_id,
        message=request.messages[0].content,
        session_id=session_id,
        request_id=request_id,
    )


def completion_identity() -> tuple[str, int, str]:
    """Create stable values shared by every chunk in one HTTP response."""
    return f"chatcmpl-{uuid4().hex}", int(time()), f"req_{uuid4().hex}"


def server_timing(result: ChatResult) -> str:
    """Expose allow-listed internal phases for diagnosis, never a claimed TTFT."""
    names = (
        ("total", "agent_total"),
        ("parse", "agent_parse"),
        ("retrieval_rules_planning", "planning"),
        ("explanation", "explanation"),
    )
    metrics = []
    for source, public in names:
        duration = result.timings_ms.get(source)
        if duration is None or not math.isfinite(duration) or duration < 0:
            continue
        metrics.append(f"{public};dur={duration:.2f}")
    return ", ".join(metrics)


def completion_body(result: ChatResult, completion_id: str, created: int) -> dict:
    """Render the non-streaming Chat Completions response subset."""
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.reason},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
    }


def stream_events(result: ChatResult, completion_id: str, created: int) -> Iterator[str]:
    """Yield standards-compatible SSE records from an already verified result."""
    yield _sse(_chunk(completion_id, created, {"role": "assistant", "content": ""}))
    for content in _text_chunks(result.reason):
        yield _sse(_chunk(completion_id, created, {"content": content}))
    yield _sse(_chunk(completion_id, created, {}, finish_reason="stop"))
    yield "data: [DONE]\n\n"


def error_body(error: OpenAIRequestError) -> dict:
    """Render a stable OpenAI-compatible error envelope."""
    return {
        "error": {
            "message": error.message,
            "type": error.error_type,
            "param": error.param,
            "code": error.code,
        }
    }


def _chunk(
    completion_id: str,
    created: int,
    delta: dict,
    finish_reason: str | None = None,
) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_ID,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
    }


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"


def _text_chunks(text: str, size: int = 24) -> Iterator[str]:
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _one_value(
    values: list[tuple[str, IdentityValue | None]],
    required: bool = False,
    missing_message: str = "",
) -> IdentityValue | None:
    present = [(name, value) for name, value in values if value is not None]
    unique = {value for _, value in present}
    if len(unique) > 1:
        names = "、".join(name for name, _ in present)
        raise OpenAIRequestError(409, f"多个入口提供了不一致的值：{names}。", "identity_conflict")
    if not present:
        if required:
            raise OpenAIRequestError(422, missing_message, "missing_user", "user")
        return None
    return present[0][1]


def _positive_int(value: str | None, field: str) -> int | None:
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or int(value) < 1:
        raise OpenAIRequestError(422, f"{field} 必须是正整数。", "invalid_user", field)
    return int(value)


def _session_id(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        raise OpenAIRequestError(
            422, f"{field} 必须是32位小写十六进制会话ID。", "invalid_session", field
        )
    return value


def _request_id(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not 1 <= len(value) <= 128:
        raise OpenAIRequestError(
            422, f"{field} 长度必须为1到128字符。", "invalid_request_id", field
        )
    return value
