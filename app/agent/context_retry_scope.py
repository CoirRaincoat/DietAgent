"""Failed local edits cannot become whole-meal retry authority."""

import re
from collections.abc import Sequence

from app.domain.models import Intent, Recipe, SessionState

_MENTION = re.compile(r"如果|假如|例如|比如|解释|是否|能否|[?？\"'“”‘’]")
_WHOLE_MENU = re.compile(
    r"^(?:请|我允许|允许)?(?:整餐调整|调整整餐|整餐重新规划|重新规划(?:这餐|本餐)?菜单|"
    r"重新安排(?:这餐|本餐)?菜单|重新推荐(?:这餐|本餐)?菜单|安排新一餐菜单)$"
)


def resolve_replacement_target(
    intent: Intent, current: Sequence[Recipe]
) -> tuple[Intent, str | None]:
    """Bind names only to this menu; duplicate names/soups are not unique slots.

    Resolve before validation can interrupt planning, retaining new safety
    facts on errors. A supplied name and index must refer to the same slot.
    Titles/aliases do not search the whole catalog or create dish identities.
    """
    if intent.action != "replace":
        return intent, None
    slot = intent.replace_slot
    if slot is not None and not 1 <= slot <= len(current):
        return intent, "指定的换菜序号不在当前菜单中，请明确菜位；未扩大为整餐调整。"
    if intent.replace_name:
        matches = [
            i + 1
            for i, recipe in enumerate(current)
            if (
                recipe.name == intent.replace_name
                or intent.replace_name in {"汤", "汤菜"}
                and "soup" in recipe.categories
            )
        ]
        if not matches or len(matches) != 1 and slot not in matches:
            return (
                intent,
                "菜名或汤位无法唯一对应当前菜单，请说明要换第几道菜；继续不授予整餐调整权限。",
            )
        if slot is not None and slot not in matches:
            return intent, "换菜名称与序号指向不同菜位，请明确一个目标；未擅自取舍或调整整餐。"
        slot = slot if slot is not None else matches[0]
    if slot is None:
        return intent, "请说明要换第几道菜；未扩大为整餐调整。"
    return intent.model_copy(update={"replace_slot": slot}), None


def preserve_context_retry_scope(state: SessionState, intent: Intent, message: str) -> Intent:
    pending = state.pending_context_replacement
    if pending is None or intent.action in {"explain", "clarify"}:
        return intent
    if intent.action == "reject":
        state.pending_context_replacement = None
        return intent
    if intent.action == "replace":
        # A new explicit edit has its own scope, validated by existing consumers.
        return intent
    commands = (
        []
        if _MENTION.search(message)
        else [
            re.sub(r"\s+", "", part)
            for part in re.split(r"[，,。；;！!\r\n]+", message)
            if _WHOLE_MENU.fullmatch(re.sub(r"\s+", "", part))
        ]
    )
    if any("重新" in command or "新一餐" in command for command in commands):
        # An explicit new whole-menu task supersedes the unresolved edit.
        state.pending_context_replacement = None
        return intent
    if commands:
        pending.whole_menu_authorized = True
    if state.menu_ids != pending.menu_ids:
        return Intent(
            action="clarify",
            clarification="原局部换菜的菜单绑定已变化，请重新指定菜位；继续不授予整餐调整权限。",
        )
    if pending.whole_menu_authorized:
        if (
            not pending.target_confirmed
            or pending.replace_slot is None
            or not 1 <= pending.replace_slot <= len(pending.menu_ids)
        ):
            return Intent(
                action="clarify",
                clarification="已记录整餐调整许可，但原换菜目标尚未唯一确认；请明确菜位，或回复“重新规划本餐菜单”以新任务替代原换菜请求。",
            )
        result = intent.model_copy(
            update={"action": "plan", "replace_slot": None, "replace_name": None}
        )
        result._replacement_exclusions = frozenset({pending.menu_ids[pending.replace_slot - 1]})
        return result
    return intent.model_copy(
        update={
            "action": "replace",
            "replace_slot": pending.replace_slot,
            "replace_name": pending.replace_name,
        }
    )
