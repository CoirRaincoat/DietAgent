"""Finite scene withdrawal, separate from a negative scene request or safety."""

import re
from dataclasses import dataclass

from app.agent.diners import DinerConflict, aggregate_constraints, find_diner
from app.domain.dining_scenes import SCENE_TAGS, scene_request_clauses
from app.domain.meal_context import MEAL_TAGS, negative_meal_request_clauses
from app.domain.models import DinerUpdate, Intent, SessionState

_TERMS = "|".join(re.escape(s) for s in sorted(SCENE_TAGS | MEAL_TAGS, key=lambda s: (-len(s), s)))
_SPLIT = re.compile(r"[、,，;；。.!！\r\n]+")
_MENTION = re.compile(r"如果|假如|比如|例如|解释|为什么|是否|能否|可以吗|[?？\"'“”‘’]")
_COMMAND = re.compile(
    r"^(?:(?P<before>本餐|这餐|我的|本人|我|[^取消撤销去掉]{1,12}的))?"
    r"(?:取消|撤销|去掉)"
    r"(?:(?P<after>本餐|这餐|我的|本人|我|[^不要]{1,12}的))?"
    r"(?P<negative>不要)?(?P<scene>" + _TERMS + r")"
    r"(?:场景要求|场景偏好|用餐要求|偏好|要求|场景)?$"
)


@dataclass(frozen=True)
class SceneWithdrawal:
    owner: str
    clause: str
    literal: str


def scene_withdrawals(message: str) -> tuple[SceneWithdrawal, ...]:
    """Only direct bounded clauses authorize source removal, not mentions."""
    if _MENTION.search(message):
        return ()
    found: list[SceneWithdrawal] = []
    for part in _SPLIT.split(message):
        literal = re.sub(r"\s+", "", part)
        match = _COMMAND.fullmatch(literal)
        if match is None or (match["before"] and match["after"]):
            continue
        if match["scene"] in MEAL_TAGS and not match["negative"]:
            continue  # Positive meal withdrawal is not a scene amendment.
        owner = match["before"] or match["after"] or "本餐"
        owner = owner.removesuffix("的")
        if owner == "这餐":
            owner = "本餐"
        value = ("不要" if match["negative"] else "") + match["scene"]
        found.append(SceneWithdrawal(owner, value, literal))
    return tuple(dict.fromkeys(found))


def _without(preferences: list[str], withdrawal: SceneWithdrawal) -> list[str]:
    result: list[str] = []
    for entry in preferences:
        # Split only standalone source clauses; never remove a substring from
        # a mixed/unknown requirement or thereby erase a hard restriction.
        parts = _SPLIT.split(entry)
        kept = [
            part
            for part in parts
            if scene_request_clauses(part) != (withdrawal.clause,)
            and negative_meal_request_clauses(part) != (withdrawal.clause,)
            and re.sub(r"\s+", "", part) != withdrawal.literal
        ]
        if kept == parts:
            result.append(entry)
        elif remainder := "，".join(part.strip() for part in kept if part.strip()):
            result.append(remainder)
    return result


def apply_scene_withdrawals(
    state: SessionState, intent: Intent, message: str
) -> tuple[Intent, str | None]:
    """Resolve all owners before mutating; unqualified means meal, not everyone.

    This is session-local. The profile and other diners are never edited. A
    negative scene/meal reference remains a separate bounded exclusion request.
    """
    if intent.action not in {"plan", "replace", "reject"}:
        return intent, None
    if re.fullmatch(
        r"(?:继续|按刚才的要求继续|继续按刚才的要求|就这样|再试一次|重试)[。.!！]?", message.strip()
    ):
        # Empty continuation is not authority to restore an explicitly removed
        # scene from a profile/history echo. Fresh requests remain separately
        # parsed, so this does not prohibit later opting into that scene again.
        preferences = list(intent.preferences)
        updates = [update.model_copy(deep=True) for update in intent.diner_updates]
        for event in state.scene_retractions:
            if event.get("status") != "removed":
                continue
            withdrawal = SceneWithdrawal(event["owner"], event["clause"], event["literal"])
            preferences = _without(preferences, withdrawal)
            for update in updates:
                try:
                    diner = find_diner(state.diners, update)
                except DinerConflict:
                    continue  # Existing diner validation still reports this.
                if diner is not None and diner.display_name == event["owner"]:
                    update.preferences = _without(update.preferences, withdrawal)
        return (
            intent.model_copy(update={"preferences": preferences, "diner_updates": updates}),
            None,
        )
    withdrawals = scene_withdrawals(message)
    if not withdrawals:
        return intent, None
    if any(
        withdrawal.clause in (*scene_request_clauses(part), *negative_meal_request_clauses(part))
        for withdrawal in withdrawals
        for part in _SPLIT.split(message)
    ):
        return intent, "同轮既取消又要求同一场景，请确认本餐要保留还是撤销；未解除任何要求。"
    try:
        update_owners = [find_diner(state.diners, update) for update in intent.diner_updates]
    except DinerConflict as error:
        return intent, str(error)
    targets: list[tuple[SceneWithdrawal, list[str], str]] = []
    for withdrawal in withdrawals:
        if withdrawal.owner == "本餐":
            if state.meal_constraints is None:
                return intent, "缺少可核对的本餐偏好来源，未撤销；请明确来源。"
            target, owner = state.meal_constraints.preferences, "本餐"
            if _without(target, withdrawal) == target and any(
                _without(d.preferences, withdrawal) != d.preferences for d in state.diners
            ):
                return (
                    intent,
                    "该场景来自用餐者档案而非本餐；请明确取消谁的哪项场景偏好，未解除任何要求。",
                )
        else:
            try:
                diner = find_diner(state.diners, DinerUpdate(diner=withdrawal.owner))
            except DinerConflict as error:
                return intent, str(error)
            if diner is None:
                return intent, "无法确定撤销场景偏好的用餐者；请提供已有明确称呼，未解除任何要求。"
            target, owner = diner.preferences, diner.display_name
        targets.append((withdrawal, target, owner))
    updates = [update.model_copy(deep=True) for update in intent.diner_updates]
    preferences = list(intent.preferences)
    for withdrawal, target, owner in targets:
        before = list(target)
        target[:] = _without(target, withdrawal)
        # An extractor may echo the canceled preference as a fresh positive.
        # Do not let that output restore it in this same amendment turn.
        preferences = _without(preferences, withdrawal)
        for update, diner in zip(updates, update_owners, strict=True):
            if withdrawal.owner != "本餐" and diner is not None and diner.display_name == owner:
                update.preferences = _without(update.preferences, withdrawal)
        event = {
            "owner": owner,
            "clause": withdrawal.clause,
            "literal": withdrawal.literal,
            "before": "；".join(before),
            "after": "；".join(target),
            "status": "removed" if target != before else "not_present",
        }
        state.last_scene_retractions.append(event)
        state.scene_retractions = (state.scene_retractions + [event])[-64:]
    state.constraints = aggregate_constraints(
        state.meal_constraints or state.constraints, state.diners
    )
    return intent.model_copy(update={"preferences": preferences, "diner_updates": updates}), None


def scene_retraction_copy(state: SessionState) -> str:
    lines = []
    for event in state.last_scene_retractions:
        verb = "已撤销" if event["status"] == "removed" else "未找到需撤销的"
        lines.append(f"{verb}{event['owner']}场景偏好：{event['clause']}。")
    if lines:
        lines.append(
            "仅调整本会话指定来源，不修改原档案；其他人的要求、过敏、不辣和换菜范围保留。撤销偏好不等于禁止该类菜，也不保证菜单必须改变。"
        )
    return "\n".join(lines)
