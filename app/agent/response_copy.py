"""Render concise user-facing explanations from verified planning facts."""

from collections.abc import Sequence

from app.agent.diet_mode import DIET_LABELS
from app.agent.diners import confirmed_attendees, is_unlinked_profile
from app.agent.meal_structure import remaining_role_gaps
from app.agent.menu_balance import MenuBalance, balance_summary
from app.agent.recipe_generation import generation_disclosure
from app.agent.scene_preferences import scene_warnings
from app.domain.advance_preparation import advance_permission_copy, advance_preparation_copy
from app.domain.cooking_methods import main_cooking_methods
from app.domain.dish_composition import composition_active, composition_counts, soup_source_counts
from app.domain.entree_preferences import entree_reference_mask, mixed_entree_warnings
from app.domain.ingredient_disclosure import ingredient_safety_copy
from app.domain.light_preparation import light_preparation_warning
from app.domain.matching_tags import (
    flavor_coverage,
    flavor_preference_issues,
    negative_flavor_menu_disclosure,
    supported_flavor_preferences,
)
from app.domain.meal_context import meal_context_warnings
from app.domain.method_preferences import method_preference_warnings
from app.domain.models import (
    ClarificationQuestion,
    Constraints,
    Diner,
    Intent,
    Recipe,
    SessionState,
)
from app.domain.recipe_origin import is_generated_recipe
from app.domain.scoped_methods import scoped_method_warnings
from app.nutrition.models import GoalMatch


def clarification_copy(questions: Sequence[ClarificationQuestion]) -> str:
    """Combine required follow-up questions into one natural prompt."""
    fields = {question.field for question in questions}
    if fields == {"people", "meal_type", "restrictions"}:
        return (
            "为了把这餐安排准确，还需要确认：这餐几个人吃、安排哪一餐，"
            "以及有没有过敏食材或忌口（没有也请说明）。"
        )
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
        if (before.recipe_id if before else None) != (after.recipe_id if after else None):
            changes.append((index + 1, before, after))
    if intent.action == "explain":
        return "这份菜单的搭配思路是："
    if intent.action == "reject":
        return "已按原有要求重新安排整份菜单，上一版菜品没有继续沿用。"
    if intent.action == "replace":
        if len(changes) == 1 and changes[0][1] is not None and changes[0][2] is not None:
            slot, before, after = changes[0]
            assert before is not None and after is not None
            return (
                f"已按你的要求，只将第 {slot} 道“{before.name}”换成“{after.name}”，"
                "其他菜保持不变。"
            )
        return "已按你的要求完成局部调整，未受影响的菜保持不变。"
    if previous:
        if not changes:
            return "已记住你刚补充的要求，当前菜单无需调整。"
        if len(changes) == 1 and changes[0][1] is not None and changes[0][2] is not None:
            slot, before, after = changes[0]
            assert before is not None and after is not None
            return (
                f"已加入你刚补充的要求，仅将第 {slot} 道“{before.name}”调整为“{after.name}”，"
                "其他菜保持不变。"
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
    health_matches: Sequence[GoalMatch] | None = None,
) -> dict[str, str]:
    """Build a small ordered fact set suitable for user-facing rendering."""
    facts = {
        "opening": _change_copy(intent, previous, chosen),
        "constraints": _constraint_copy(constraints),
        "balance": balance_summary(balance),
    }
    diner_text = _diner_copy(diners)
    if safety_notice := ingredient_safety_copy(list(chosen)):
        facts["ingredient_safety"] = safety_notice
    if diner_text:
        facts["diners"] = diner_text
    if constraints.health_goals:
        if (
            previous
            and all(a.recipe_id == b.recipe_id for a, b in zip(previous, chosen))
            and len(previous) == len(chosen)
            and intent.action == "plan"
        ):
            facts["opening"] = "已复核当前已知要求，本轮保留原菜单；这不表示健康目标已经达标。"
        statements = ["健康目标仅参与食材和做法的定性偏好复核，不是营养达标评分。"]
        matches = {match.goal: match for match in (health_matches or [])}
        for goal in dict.fromkeys(constraints.health_goals):
            match = matches.get(goal)
            if match is None:
                statements.append(
                    f"{goal}：目标已记录，但本次没有足够健康目标核对证据，不能宣称已满足。"
                )
                continue
            status = {
                "caution": "仍有需关注的源配方证据",
                "preference_match": "有定性偏好依据，未证明摄入达标",
                "insufficient_data": "匹配证据不足",
            }[match.status]
            statements.append(f"{goal}：{status}。" + "".join(match.reasons) + match.limitation)
        facts["health_goals"] = "\n".join(statements)
    flavors = supported_flavor_preferences(constraints.preferences)
    flavor_issues = flavor_preference_issues(constraints.preferences,
        excluded_ingredients=constraints.excluded_ingredients)
    if flavors or flavor_issues:
        mask = 0
        for recipe in chosen:
            mask |= flavor_coverage(recipe, flavors)
        covered = [flavor for index, flavor in enumerate(flavors) if mask & (1 << index)]
        missing_flavors = [
            flavor for index, flavor in enumerate(flavors) if not mask & (1 << index)
        ]
        statements = []
        if covered:
            statements.append("口味来源参考已覆盖：" + "、".join(covered) + "。")
        if "清淡" in flavors:
            statements.extend(
                warning for recipe in chosen if (warning := light_preparation_warning(recipe))
            )
        if missing_flavors:
            statements.append(
                "当前菜单尚未覆盖有明确来源参考的口味偏好：" + "、".join(missing_flavors) + "。"
            )
            statements.append(
                "已保留食材限制、荤素数量、餐次和本轮换菜范围；缺少参考不等于实际尝味后不符。"
            )
        if missing_flavors or flavor_issues:
            if (
                previous
                and len(previous) == len(chosen)
                and all(a.recipe_id == b.recipe_id for a, b in zip(previous, chosen))
                and intent.action == "plan"
            ):
                facts["opening"] = (
                    "已记录当前要求，本轮保留原菜单；仍有口味缺口、未知或冲突需要处理。"
                )
        statements.extend(flavor_issues)
        statements.extend(negative_flavor_menu_disclosure(chosen, constraints.preferences))
        statements.append("原始标签和菜名仅为口味参考，不是实际尝味、不辣、低钠或健康效果保证。")
        facts["flavor_preferences"] = "\n".join(statements)
    scenes = scene_warnings(chosen, constraints)
    if scenes:
        facts["dining_scene"] = "\n".join(scenes)
        if (
            previous
            and len(previous) == len(chosen)
            and all(a.recipe_id == b.recipe_id for a, b in zip(previous, chosen))
            and intent.action == "plan"
        ):
            facts["opening"] = (
                "已记录当前要求，本轮保留原菜单；用餐场景仍仅有有限来源参考或缺口，未证明整餐适配。"
            )
    methods = method_preference_warnings(chosen, constraints.preferences)
    methods.extend(scoped_method_warnings(chosen, constraints.scoped_methods))
    if constraints.method_meal_priority is not None:
        priority = "餐次参考" if constraints.method_meal_priority == "meal" else "明确做法参考"
        methods.insert(0, f"本餐按你已确认的优先项：{priority}；另一项仍可能有缺口，并未删除原要求。"
                       "该选择不是安全、份量或适配认证，过敏、不辣与换菜范围仍独立保留。")
    if methods:
        facts["method_preferences"] = "\n".join(methods)
        if (
            previous
            and len(previous) == len(chosen)
            and all(a.recipe_id == b.recipe_id for a, b in zip(previous, chosen))
            and intent.action == "plan"
        ):
            facts["opening"] = (
                "已复核明确做法要求，本轮保留原菜单；做法匹配仅为有限源动作参考，缺口与边界见下。"
            )
    entree_warnings = mixed_entree_warnings(chosen, constraints)
    if entree_warnings:
        meat_names = [r.name for r in chosen if entree_reference_mask(r) & 1]
        no_meat_names = [r.name for r in chosen if entree_reference_mask(r) & 2]
        facts["mixed_entrees"] = "\n".join(
            [
                "肉鱼菜主体来源参考：" + ("、".join(meat_names) or "未确认") + "。",
                "无肉菜来源参考（允许蛋奶）：" + ("、".join(no_meat_names) or "未确认") + "。",
                *entree_warnings,
            ]
        )
        if intent.action == "plan" and any(
            "尚缺" in text or "不一致" in text for text in entree_warnings
        ):
            facts["opening"] = (
                "已记录本餐要求；荤素来源参考仍有缺口或冲突，未宣称所有要求均已满足。"
                "已保留当前安全限制与调整范围。"
            )
    context_warnings = meal_context_warnings(chosen, constraints.meal_type)
    if context_warnings:
        facts["meal_context"] = "\n".join(context_warnings)
    if prep_notice := advance_preparation_copy(chosen):
        facts["advance_preparation"] = prep_notice
    if permission_notice := advance_permission_copy(constraints.preferences):
        facts["advance_preparation"] = "\n".join(filter(None, (
            facts.get("advance_preparation"), permission_notice,
        )))
    device_programs = [r.name for r in chosen if "开始烹饪" in r.steps and not main_cooking_methods(r)]
    if device_programs:
        facts["source_program"] = (
            "原机器程序需另核：" + "、".join(device_programs)
            + "。当前有限来源核验未确认具体成菜做法；请核对原设备说明，"
            "不按菜名补写蒸法、温度、时长或熟度保证。"
        )
    gaps = remaining_role_gaps(chosen, constraints)
    if gaps:
        labels = {"vegetable": "蔬菜类菜", "protein": "蛋白质来源菜", "staple": "主食"}
        missing = "、".join(f"{labels[role]} {count} 道" for role, count in gaps.items())
        facts["role_gaps"] = (
            "按本餐的搭配规则，当前菜单仍缺："
            + missing
            + "。已保留食材限制与本轮换菜范围；菜品结构不等于营养摄入达标。"
        )
    if composition_active(constraints):
        counts = composition_counts(chosen, constraints)
        facts["dish_composition"] = (
            f"本餐非汤、非主食成菜按源配方核对：荤菜 {counts['meat']} 道、素菜 {counts['vegetarian']} 道。"
            "这里素菜允许蛋奶；含肉末、肉汤或鱼虾来源的菜不算素菜，复合配料不明时不冒充素菜。"
            "该口径不是纯素、份量或营养摄入保证。"
        )
        if constraints.meat_dish_scope == "independent_entree":
            facts["dish_composition"] = (
                f"本餐非汤、非主食成菜按明确独立口径核对：独立荤菜 {counts['meat']} 道、素菜 {counts['vegetarian']} 道。"
                "独立荤菜需有限肉鱼主体来源参考；肉末点缀的蔬菜、肉汤、虾皮和动物油调味不能填此数量，"
                "但含动物来源的菜也不因此算素菜。素菜允许蛋奶。"
                "仅核对菜名、完整食材名与源角色，未核验比例、份量或营养摄入；不是纯素或过敏安全保证。"
            )
    if constraints.meat_soup_count is not None or constraints.vegetarian_soup_count is not None:
        soups = soup_source_counts(chosen)
        facts["dish_composition"] += (
            f"\n汤的源配方另核对：荤汤 {soups['meat']} 道、素汤 {soups['vegetarian']} 道。"
            f"来源未确认汤 {soups['other']} 道。"
            "素汤按完整无肉来源证据核对，允许蛋奶；菜名写素或清淡不代替成分证据，肉汤/虾皮/动物油不算素汤。"
            "来源不明配料不确认为素汤，未核验品牌、交叉接触或营养量。"
        )
    if constraints.diet_mode != "omnivore":
        facts["whole_meal_diet"] = (
            "本次共享菜单按整餐"
            + DIET_LABELS[constraints.diet_mode]
            + "筛选，包含汤、主食、配料和步骤中的添加物。"
            + (
                "蛋奶可用；肉源、肉汤及来源不明配料不放行。"
                if constraints.diet_mode == "ovo_lacto_vegetarian"
                else "蛋奶、蜂蜜等动物来源及来源不明配料不放行。"
            )
            + "仅核对菜谱声明文字，未验证品牌完整配方、漏列食材或交叉接触；不保证份量或营养摄入。"
        )
    if any(is_generated_recipe(recipe) for recipe in chosen):
        facts["generated_recipe"] = generation_disclosure(chosen)
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
    if "diners" in facts:
        required.append("diners")
    if "ingredient_safety" in facts:
        required.append("ingredient_safety")
    if "role_gaps" in facts:
        required.append("role_gaps")
    if "dish_composition" in facts:
        required.append("dish_composition")
    if "whole_meal_diet" in facts:
        required.append("whole_meal_diet")
    if "meal_context" in facts:
        required.append("meal_context")
    if "health_goals" in facts:
        required.append("health_goals")
    if "food_preferences" in facts:
        required.append("food_preferences")
    if "flavor_preferences" in facts:
        required.append("flavor_preferences")
    if "flavor_retraction" in facts:
        required.append("flavor_retraction")
    if "dining_scene" in facts:
        required.append("dining_scene")
    if "method_preferences" in facts:
        required.append("method_preferences")
    if "mixed_entrees" in facts:
        required.append("mixed_entrees")
    if "source_program" in facts:
        required.append("source_program")
    if "advance_preparation" in facts:
        required.append("advance_preparation")
    if "source_food_tradeoff" in facts:
        required.append("source_food_tradeoff")
    if "next_meal_tradeoff" in facts:
        required.append("next_meal_tradeoff")
    if "generated_recipe" in facts:
        required.append("generated_recipe")
    required.extend(key for key in (
        "catalog", "nutrition", "profile_scope", "restore", "constraint_restore",
    ) if key in facts)
    return required
