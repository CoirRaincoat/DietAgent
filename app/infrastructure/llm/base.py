"""Provider-independent language model boundary and safe public failures."""

from abc import ABC, abstractmethod

from app.domain.models import Intent, SessionState, UserProfile


class LLMUnavailable(Exception):
    """An upstream failure with a stable, non-sensitive public code."""

    _MESSAGES = {
        "timeout": "语言模型请求超时，请稍后重试。",
        "authentication": "语言模型服务认证失败。",
        "rate_limited": "语言模型服务繁忙，请稍后重试。",
        "upstream": "语言模型服务暂时不可用。",
        "network": "暂时无法连接语言模型服务。",
        "request_rejected": "语言模型服务未接受该请求。",
        "invalid_output": "语言模型返回的内容未通过校验。",
        "original_profile_blocked": "真实模型联调仅允许合成画像；原始档案仅用于本地验收。",
    }

    def __init__(self, code: str = "upstream") -> None:
        self.code = code if code in self._MESSAGES else "upstream"
        super().__init__(self._MESSAGES[self.code])


class LLMOutputError(LLMUnavailable):
    """An incomplete, malformed, or semantically invalid model response."""

    def __init__(self) -> None:
        super().__init__("invalid_output")


class BaseLLM(ABC):
    @abstractmethod
    async def parse(
        self, message: str, state: SessionState, profile: UserProfile
    ) -> Intent:
        """Extract additions to the existing constraints, never a replacement state."""

    @abstractmethod
    async def explain(self, facts: dict[str, str]) -> list[str]:
        """Select ordered fact IDs; the caller renders the verified sentences."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release resources owned by the adapter."""
