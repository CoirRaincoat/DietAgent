"""Finite negative scene/meal metadata gates, not suitability certification."""

from collections.abc import Sequence

from app.domain.dining_scenes import scene_requests
from app.domain.matching_tags import matching_tags
from app.domain.meal_context import source_meals, supported_meal_exclusions
from app.domain.models import Constraints, Recipe

CONTEXT_EXCLUSION_VERSION = "explicit-negative-source-context-v1"


def context_conflict_issue(constraints: Constraints) -> str | None:
    requests = scene_requests(constraints.preferences)
    conflicts = [tag for tag in requests.positive if tag in requests.negative]
    if constraints.meal_type in supported_meal_exclusions(constraints.preferences):
        conflicts.append("当前餐次「" + constraints.meal_type + "」与其排除要求")
    if not conflicts:
        return None
    target = requests.negative[0] if requests.negative else constraints.meal_type
    return (
        "用餐场景／餐次要求存在冲突："
        + "、".join(conflicts)
        + "；请明确要撤销哪个来源的哪项要求，例如“取消本餐的不要"
        + target
        + "”；"
        "不擅自删除档案、本餐或其他人的要求，也不自动切换餐次。"
    )


def context_exclusion_hits(recipe: Recipe, constraints: Constraints) -> tuple[str, ...]:
    """Exact source labels/title scenes, not assistant-compatible references.

    Meal exclusions require actual raw meal label evidence: empty raw metadata
    cannot be replaced by a forged cache. Source descriptions remain fallible
    references, not clinical, actual sensory or portion facts.
    """
    requests = scene_requests(constraints.preferences)
    excluded_meals = supported_meal_exclusions(constraints.preferences)
    if not requests.negative and not excluded_meals:
        return ()
    tags = matching_tags(recipe)
    hits = [
        f"场景排除「{item.tag}」命中来源参考：{item.evidence}。"
        for item in tags.scenes
        if item.tag in requests.negative and item.origin in {"source_label", "title_reference"}
    ]
    hits.extend(
        f"餐次排除「{tag}」命中原餐次标签：{recipe.raw_label}。"
        for tag in sorted(source_meals(recipe))
        if tag in excluded_meals and recipe.raw_label.strip()
    )
    return tuple(dict.fromkeys(hits))


def context_exclusion_disclosure(menu: Sequence[Recipe], constraints: Constraints) -> list[str]:
    scenes = scene_requests(constraints.preferences).negative
    meals = supported_meal_exclusions(constraints.preferences)
    if not scenes and not meals:
        return []
    requested = [
        *("场景「" + tag + "」" for tag in scenes),
        *("餐次标签「" + tag + "」" for tag in meals),
    ]
    lines = [
        "负向场景／餐次要求已参与来源排除："
        + "、".join(requested)
        + "；仅排除原标签或明确菜名命中的记录。未命中是未知，不证明实际不属于该场景；"
        "助手的可分装等参考不作为禁用证据，不保证整餐场景排除已完成。"
    ]
    for index, recipe in enumerate(menu, start=1):
        if hits := context_exclusion_hits(recipe, constraints):
            lines.append(f"第 {index} 道“{recipe.name}”仍命中排除参考：" + "、".join(hits))
    return lines
