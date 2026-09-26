"""FastAPI transport; business behavior lives in MealAgent."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.service import MealAgent, UnknownSession, UnknownUser
from app.api.openai_compat import (
    ChatCompletionsRequest,
    OpenAIRequestError,
    completion_body,
    completion_identity,
    error_body,
    resolve_agent_request,
    stream_events,
)
from app.domain.models import ChatResult
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM, LLMOutputError, LLMUnavailable
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.sessions import SessionConflict, SessionStore
from app.infrastructure.settings import Settings
from app.infrastructure.synthetic import load_synthetic_catalog


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    request_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank")
        return value.strip()


class CapacityExceeded(Exception):
    """The process-wide request capacity could not be acquired quickly."""


def create_app(
    settings: Settings | None = None,
    llm: BaseLLM | None = None,
    catalog: DataCatalog | None = None,
    store: SessionStore | None = None,
) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        data = catalog if catalog is not None else load_synthetic_catalog()
        provider = llm or DeepSeekLLM(
            api_key=config.deepseek_api_key.get_secret_value(),
            model=config.deepseek_model, base_url=config.deepseek_base_url,
            timeout_seconds=config.llm_timeout_seconds,
        )
        application.state.agent = MealAgent(
            data, store or SessionStore(config.database_path), provider
        )
        application.state.capacity = asyncio.Semaphore(8)
        try:
            yield
        finally:
            if llm is None:
                await provider.aclose()

    application = FastAPI(
        title="方太个性化膳食规划 Agent",
        version="0.3.0",
        description="合成画像比赛 Demo：主动澄清、库内单餐规划、换菜及可追溯营养卡片。请本地运行。",
        lifespan=lifespan,
    )

    async def run_agent(
        user_id: int,
        message: str,
        session_id: str | None,
        request_id: str | None,
    ) -> ChatResult:
        semaphore = application.state.capacity
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=0.25)
        except TimeoutError:
            raise CapacityExceeded from None
        try:
            return await application.state.agent.chat(
                user_id=user_id,
                message=message,
                session_id=session_id,
                request_id=request_id,
            )
        finally:
            semaphore.release()

    @application.exception_handler(OpenAIRequestError)
    async def openai_error_handler(_: Request, error: OpenAIRequestError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error_body(error))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> Response:
        if request.url.path != "/v1/chat/completions":
            return await request_validation_exception_handler(request, error)
        details = error.errors()
        parameter = None
        if details:
            parameter = ".".join(str(part) for part in details[0].get("loc", [])[1:]) or None
        public_error = OpenAIRequestError(
            422,
            "请求字段不符合当前 Chat Completions 文本接口约定。",
            "invalid_request",
            parameter,
        )
        return JSONResponse(status_code=422, content=error_body(public_error))

    @application.get("/health")
    async def health() -> dict:
        agent = application.state.agent
        return {
            "status": "ok",
            "recipe_count": len(agent.catalog.recipes),
            "profile_count": len(agent.catalog.profiles),
            "profile_data_scope": sorted({p.data_scope for p in agent.catalog.profiles.values()}),
            "llm_configured": llm is not None or bool(config.deepseek_api_key.get_secret_value()),
            "model": config.deepseek_model,
            "tools": agent.tools.names,
        }

    @application.get("/demo/profiles")
    async def demo_profiles() -> dict:
        return {
            "data_scope": "synthetic",
            "profiles": [
                {"user_id": profile.user_id, "label": f"合成画像 {profile.user_id}",
                 "allergies": profile.allergies, "health_goals": profile.health_goals,
                 "preferences": profile.preferences}
                for profile in application.state.agent.catalog.profiles.values()
                if profile.data_scope == "synthetic"
            ],
        }

    @application.post("/chat", response_model=ChatResult)
    async def chat(request: ChatRequest) -> ChatResult:
        try:
            return await run_agent(**request.model_dump())
        except CapacityExceeded:
            raise HTTPException(
                429, detail={"code": "BUSY", "message": "服务繁忙，请稍后重试。"}
            ) from None
        except (UnknownUser, UnknownSession) as error:
            raise HTTPException(404, detail={"code": "NOT_FOUND", "message": str(error)}) from None
        except SessionConflict as error:
            raise HTTPException(409, detail={"code": "SESSION_CONFLICT", "message": str(error)}) from None
        except LLMOutputError:
            raise HTTPException(
                502, detail={"code": "LLM_INVALID_OUTPUT", "message": "模型未返回可验证的需求结构，请重试。"}
            ) from None
        except LLMUnavailable as error:
            if error.code == "original_profile_blocked":
                raise HTTPException(403, detail={
                    "code": "ORIGINAL_PROFILE_BLOCKED",
                    "message": "真实模型联调仅允许合成画像；原始档案请使用本地验收入口。",
                }) from None
            raise HTTPException(
                503, detail={"code": "LLM_UNAVAILABLE", "message": "模型服务暂不可用，请检查配置或稍后重试。"}
            ) from None

    @application.post("/v1/chat/completions", include_in_schema=True)
    async def chat_completions(
        request: ChatCompletionsRequest,
        x_user_id: str | None = Header(default=None, alias="X-User-ID"),
        x_session_id: str | None = Header(default=None, alias="X-Session-ID"),
        x_client_request_id: str | None = Header(
            default=None, alias="X-Client-Request-Id"
        ),
    ) -> Response:
        resolved = resolve_agent_request(
            request,
            header_user_id=x_user_id,
            header_session_id=x_session_id,
            header_request_id=x_client_request_id,
        )
        try:
            result = await run_agent(
                user_id=resolved.user_id,
                message=resolved.message,
                session_id=resolved.session_id,
                request_id=resolved.request_id,
            )
        except CapacityExceeded:
            raise OpenAIRequestError(
                429,
                "服务繁忙，请稍后重试。",
                "rate_limit_exceeded",
                error_type="rate_limit_error",
            ) from None
        except (UnknownUser, UnknownSession) as error:
            raise OpenAIRequestError(404, str(error), "not_found") from None
        except SessionConflict as error:
            raise OpenAIRequestError(409, str(error), "session_conflict") from None
        except LLMOutputError:
            raise OpenAIRequestError(
                502,
                "模型未返回可验证的需求结构，请重试。",
                "llm_invalid_output",
                error_type="server_error",
            ) from None
        except LLMUnavailable as error:
            if error.code == "original_profile_blocked":
                raise OpenAIRequestError(
                    403,
                    str(error),
                    "original_profile_blocked",
                    error_type="permission_error",
                ) from None
            raise OpenAIRequestError(
                503,
                "模型服务暂不可用，请检查配置或稍后重试。",
                "llm_unavailable",
                error_type="server_error",
            ) from None

        completion_id, created, server_request_id = completion_identity()
        headers = {
            "X-Request-ID": server_request_id,
            "X-Session-ID": result.conversation_state.session_id,
        }
        if not request.stream:
            return JSONResponse(
                content=completion_body(result, completion_id, created), headers=headers
            )
        headers.update(
            {
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )
        return StreamingResponse(
            stream_events(result, completion_id, created),
            media_type="text/event-stream",
            headers=headers,
        )

    return application


app = create_app()
