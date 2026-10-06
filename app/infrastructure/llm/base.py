"""Provider-independent language model boundary and safe public failures."""

from abc import ABC, abstractmethod
from uuid import uuid4

from app.domain.generated_recipe import RecipeDraft
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
    """Rejected output with allowlisted diagnostics, never provider content."""

    _STAGES = {"response", "intent", "explanation"}
    _CATEGORIES = {
        "invalid_envelope", "incomplete_output", "invalid_json", "duplicate_key",
        "missing_field", "invalid_type", "out_of_range", "unknown_field",
        "invalid_action", "invalid_semantics", "schema_validation",
    }
    _FIELDS = {
        "unknown", "content", "choices", "finish_reason", "action",
        "excluded_ingredients", "revoke_exclusions", "revoke_confirmed", "revoke_cancelled",
        "allergies", "allergy_clarifications", "diner_updates",
        "preferred_ingredients", "health_goals", "preferences", "inventory", "no_spicy",
        "meal_type", "dish_count", "soup_count", "people", "restrictions_confirmed",
        "max_minutes", "clear_time_limit", "replace_slot", "replace_name", "query_terms",
        "clarification", "reason_ids", "restore_constraints",
    }

    def __init__(
        self, *, stage: str = "intent", field: str = "unknown",
        category: str = "schema_validation",
    ) -> None:
        self.stage = stage if stage in self._STAGES else "intent"
        self.field = field if field in self._FIELDS else "unknown"
        self.category = category if category in self._CATEGORIES else "schema_validation"
        self.request_id = uuid4().hex
        super().__init__("invalid_output")

    def diagnostics(self) -> dict[str, str]:
        """Public-safe correlation metadata; excludes messages, values and secrets."""
        return {
            "stage": self.stage, "field": self.field,
            "category": self.category, "request_id": self.request_id,
        }


class BaseLLM(ABC):
    async def propose_recipe(self, user_messages: list[str]) -> RecipeDraft | None:
        """Optional proposal capability; default never calls a remote provider."""
        return None

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
