"""Literal source-clause retraction bound to the original meal and edit scope."""

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Literal

from app.domain.matching_tags import (
    flavor_conflicts,
    flavor_overlaps,
    flavor_request_parts,
    supported_flavor_exclusions,
)
from app.domain.models import (
    FlavorResolution,
    FlavorRetraction,
    FlavorRetractionOption,
    Recipe,
    SessionState,
)

Action = Literal["plan", "replace", "reject", "explain"]
PAUSE = "暂不规划"


def _remaining(entry: str, start: int, end: int) -> str:
    remainder = (entry[:start] + entry[end:]).strip(" 、,，;；。.!！?？\r\n")
    if not flavor_request_parts(remainder) and not re.sub(
        r"不过|但是|可是|然而|而是|改为|改成|换成|但|[\s、,，;；。.!！?？]", "", remainder
    ):
        return ""
    return remainder


def binding(
    state: SessionState, catalog: Sequence[Recipe], action: Action, slot: int | None
) -> str:
    value = {
        "version": "source-clause-flavor-retraction-v1",
        "user_id": state.user_id,
        "session_id": state.session_id,
        "meal_constraints": (
            state.meal_constraints.model_dump(mode="json") if state.meal_constraints else None
        ),
        "constraints": state.constraints.model_dump(mode="json"),
        "diners": [d.model_dump(mode="json") for d in state.diners],
        "menu_ids": state.menu_ids,
        "rejected_ids": state.rejected_recipe_ids,
        "pending_allergy": state.pending_allergy,
        "pending_allergy_terms": state.pending_allergy_terms,
        "pending_diet_mode": state.pending_diet_mode,
        "pending_dish_composition": state.pending_dish_composition,
        "action": action,
        "replace_slot": slot,
        "catalog": [r.model_dump(mode="json") for r in sorted(catalog, key=lambda r: r.recipe_id)],
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def make_pending(
    state: SessionState, catalog: Sequence[Recipe], action: Action, slot: int | None
) -> FlavorResolution:
    conflicts = flavor_conflicts(state.constraints.preferences)
    exclusions = supported_flavor_exclusions(state.constraints.preferences)
    sources: list[tuple[str | None, str, list[str]]] = [
        (
            None,
            "本餐",
            (
                state.meal_constraints.preferences
                if state.meal_constraints
                else state.constraints.preferences
            ),
        )
    ]
    sources.extend(
        (d.diner_id, f"第{index}位用餐者（{d.display_name}）", d.preferences)
        for index, d in enumerate(state.diners, 1)
        if d.attendance
    )
    options = []
    for diner_id, owner, preferences in sources:
        for index, entry in enumerate(preferences):
            for part in flavor_request_parts(entry):
                if not part.pure_flavor_clause or any(
                    flavor_overlaps(n, "辣") for n in part.negative
                ):
                    continue  # Never retract a non-spicy exclusion via taste choices.
                relevant = any(p in conflicts for p in part.positive) or any(
                    flavor_overlaps(p, n) for p in conflicts for n in part.negative
                )
                if not relevant:
                    continue
                # A positive sub-reference can participate in a compound
                # conflict; the option explicitly names the entire removed clause.
                if part.positive and not any(
                    flavor_overlaps(p, n) for p in part.positive for n in exclusions
                ):
                    continue
                remainder = _remaining(entry, part.start, part.end)
                options.append(
                    FlavorRetractionOption(
                        display=f"撤回{owner}第{index + 1}条口味第{part.index + 1}段：{part.text}",
                        diner_id=diner_id,
                        preference_index=index,
                        before=entry,
                        after=remainder,
                        removed_clause=part.text,
                    )
                )
    prompt = "已记录的正负口味偏好存在冲突：" + "、".join(conflicts) + "。"
    if options:
        prompt += "请明确回复一个显示的撤回选项，每次只撤回该来源的这段口味原文；其余子句、其他人的要求及安全限制保留。"
    else:
        prompt += "当前冲突含安全或无法独立划分的混合原文，没有可自动撤回的纯口味子句；请重新明确归属与要求，不自动解除。"
    prompt += "本会话内调整，不修改原档案；普通继续不代选，也不扩大原换菜范围。可以回复暂不规划。"
    return FlavorResolution(
        binding_hash=binding(state, catalog, action, slot),
        action=action,
        replace_slot=slot,
        original_menu_ids=list(state.menu_ids),
        conflicts=list(conflicts),
        options=options,
        prompt=prompt,
    )


def choice(message: str, pending: FlavorResolution | None) -> FlavorRetractionOption | None:
    if pending is None:
        return None
    text = message.strip().rstrip("。.!！")
    return next((option for option in pending.options if option.display == text), None)


def retract(
    state: SessionState, pending: FlavorResolution, option: FlavorRetractionOption, answer: str
) -> FlavorRetraction:
    if option not in pending.options:
        raise ValueError("Retraction must come from the bound options")
    if option.diner_id is None:
        if state.meal_constraints is None:
            raise ValueError("Meal source missing")
        target = state.meal_constraints.preferences
        owner = "本餐"
    else:
        diner = next((d for d in state.diners if d.diner_id == option.diner_id), None)
        if diner is None or not diner.attendance:
            raise ValueError("Diner source missing or absent")
        target = diner.preferences
        owner = diner.display_name + "（" + diner.diner_id + "）"
    if option.preference_index >= len(target) or target[option.preference_index] != option.before:
        raise ValueError("Preference source changed")
    # Recompute permissible options instead of trusting persisted replacement
    # text; session/catalog binding is independently checked by the caller.
    permitted = []
    for part in flavor_request_parts(option.before):
        if (
            part.text == option.removed_clause
            and part.pure_flavor_clause
            and not any(flavor_overlaps(n, "辣") for n in part.negative)
            and _remaining(option.before, part.start, part.end) == option.after
        ):
            permitted.append(part)
    if not permitted:
        raise ValueError("Not a retractable pure-flavor clause")
    if option.after:
        target[option.preference_index] = option.after
    else:
        target.pop(option.preference_index)
    event = FlavorRetraction(
        owner=owner,
        before=option.before,
        after=option.after,
        removed_clause=option.removed_clause,
        answer=answer,
        action=pending.action,
        replace_slot=pending.replace_slot,
    )
    state.last_flavor_retraction = event
    state.flavor_retractions.append(event)
    state.pending_flavor_resolution = None
    return event


def retraction_copy(event: FlavorRetraction) -> str:
    return (
        f"已按你的明确选择，仅在本会话撤回{event.owner}的口味子句「{event.removed_clause}」。"
        + (f"同条原文的其余内容保留为「{event.after}」。" if event.after else "该条已无其他内容。")
        + "其他来源、过敏、不辣等安全限制及原换菜范围不解除；原档案未修改。"
    )
