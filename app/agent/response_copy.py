"""Render concise user-facing explanations from verified planning facts."""

from collections.abc import Sequence

from app.agent.menu_balance import MenuBalance, balance_summary
from app.domain.models import ClarificationQuestion, Constraints, Diner, Intent, Recipe


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
    active = [diner for diner in diners if diner.attendance]
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
    return facts


def required_fact_ids(intent: Intent, facts: dict[str, str]) -> list[str]:
    """Return the minimum facts that must survive optional model selection."""
    required = ["opening", "constraints"]
    if intent.action != "replace":
        required.append("balance")
    if "diners" in facts:
        required.append("diners")
    return required
