"""Direct non-conflicting soft-flavor withdrawal from an explicit source."""

import re
from dataclasses import dataclass

from app.agent.diners import DinerConflict, aggregate_constraints, find_diner
from app.domain.matching_tags import (
    FLAVOR_REQUEST_TERMS,
    FlavorRequestPart,
    flavor_conflicts,
    flavor_overlaps,
    flavor_request_parts,
)
from app.domain.models import DinerUpdate, Intent, SessionState

_TERMS = "|".join(re.escape(t) for t in sorted(FLAVOR_REQUEST_TERMS, key=lambda t: (-len(t), t)))
_COMMAND = re.compile(
    r"^(?:(?P<before>本餐|这餐|我的|本人|我|[^取消撤销去掉]{1,12}的))?"
    r"(?P<verb>取消|撤销|去掉)"
    r"(?:(?P<after>本餐|这餐|我的|本人|我|[^不要]{1,12}的))?"
    r"(?P<negative>不要|不喜欢|不)?(?P<flavor>" + _TERMS + r")"
    r"(?P<suffix>口味要求|口味偏好|偏好|要求|口味)?$"
)
_SPLIT = re.compile(r"[、,，;；。.!！\r\n]+")
_MENTION = re.compile(r"如果|假如|比如|例如|解释|为什么|是否|能否|可以吗|[?？\"'“”‘’「」]")


@dataclass(frozen=True)
class FlavorWithdrawal:
    owner: str
    flavor: str
    negative: bool
    literal: str


def flavor_withdrawals(message: str) -> tuple[FlavorWithdrawal, ...]:
    if _MENTION.search(message):
        return ()
    found = []
    for part in _SPLIT.split(message):
        literal = re.sub(r"\s+", "", part)
        match = _COMMAND.fullmatch(literal)
        if match is None or (match["before"] and match["after"]):
            continue
        if match["verb"] == "去掉" and not match["suffix"]:
            continue  # Bare 去掉酸 is a negative request, not preference removal.
        owner = (match["before"] or match["after"] or "本餐").removesuffix("的")
        found.append(
            FlavorWithdrawal(
                "本餐" if owner == "这餐" else owner,
                "酸甜" if match["flavor"] == "糖醋" else match["flavor"],
                bool(match["negative"]),
                literal,
            )
        )
    return tuple(dict.fromkeys(found))


def _matches(part: FlavorRequestPart, withdrawal: FlavorWithdrawal) -> bool:
    wanted = (withdrawal.flavor,)
    return (
        (part.negative == wanted and not part.positive)
        if withdrawal.negative
        else (part.positive == wanted and not part.negative)
    )


def _without(preferences: list[str], withdrawal: FlavorWithdrawal) -> list[str]:
    result = []
    for entry in preferences:
        spans = [
            part
            for part in flavor_request_parts(entry)
            if (part.pure_flavor_clause and _matches(part, withdrawal))
            or re.sub(r"\s+", "", part.text) == withdrawal.literal
        ]
        remainder = entry
        for part in reversed(spans):
            remainder = remainder[: part.start] + remainder[part.end :]
        # Preserve every unaffected source span verbatim, including contrasts.
        remainder = remainder.strip(" 、,，;；。.!！\r\n") if spans else remainder
        if remainder:
            result.append(remainder)
    return result


def apply_flavor_withdrawals(
    state: SessionState, intent: Intent, message: str
) -> tuple[Intent, str | None]:
    if intent.action not in {"plan", "replace", "reject"}:
        return intent, None
    withdrawals = flavor_withdrawals(message)
    continuation = re.fullmatch(
        r"(?:继续|按刚才的要求继续|继续按刚才的要求|就这样|再试一次|重试)[。.!！]?", message.strip()
    )
    if continuation:
        withdrawals = tuple(
            FlavorWithdrawal(
                event["owner"], event["flavor"], event["negative"] == "true", event["literal"]
            )
            for event in state.direct_flavor_retractions
            if event.get("status") == "removed"
        )
    if not withdrawals:
        return intent, None
    if not continuation and flavor_conflicts(state.constraints.preferences):
        return (
            intent,
            "已有正负口味冲突，请回复当前完整的来源撤回选项；本轮直接命令未撤销口味或安全要求。",
        )
    if any(w.negative and flavor_overlaps(w.flavor, "辣") for w in withdrawals):
        return intent, "不吃辣及包含辣味的排除不由软口味撤销解除；已保留安全要求。"
    if not continuation and any(
        _matches(p, w)
        for w in withdrawals
        for p in flavor_request_parts(message)
        if p.pure_flavor_clause
    ):
        return intent, "同轮既取消又要求同一口味，请确认保留还是撤销；未撤销本项。"
    try:
        update_owners = [find_diner(state.diners, u) for u in intent.diner_updates]
    except DinerConflict as error:
        return intent, str(error)
    targets = []
    for withdrawal in withdrawals:
        if continuation:
            continue
        if withdrawal.owner == "本餐":
            if state.meal_constraints is None:
                return intent, "本餐口味来源缺失，不能撤销；请确认来源。"
            target, owner = state.meal_constraints.preferences, "本餐"
            if _without(target, withdrawal) == target and any(
                _without(d.preferences, withdrawal) != d.preferences for d in state.diners
            ):
                return intent, "该口味来自用餐者而非本餐；请明确取消谁的哪项口味偏好，未撤销本项。"
        else:
            try:
                diner = find_diner(state.diners, DinerUpdate(diner=withdrawal.owner))
            except DinerConflict as error:
                return intent, str(error)
            if diner is None:
                return intent, "无法确认撤销口味的用餐者，请使用已有明确称呼；未撤销本项。"
            target, owner = diner.preferences, diner.display_name
        if _without(target, withdrawal) == target and any(
            withdrawal.flavor in p.positive + p.negative
            for entry in target
            for p in flavor_request_parts(entry)
        ):
            return (
                intent,
                "该口味位于混合或复合原文，不能独立撤销；请明确要调整的完整来源子句，未撤销本项。",
            )
        targets.append((withdrawal, target, owner))
    preferences = list(intent.preferences)
    updates = [u.model_copy(deep=True) for u in intent.diner_updates]
    for withdrawal in withdrawals:
        preferences = _without(preferences, withdrawal)
        for update, diner in zip(updates, update_owners, strict=True):
            target_owner = withdrawal.owner
            if diner is not None and (
                diner.display_name == target_owner
                or (diner.profile_owner and target_owner in {"我", "本人"})
            ):
                update.preferences = _without(update.preferences, withdrawal)
    for withdrawal, target, owner in targets:
        before = list(target)
        target[:] = _without(target, withdrawal)
        event = {
            "owner": owner,
            "flavor": withdrawal.flavor,
            "negative": str(withdrawal.negative).lower(),
            "literal": withdrawal.literal,
            "before": "；".join(before),
            "after": "；".join(target),
            "status": "removed" if before != target else "not_present",
            "action": intent.action,
            "replace_slot": str(intent.replace_slot or ""),
        }
        state.last_direct_flavor_retractions.append(event)
        state.direct_flavor_retractions = (state.direct_flavor_retractions + [event])[-64:]
    state.constraints = aggregate_constraints(
        state.meal_constraints or state.constraints, state.diners
    )
    return intent.model_copy(update={"preferences": preferences, "diner_updates": updates}), None


def direct_flavor_retraction_copy(state: SessionState) -> str:
    lines = []
    for event in state.last_direct_flavor_retractions:
        verb = "已撤销" if event["status"] == "removed" else "未找到需撤销的"
        clause = ("不要" if event["negative"] == "true" else "") + event["flavor"]
        lines.append(f"{verb}{event['owner']}口味偏好：{clause}。")
    if lines:
        lines.append(
            "仅撤销本会话指定来源的独立口味子句，不修改原档案；其他人的要求、过敏、不辣与原换菜范围保留。撤销正向口味回到中性，不等于禁止该口味或必须换菜。"
        )
    return "\n".join(lines)
