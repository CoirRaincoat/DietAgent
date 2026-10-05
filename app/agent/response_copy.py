"""Render concise user-facing explanations from verified planning facts."""

from collections.abc import Sequence

from app.agent.diners import confirmed_attendees, is_unlinked_profile
from app.agent.menu_balance import MenuBalance, balance_summary
from app.domain.models import (
    ClarificationQuestion,
    Constraints,
    Diner,
    Intent,
    Recipe,
    SessionState,
)


def clarification_copy(questions: Sequence[ClarificationQuestion]) -> str:
    """Combine required follow-up questions into one natural prompt."""
    fields = {question.field for question in questions}
    if fields == {"people", "meal_type", "restrictions"}:
        return "为了把这餐安排准确，还需要确认：这餐几个人吃、安排哪一餐，" "以及有没有过敏食材或忌口（没有也请说明）。"
    prompts = [question.prompt.rstrip("。？！?! ") for question in questions]
    if not prompts:
        return "请补充本餐需要调整的具体要求。"
    if len(prompts) == 1:
        return prompts[0] + "？"
    if len(prompts) == 2:
        joined = "，以及".join(prompts)
    else:
        joined = "、".join(prompts[:-1]) + "，以及" + prompts[-1]
    return "为了把这餐安排准确，还需要确认：" + joined + "。"


def _change_copy(
    intent: Intent,
    previous: Sequence[Recipe],
    chosen: Sequence[Recipe],
) -> str:
    """Describe only the menu changes relevant to the current intent."""
    changes: list[tuple[int, Recipe | None, Recipe | None]] = []
    for index in range(max(len(previous), len(chosen))):
        before = previous[index] if index < len(previous) else None
        after = chosen[index] if index < len(chosen) else None
        if (before.recipe_id if before else None) != (
            after.recipe_id if after else None
        ):
            changes.append((index + 1, before, after))
    if intent.action == "explain":
        return "这份菜单的搭配思路是："
    if intent.action == "reject":
        return "已按原有要求重新安排整份菜单，上一版菜品没有继续沿用。"
    if intent.action == "replace":
        if (
            len(changes) == 1
            and changes[0][1] is not None
            and changes[0][2] is not None
        ):
            slot, before, after = changes[0]
            assert before is not None and after is not None
            return f"已按你的要求，只将第 {slot} 道“{before.name}”换成“{after.name}”，" "其他菜保持不变。"
        return "已按你的要求完成局部调整，未受影响的菜保持不变。"
    if previous:
        if not changes:
            return "已记住你刚补充的要求，当前菜单无需调整。"
        if (
            len(changes) == 1
            and changes[0][1] is not None
            and changes[0][2] is not None
        ):
            slot, before, after = changes[0]
            assert before is not None and after is not None
            return (
                f"已加入你刚补充的要求，仅将第 {slot} 道“{before.name}”调整为“{after.name}”，" "其他菜保持不变。"
            )
        return f"已加入你刚补充的要求，本轮调整了 {len(changes)} 道菜，其余保持不变。"
    return "已根据你确认的人数、餐次和饮食要求安排好这餐。"


def _constraint_copy(constraints: Constraints) -> str:
    """Summarize user constraints without exposing rule-engine terminology."""
    requirements: list[str] = []
    if constraints.allergies:
        requirements.append("避开" + "、".join(constraints.allergies))
    if constraints.excluded_ingredients:
        requirements.append("不含" + "、".join(constraints.excluded_ingredients))
    if constraints.no_spicy:
        requirements.append("不辣")
    if constraints.inventory is not None:
        requirements.append("只使用你列出的食材")
    if requirements:
        return "菜单已按要求" + "、".join(requirements) + "。"
    return "菜单已按当前确认的要求筛选。"


def _diner_copy(diners: Sequence[Diner]) -> str | None:
    """Describe attributed diner restrictions when at least two diners attend."""
    active = confirmed_attendees(list(diners))
    if len(active) < 2:
        return None
    descriptions: list[str] = []
    for diner in active:
        restrictions: list[str] = []
        if diner.allergies:
            restrictions.append("避开" + "、".join(diner.allergies))
        if diner.excluded_ingredients:
            restrictions.append("不吃" + "、".join(diner.excluded_ingredients))
        if diner.no_spicy:
            restrictions.append("不辣")
        if restrictions:
            descriptions.append(diner.display_name + "需要" + "、".join(restrictions))
    if not descriptions:
        return None
    return "多人要求方面，" + "；".join(descriptions) + "，已同时用于整桌筛选。"


def response_facts(
    *,
    intent: Intent,
    constraints: Constraints,
    diners: Sequence[Diner],
    previous: Sequence[Recipe],
    chosen: Sequence[Recipe],
    balance: MenuBalance,
) -> dict[str, str]:
    """Build a small ordered fact set suitable for user-facing rendering."""
    facts = {
        "opening": _change_copy(intent, previous, chosen),
        "constraints": _constraint_copy(constraints),
        "balance": balance_summary(balance),
    }
    diner_text = _diner_copy(diners)
    if diner_text:
        facts["diners"] = diner_text
    if any(diner.attendance and is_unlinked_profile(diner) for diner in diners):
        facts["profile_scope"] = (
            "档案主体身份待关联；已知限制保守用于共享菜单核对，"
            "未推断其对应哪位参餐者，也未断言其缺席。"
        )
    return facts


def state_change_facts(intent: Intent, before: SessionState, after: SessionState) -> dict[str, str]:
    """Describe completed operations from state evidence, never model intent alone."""
    if not after.menu_valid:
        return {}
    facts: dict[str, str] = {}
    if intent.restore_menu is not None and after.menu_history:
        original = after.menu_history[0]
        if after.menu_ids == original.recipe_ids:
            active_before = {action.action_id for action in before.rejection_actions if action.active}
            undone = any(
                action.action_id in active_before and not action.active
                and action.source_menu_revision_id == original.revision_id
                for action in after.rejection_actions
            )
            undo_copy = "并仅撤销与之对应的拒绝记录；" if undone else "；"
            facts["restore"] = (
                "已按你的要求恢复此前那桌菜单" + undo_copy
                + "后来新增的过敏、忌口与要求仍然保留。"
            )
    if intent.restore_constraints:
        facts["constraint_restore"] = (
            f"已将总菜数恢复为 {after.constraints.dish_count} 道；"
            "后来新增的过敏、忌口与要求仍然保留。"
        )
    pending = before.pending_revoke_exclusion
    if pending is not None and after.pending_revoke_exclusion is None:
        if intent.revoke_cancelled:
            facts["operation"] = "已取消本次撤销请求，原有忌口继续保留。"
        elif intent.revoke_confirmed:
            old_meal = before.meal_constraints or before.constraints
            new_meal = after.meal_constraints or after.constraints
            removed = [v for v in old_meal.excluded_ingredients if v not in new_meal.excluded_ingredients]
            old_diners = {d.diner_id: d for d in before.diners}
            for diner in after.diners:
                old = old_diners.get(diner.diner_id)
                if old is not None:
                    removed.extend(v for v in old.excluded_ingredients if v not in diner.excluded_ingredients)
            if removed:
                label = pending.subject + "的" if pending.subject else ""
                facts["operation"] = (
                    "已确认取消" + label + "普通忌口「" + "、".join(dict.fromkeys(removed))
                    + "」；其他忌口与过敏仍然保留。"
                )
            else:
                facts["operation"] = "本轮未删除任何普通忌口，现有忌口与过敏仍然保留。"
    return facts


def required_fact_ids(intent: Intent, facts: dict[str, str]) -> list[str]:
    """Return the minimum facts that must survive optional model selection."""
    required = ["opening", "constraints"]
    if intent.action != "replace":
        required.append("balance")
    required.extend(key for key in (
        "catalog", "nutrition", "profile_scope", "diners", "restore", "constraint_restore",
    ) if key in facts)
    return required
