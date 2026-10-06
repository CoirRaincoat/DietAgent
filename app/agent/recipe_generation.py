"""Bounded local recipe synthesis, not an LLM or an extension of the source KB.

The original steam proposal remains exact. An explicit pan-frying request may
compose tofu and declared cooking oil instead; neither is general AI creation.
This does not remove ingredients from a supplied recipe or invent measurements,
servings, appliance programs, nutrition or treatment claims. Unknown requests
remain gaps; a source-compatible dish always takes precedence. A separately
versioned no-spicy liangfen draft only fills that explicit missing meal dish.
"""

import hashlib
import json
from typing import Literal

from app.domain.generated_recipe import verified_proposal
from app.domain.matching_tags import (
    explicit_non_spicy_flavor_preference,
    flavor_overlaps,
    supported_flavor_exclusions,
)
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Ingredient, Recipe, SessionState
from app.domain.protein_food_references import named_protein_foods
from app.domain.recipe_origin import GENERATOR_FLAG, LIANGFEN_FLAG, is_generated_recipe
from app.domain.scoped_methods import scoped_method_mask
from app.rules.engine import RuleEngine

GENERATOR_VERSION = "local-tofu-main-v1"
PAN_GENERATOR_VERSION = "local-pan-fried-tofu-v1"
PAN_GENERATOR_FLAG = "generated_local_pan_fried_tofu_v1"
GENERATION_DISCLOSURE = (
    "库内未找到符合本餐已知限制的豆腐主体菜，补入本地新生成方案：清蒸豆腐。"
    "这不是原2000菜谱，也不是删除原方肉蛋后的版本；配料仅老豆腐和水，做法为蒸。"
    "方案待试做，豆腐品牌完整成分及交叉接触需另核；未设用量、份数、设备参数或营养量，"
    "不承诺低钠达标、降压或护心功效。"
)
LIANGFEN_DISCLOSURE = (
    "库内未找到符合本餐已知限制的正餐凉粉，补入本地新生成方案：黄瓜清拌凉粉。"
    "这不是原2000菜谱，也不是把川北凉粉或伤心凉粉删辣后冒充原方。"
    "配料为豌豆淀粉、水、黄瓜、米醋和盐，未加入辣料或未声明料汁；"
    "淀粉与水的配比按所用产品的凉粉制作说明核对，需提前煮制、冷却凝固。"
    "方案待试做，品牌完整配方及交叉接触另核；未核用量、人数份量、"
    "总备餐时间或保存条件，不承诺实际辣感、低钠达标或护心降压功效。"
)


def _liangfen_proposal() -> Recipe:
    """Independent declared-ingredient draft, not an edited source recipe.

    Ratios vary with starch products. Defer to the actual product's liangfen
    instructions rather than inventing quantities, timings or servings.
    """
    content = {
        "version": "local-non-spicy-liangfen-v1",
        "name": "黄瓜清拌凉粉（新生成）",
        "raw_ingredients": "主料：豌豆淀粉（制成凉粉）；水；黄瓜；辅料：米醋；盐",
        "steps": (
            "按所用豌豆淀粉包装的凉粉制作说明量取淀粉与水，先用部分水搅成无颗粒粉浆。\n"
            "锅中加余下的水，加热后倒入粉浆，持续搅拌煮至糊化、均匀半透明；倒入洁净耐热容器。\n"
            "冷却至完全凝固后将凉粉切条；黄瓜洗净切丝。\n"
            "将凉粉与黄瓜摆盘，以米醋和盐调味拌匀；只使用已声明的配料。"
        ),
    }
    fingerprint = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return Recipe(
        recipe_id="generated_" + fingerprint[:24],
        name=content["name"], raw_ingredients=content["raw_ingredients"],
        steps=content["steps"],
        ingredients=[Ingredient(raw=name, name=name)
                     for name in ("豌豆淀粉", "水", "黄瓜", "米醋", "盐")],
        labels=[], categories=["staple"], methods=["煮", "拌"],
        meal_types=["午餐", "晚餐"], source_row=0, fingerprint=fingerprint,
        quality_flags=[LIANGFEN_FLAG, "proposal_not_kitchen_validated"],
    )


def propose_missing_liangfen(
    constraints: Constraints, source_candidates: list[Recipe], menu: list[Recipe],
    rejected_ids: set[str], rules: RuleEngine,
) -> Recipe | None:
    """Only fill an explicit unfulfilled no-spicy liangfen request.

    Call with the full original catalog. A rejected or lower-ranked safe source
    dish does not justify synthesis. Slot permissions are owned by the caller.
    """
    if (
        "凉粉" not in {value.strip() for value in constraints.preferred_ingredients}
        or not (constraints.no_spicy or explicit_non_spicy_flavor_preference(constraints.preferences))
        or constraints.meal_type not in {"午餐", "晚餐"}
        or constraints.max_minutes is not None
        # The fixed draft uses vinegar. Do not invent a vinegar-free variant
        # or use cached flavor labels to certify an actual taste.
        or any(flavor_overlaps(value, "酸")
               for value in supported_flavor_exclusions(constraints.preferences))
        or any(rules.preference_matches(recipe, "凉粉") for recipe in menu)
        or any(is_main_meal_recipe(recipe)
               and rules.preference_matches(recipe, "凉粉")
               and rules.evaluate(recipe, constraints).allowed
               for recipe in source_candidates)
    ):
        return None
    proposal = _liangfen_proposal()
    if proposal.recipe_id in rejected_ids or not rules.evaluate(proposal, constraints).allowed:
        return None
    return proposal


def _tofu_proposal(method: Literal["蒸", "煎"] = "蒸") -> Recipe:
    content = {
        "version": GENERATOR_VERSION,
        "name": "清蒸豆腐（新生成）",
        "raw_ingredients": "主料：老豆腐；辅料：水",
        "steps": (
            "将老豆腐切块，放入耐热盘。\n"
            "蒸锅加水，水沸后放入盛豆腐的盘，盖盖蒸至豆腐中心热透。\n"
            "取出时防烫，沥去盘内积水后装盘；不添加未声明的配料。"
        ),
    }
    if method == "煎":
        content = {
            "version": PAN_GENERATOR_VERSION,
            "name": "香煎豆腐（新生成）",
            "raw_ingredients": "主料：老豆腐；辅料：食用油",
            "steps": (
                "将老豆腐切片，用厨房纸吸干表面水分。\n"
                "锅中加入食用油，放入豆腐片，煎至两面金黄且中心熟透。\n"
                "取出装盘；不加入未声明的配料，不再焖煮。"
            ),
        }
    fingerprint = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return Recipe(
        recipe_id="generated_" + fingerprint[:24],
        name=content["name"],
        raw_ingredients=content["raw_ingredients"],
        steps=content["steps"],
        ingredients=[
            Ingredient(raw=name, name=name)
            for name in (("老豆腐", "水") if method == "蒸" else ("老豆腐", "食用油"))
        ],
        labels=["原味"],
        categories=["protein"],
        methods=[method],
        meal_types=["午餐", "晚餐"],
        source_row=0,
        fingerprint=fingerprint,
        quality_flags=[GENERATOR_FLAG, "proposal_not_kitchen_validated"]
        + ([PAN_GENERATOR_FLAG] if method == "煎" else []),
    )


def resolve_generated_recipe(recipe_id: str) -> Recipe | None:
    """Reconstruct the exact versioned proposal for replay/restart, not user data."""
    for method in ("蒸", "煎"):
        proposal = _tofu_proposal(method)
        if recipe_id == proposal.recipe_id:
            return proposal
    proposal = _liangfen_proposal()
    if recipe_id == proposal.recipe_id:
        return proposal
    return None


def session_recipe_records(
    source: dict[str, Recipe], state: SessionState
) -> dict[str, Recipe]:
    """Session-local lookup; never contaminate the shared catalog or retrieval."""
    ids = set(state.menu_ids + state.rejected_recipe_ids)
    if state.pending_method_tradeoff is not None:
        pending = state.pending_method_tradeoff
        ids.update(
            pending.original_menu_ids
            + pending.meal_option_ids
            + pending.method_option_ids
        )
    records = dict(source)
    records.update(
        {
            key: recipe
            for key, recipe in state.generated_recipes.items()
            if key == recipe.recipe_id and verified_proposal(recipe)
        }
    )
    for recipe_id in ids - records.keys():
        proposal = resolve_generated_recipe(recipe_id)
        if proposal is not None:
            records[recipe_id] = proposal
    return records


def verified_generated_recipe(recipe: Recipe) -> bool:
    fixed = resolve_generated_recipe(recipe.recipe_id)
    return (fixed is not None and fixed == recipe) or verified_proposal(recipe)


def generation_disclosure(recipes: list[Recipe]) -> str:
    if any(LIANGFEN_FLAG in recipe.quality_flags for recipe in recipes):
        others = [recipe for recipe in recipes if is_generated_recipe(recipe)
                  and LIANGFEN_FLAG not in recipe.quality_flags]
        return LIANGFEN_DISCLOSURE + (generation_disclosure(others) if others else "")
    if not any(verified_proposal(recipe) for recipe in recipes):
        if any(PAN_GENERATOR_FLAG in recipe.quality_flags for recipe in recipes):
            return (
                "库内未找到符合本餐已知限制及指定煎做法的豆腐主体菜，补入本地新生成方案：香煎豆腐。"
                "这不是原2000菜谱或模型原提案，也不是删除原方肉蛋后的版本；配料仅老豆腐和食用油，完成做法为煎。"
                "方案待试做，品牌完整成分及交叉接触需另核；未设用量、份数、设备参数或营养量，"
                "不承诺少油、低钠达标、降压或护心功效。"
            )
        return GENERATION_DISCLOSURE
    return (
        "库内未找到符合本餐已知限制的豆腐主体菜，补入新生成方案。"
        "这不是原2000菜谱，也不是删除原方肉蛋后的版本；配料和步骤为生成提案，已按已知本地限制筛查。"
        "方案待试做，品牌完整成分及交叉接触需另核；未设用量、份数、设备参数或营养量，"
        "不承诺低钠达标、降压或护心功效。"
    )


def propose_missing_tofu(
    constraints: Constraints,
    source_candidates: list[Recipe],
    menu: list[Recipe],
    rejected_ids: set[str],
    rules: RuleEngine,
) -> Recipe | None:
    """No override of safety, known source options, rejection or unsupported scope."""
    if (
        "豆腐"
        not in {
            rules.canonical_food(value) for value in constraints.preferred_ingredients
        }
        or any("豆腐" in named_protein_foods(recipe) for recipe in menu)
        or constraints.meal_type not in {"午餐", "晚餐"}
        or constraints.max_minutes is not None
    ):
        return None
    # Rejection of an available source dish is not proof the library lacks one.
    tofu_methods = [
        request
        for request in constraints.scoped_methods
        if rules.canonical_food(request.food) == "豆腐" and request.required
    ]
    if any(
        "豆腐" in named_protein_foods(recipe)
        and rules.evaluate(recipe, constraints).allowed
        and all(
            scoped_method_mask(recipe, [request], slot=request.slot)
            for request in tofu_methods
        )
        for recipe in source_candidates
    ):
        return None
    proposal = _tofu_proposal(
        "煎"
        if tofu_methods and {request.method for request in tofu_methods} == {"煎"}
        else "蒸"
    )
    if (
        proposal.recipe_id in rejected_ids
        or not rules.evaluate(proposal, constraints).allowed
    ):
        return None
    return proposal
