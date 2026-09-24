"""FastAPI transport; business behavior lives in MealAgent."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.agent.service import MealAgent, UnknownSession, UnknownUser
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
        semaphore = application.state.capacity
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=0.25)
        except TimeoutError:
            raise HTTPException(429, detail={"code": "BUSY", "message": "服务繁忙，请稍后重试。"}) from None
        try:
            return await application.state.agent.chat(**request.model_dump())
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
        finally:
            semaphore.release()

    return application


app = create_app()
