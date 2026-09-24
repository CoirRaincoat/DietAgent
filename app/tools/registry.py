"""A bounded allowlist of real program tools, invoked by the agent workflow.

No tool accepts code, shell commands, arbitrary URLs or model-generated SQL.
Tool events contain counts and statuses, not raw personal health records.
"""

from collections.abc import Callable
from typing import Any

from app.domain.models import ToolEvent


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, function: Callable[..., Any]) -> None:
        if name in self._tools:
            raise ValueError(f"Duplicate tool: {name}")
        self._tools[name] = function

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def call(self, name: str, events: list[ToolEvent], **arguments: Any) -> Any:
        if name not in self._tools:
            raise ValueError("Tool is not registered")
        result = self._tools[name](**arguments)
        summary: dict[str, Any] = {}
        if isinstance(result, list):
            summary["count"] = len(result)
        elif hasattr(result, "recipes"):
            summary["count"] = len(result.recipes)
            summary["success"] = result.failure is None
        elif hasattr(result, "recipe_ids"):
            summary["count"] = len(result.recipe_ids)
            summary["analysis_type"] = result.analysis_type
        elif hasattr(result, "recipe_id"):
            summary["count"] = 1
            summary["analysis_type"] = result.analysis_type
        events.append(ToolEvent(name=name, summary=summary))
        return result
