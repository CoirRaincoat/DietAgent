"""Deterministic, source-aware screening; health goals never waive hard rules."""

import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.domain.context_exclusions import context_exclusion_hits
from app.domain.health_evidence import HealthEvidence, HealthRule, health_evidence
from app.domain.ingredient_disclosure import ingredient_disclosures
from app.domain.light_preparation import light_preparation_evidence, light_preparation_warning
from app.domain.matching_tags import (
    explicit_non_spicy_flavor_preference,
    flavor_exclusion_hits,
    supported_flavor_preferences,
)
from app.domain.meal_context import infant_only_source
from app.domain.meal_roles import is_dessert_recipe, is_main_meal_recipe, is_non_meal_role_exclusion
from app.domain.menu_group_preferences import is_menu_group_preference, menu_group_matches
from app.domain.models import Constraints, Recipe
from app.domain.source_preparation import grain_completion_issue
from app.domain.source_soups import undrained_pot_reference
from app.rules.diet import diet_reasons
from app.rules.non_spicy import non_spicy_reasons
from app.rules.sauce_composition import unresolved_sauce_evidence

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "configs" / "rules.yaml"


@dataclass
class RuleDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0
    warnings: list[str] = field(default_factory=list)


@lru_cache(maxsize=8192)
def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold().strip("，,。；;:：()（）[]【】")


def contains_term(text: str, term: str) -> bool:
    """Chinese terms have no word boundaries; Latin names do."""
    text, term = compact(text), compact(term)
    if not term:
        return False
    if re.fullmatch(r"[a-z -]+", term):
        return re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", text) is not None
    return term in text


class RuleEngine:
    def __init__(self, config_path: Path | None = None, *, experiment_category_scope: bool = False):
        path = Path(config_path) if config_path is not None else DEFAULT_RULES_PATH
        self.config: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        # Explicit source-ranking ablation only; API/service never opt in.
        self.experiment_category_scope = experiment_category_scope
        self.aliases: dict[str, list[str]] = self.config["aliases"]
        # Culinary membership is directional: chicken wings belong to chicken,
        # but are not interchangeable with chicken breast in strict inventory.
        # Optional for compatibility with existing rule configurations.
        self.food_families: dict[str, dict[str, Any]] = self.config.get("food_families", {})
        self._canonical = {
            compact(alias): canonical
            for canonical, aliases in self.aliases.items()
            for alias in [canonical, *aliases]
        }
        self._known_foods = set(self.config["known_foods"])
        self._known_foods.update(self._canonical)
        for value in self.config["allergens"].values():
            self._known_foods.update(value["terms"])
        for value in self.config["health_goals"].values():
            self._known_foods.update(value.get("discourage_terms", []))
            self._known_foods.update(value.get("attention_terms", []))
            self._known_foods.update(value.get("prefer_terms", []))
        self._known_foods.update(self.config["spicy_terms"])
        self._known_food_names = {self.canonical_food(value) for value in self._known_foods}

    def canonical_food(self, value: str) -> str:
        cleaned = compact(value)
        return self._canonical.get(cleaned, cleaned)

    def aliases_for(self, value: str) -> list[str]:
        canonical = self.canonical_food(value)
        return [canonical, *self.aliases.get(canonical, [])]

    @lru_cache(maxsize=256)
    def _allergen_keys(self, value: str) -> list[str]:
        value = re.sub(r"(过敏原|过敏食材|过敏|不耐受)$", "", compact(value))
        for name, groups in self.config["allergen_groups"].items():
            if compact(name) == value:
                return list(groups)
        for key, rule in self.config["allergens"].items():
            if value == key or value in [compact(s) for s in rule["inputs"]]:
                return [key]
        if value in [compact(s) for s in self.config["specific_allergens"]]:
            return [f"specific:{value}"]
        canonical = self.canonical_food(value)
        if canonical in [compact(s) for s in self.config["specific_allergens"]]:
            return [f"specific:{canonical}"]
        return []

    def unresolved_allergies(self, constraints: Constraints) -> list[str]:
        return [a for a in constraints.allergies if not self._allergen_keys(a)]

    def unresolved_exclusions(self, constraints: Constraints) -> list[str]:
        """No substring hit is not proof of safety for an unsupported food name."""
        return [
            value for value in constraints.excluded_ingredients
            if not is_non_meal_role_exclusion(value)
            and not self._allergen_keys(value)
            and self.canonical_food(value) not in self._known_food_names
        ]

    def _food_text(self, recipe: Recipe) -> str:
        # Titles and promotional labels are deliberately absent. Step-only
        # ingredients and optional ingredients must still undergo screening.
        return "\n".join(
            [recipe.raw_ingredients, *(i.name for i in recipe.ingredients), recipe.steps]
        )

    def goal_evidence(self, recipe: Recipe, goal: str) -> HealthEvidence | None:
        """Return qualitative source evidence, or None for an unconfigured goal."""
        rule = self.config["health_goals"].get(goal)
        if rule is None:
            return None
        configured = HealthRule.from_mapping(rule)
        if self.experiment_category_scope:
            configured = replace(configured, scope_category_to_role=True)
        # Ordinary food-reference ranking and the stricter, source-authorized
        # meat replacement are different decisions. The role gate excludes an
        # incidental binder/egg-product token here; the existing caution terms
        # remain independently visible. Do not erase a declared tofu body just
        # because its recipe also contains sugar, or certify the whole dish.
        return health_evidence(recipe, configured)

    def health_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        """Keep separate goal preferences; unknown goals never earn points."""
        return tuple(
            evidence.score if (evidence := self.goal_evidence(recipe, goal)) is not None else 0
            for goal in dict.fromkeys(constraints.health_goals)
        )

    def soft_goal_scores(self, recipe: Recipe, constraints: Constraints) -> tuple[int, ...]:
        """Separate configured health goals and supported preparation preferences."""
        scores = self.health_scores(recipe, constraints)
        if "清淡" in supported_flavor_preferences(constraints.preferences):
            scores += (int(light_preparation_evidence(recipe).method_reference),)
        return scores

    def _allergen_matches(self, text: str, key: str) -> list[str]:
        if key.startswith("specific:"):
            food = key.split(":", 1)[1]
            family = self.food_families.get(food)
            if family is not None:
                return self._family_matches(text, family)
            terms = self.aliases_for(food)
        else:
            rule = self.config["allergens"][key]
            terms = rule["terms"]
            for ignored in rule.get("ignore_phrases", []):
                text = text.replace(ignored, "")
        return [term for term in terms if contains_term(text, term)]

    @staticmethod
    def _family_matches(text: str, family: dict[str, Any]) -> list[str]:
        for ignored in family.get("ignore_phrases", []):
            text = text.replace(ignored, " ")
        matches = [term for term in family.get("members", []) if contains_term(text, term)]
        # Each parsed ingredient occupies its own line in _food_text. This lets
        # the ingredient 鸡 match without interpreting 鸡精 or 鸡腿菇 as chicken.
        names = {compact(line) for line in text.splitlines()}
        matches.extend(term for term in family.get("exact_names", []) if compact(term) in names)
        return matches

    def _uncertain_family_matches(self, text: str, value: str) -> list[str]:
        matches: set[str] = set()
        for key in self._allergen_keys(value):
            if not key.startswith("specific:"):
                continue
            family = self.food_families.get(key.split(":", 1)[1], {})
            remaining = text
            for ignored in family.get("uncertainty_ignore_phrases", []):
                remaining = remaining.replace(ignored, " ")
            matches.update(term for term in family.get("uncertain_members", [])
                           if contains_term(remaining, term))
        return sorted(matches)

    def food_matches(self, recipe: Recipe, value: str) -> list[str]:
        """Match actual food text using aliases or a recognized food group."""
        text = self._food_text(recipe)
        groups = self._allergen_keys(value)
        if groups:
            return sorted({term for key in groups for term in self._allergen_matches(text, key)})
        return [term for term in self.aliases_for(value) if contains_term(text, term)]

    def preference_matches(self, recipe: Recipe, value: str) -> list[str]:
        """Source-backed meal groups only on the positive preference axis.

        food_matches remains the separate safety/inventory resolver. A group
        match must not turn an unknown allergy or exclusion into permission.
        """
        if is_menu_group_preference(value):
            return menu_group_matches(recipe, value)
        return self.food_matches(recipe, value)

    def _step_ingredients(self, text: str) -> set[str]:
        # Longest-first masking avoids treating 鸡精 as 鸡 or 芝麻油 as 油.
        remaining = text.replace("鱼香", "").replace("鱼眼泡", "")
        found: set[str] = set()
        for term in sorted(self._known_foods, key=lambda item: (-len(item), item)):
            if contains_term(remaining, term):
                found.add(self.canonical_food(term))
                remaining = remaining.replace(term, " ")
        return found

    def inventory_missing(self, recipe: Recipe, inventory: list[str]) -> list[str]:
        available = {self.canonical_food(item) for item in inventory}
        declared = {self.canonical_food(item.name) for item in recipe.ingredients}
        step_foods = self._step_ingredients(recipe.steps)
        # Step terms that name part of an explicitly supplied ingredient (e.g.
        # 鸡胸肉 -> 鸡肉) need not become duplicate inventory requirements.
        needed = declared | step_foods
        return sorted(needed - available)

    def evaluate(self, recipe: Recipe, constraints: Constraints) -> RuleDecision:
        reasons: list[str] = []
        warnings: list[str] = []
        food_exclusions = [
            value for value in constraints.excluded_ingredients
            if not is_non_meal_role_exclusion(value)
        ]
        role_exclusions = [
            value for value in constraints.excluded_ingredients
            if is_non_meal_role_exclusion(value)
        ]
        if role_exclusions and not is_main_meal_recipe(recipe) and (
            not is_dessert_recipe(recipe)
            or any(value.strip() in {"甜品", "甜点"} for value in role_exclusions)
        ):
            reasons.append(
                "菜品类别排除「" + "、".join(role_exclusions)
                + "」需可核验正餐角色；该记录为非正餐或正餐角色未确认。"
            )
        unknown_sauces = unresolved_sauce_evidence(recipe, self._known_foods)
        if unknown_sauces:
            source = "；".join(unknown_sauces)
            non_spicy = constraints.no_spicy or explicit_non_spicy_flavor_preference(
                constraints.preferences
            )
            if (
                non_spicy
                or constraints.allergies
                or food_exclusions
                or constraints.diet_mode in {"ovo_lacto_vegetarian", "vegan"}
            ):
                reasons.append("未能完整核验源酱汁／蘸料成分，无法确认符合已声明饮食限制：" + source)
            else:
                warnings.append("源酱汁／蘸料成分尚未完整核验，不保证配方或饮食适配：" + source)
        if "清淡" in supported_flavor_preferences(constraints.preferences):
            if warning := light_preparation_warning(recipe):
                warnings.append(warning)
        reasons.extend(flavor_exclusion_hits(recipe, constraints.preferences))
        reasons.extend(context_exclusion_hits(recipe, constraints))
        if infant_only_source(recipe):
            reasons.append("源菜谱明确面向婴儿/宝宝专用准备，不用于普通共享正餐；未提供婴幼儿专餐规划能力。")
        if issue := grain_completion_issue(recipe.name, (i.name for i in recipe.ingredients), recipe.steps):
            reasons.append(issue)
        if undrained_pot_reference(recipe.name, (i.name for i in recipe.ingredients), recipe.steps):
            pot_issue = "原方煲类含水或汤底并经煮炖，未见最终沥汤或收汁依据，成品汤汁形态待核。"
            if constraints.soup_count == 0:
                reasons.append(pot_issue + "当前明确不要汤，不能用非汤类别标签证明满足。")
            else:
                warnings.append(pot_issue + "未将其自动改判为汤或推算汤数。")
        if not recipe.eligible or not recipe.steps.strip() or not recipe.ingredients:
            reasons.append("菜谱缺少可用食材或步骤，或属于不可直接推荐的加工组件。")
        unresolved = self.unresolved_allergies(constraints)
        if unresolved:
            reasons.append("过敏原缺少已支持映射，需要澄清：" + "、".join(unresolved))
        unresolved_exclusions = self.unresolved_exclusions(constraints)
        if unresolved_exclusions:
            reasons.append("排除食材缺少已支持映射，需要澄清：" + "、".join(unresolved_exclusions))
        if constraints.allergies and "unparsed_ingredients" in recipe.quality_flags:
            reasons.append("食材解析不完整，无法核对过敏限制。")
        reasons.extend(diet_reasons(recipe, constraints))
        if constraints.diet_mode != "omnivore":
            warnings.append("整餐素食按已声明原料和步骤核对；未验证品牌完整配方、漏列食材或交叉接触。")

        text = self._food_text(recipe)
        warnings.extend(ingredient_disclosures(recipe))
        for allergy in constraints.allergies:
            matched = sorted({term for key in self._allergen_keys(allergy)
                              for term in self._allergen_matches(text, key)})
            if matched:
                reasons.append(f"过敏限制「{allergy}」命中配料或步骤：{'、'.join(matched)}。")
            uncertain = self._uncertain_family_matches(text, allergy)
            if uncertain:
                reasons.append(
                    f"复合配料来源不明，无法核对过敏限制「{allergy}」：{'、'.join(uncertain)}。"
                )
        if constraints.allergies or food_exclusions:
            uncertain = [term for term in self.config["uncertain_composites"]
                         if contains_term(text, term)]
            if uncertain:
                restriction = "过敏限制" if constraints.allergies else "排除食材限制"
                reasons.append(f"复合配料成分不明确，无法确认{restriction}：" + "、".join(uncertain))
        if constraints.allergies:
            warnings.append("仅核对菜谱文字中的已知过敏原；未验证品牌配方或烹饪交叉接触。")
        for excluded in food_exclusions:
            matches = self.food_matches(recipe, excluded)
            if matches:
                reasons.append(f"明确排除「{excluded}」命中配料或步骤：{'、'.join(matches)}。")
            uncertain = self._uncertain_family_matches(text, excluded)
            if uncertain:
                reasons.append(
                    f"复合配料来源不明，无法核对排除食材「{excluded}」：{'、'.join(uncertain)}。"
                )
        if constraints.no_spicy or explicit_non_spicy_flavor_preference(constraints.preferences):
            reasons.extend(non_spicy_reasons(
                text, recipe.labels,
                ingredient_terms=self.config["spicy_terms"],
                spicy_labels=self.config.get("spicy_labels", ["辣", "微辣", "香辣", "酸辣"]),
                uncertain_terms=self.config["uncertain_composites"],
                unparsed="unparsed_ingredients" in recipe.quality_flags,
            ))
            warnings.append("不辣检查基于菜谱配料、步骤和显式口味标签；未验证品牌配方或个人辣味感受。")
        if constraints.max_minutes is not None:
            reasons.append(f"菜谱缺少可核验的总烹饪时间，无法保证 {constraints.max_minutes} 分钟内完成。")
        if constraints.inventory is not None:
            if not constraints.inventory:
                reasons.append("库存明确为空，无法使用任何配料。")
            else:
                missing = self.inventory_missing(recipe, constraints.inventory)
                if missing:
                    reasons.append("严格库存缺少食材或调料：" + "、".join(missing))
            warnings.append("库存只核对食材种类，不保证数量充足；调料未默认视为已有。")
        if reasons:
            return RuleDecision(False, reasons, warnings=warnings)

        score = 0.0
        for item in constraints.preferred_ingredients:
            if self.preference_matches(recipe, item):
                score += 3.0
                if is_menu_group_preference(item):
                    reasons.append(f"源菜位及原料支持偏好「{item}」；不表示份量已核算。")
                else:
                    reasons.append(f"配料包含偏好食材「{item}」。")
        for goal in dict.fromkeys(constraints.health_goals):
            rule = self.config["health_goals"].get(goal)
            if rule is None:
                warnings.append(f"健康目标「{goal}」暂未配置定性排序规则。")
                continue
            evidence = self.goal_evidence(recipe, goal)
            assert evidence is not None
            score += evidence.score
            if evidence.has_preference:
                ranked_categories = evidence.category_foods if evidence.category_rank_enabled else ()
                preferred = evidence.preferred_foods if evidence.preferred_rank_terms is None else tuple(
                    food for food in evidence.preferred_foods
                    if any(contains_term(food, term) for term in evidence.preferred_rank_terms)
                )
                ranked_foods = list(dict.fromkeys([*preferred, *ranked_categories]))
                if evidence.positive_rank_enabled and (ranked_foods or evidence.good_methods):
                    reasons.append(f"{goal}偏好排序参考：{'、'.join([*ranked_foods, *evidence.good_methods])}；仅为食材或做法依据。")
                if evidence.category_foods and (
                    not evidence.category_rank_enabled or not evidence.positive_rank_enabled
                ):
                    reasons.append(
                        f"{goal}食材来源参考：{'、'.join(evidence.category_foods)}；"
                        "配料来源不作为当前成菜类别的排序加分，未判断摄入量或健康达标。"
                    )
            if evidence.has_rank_caution:
                caution = [*evidence.discouraged_foods, *evidence.bad_methods]
                caution += [f"{term}（原始配料声明）" for term in evidence.raw_cautions]
                caution += [f"{term}（步骤声明）" for term in evidence.step_cautions]
                reasons.append(f"{goal}偏好降低该配方排序，依据：{'、'.join(caution)}；未判断摄入量。")
            if evidence.has_attention:
                attention = list(evidence.attention_foods)
                attention += [f"{term}（原始配料声明）" for term in evidence.raw_attention]
                attention += [f"{term}（步骤声明）" for term in evidence.step_attention]
                reasons.append(f"{goal}含钠来源待核：{'、'.join(attention)}；仅提醒，不因出现而扣分。需核品牌、用量、分餐和全天摄入，不判定高钠或低钠。")
            warnings.append(rule["note"])
        if "清淡" in supported_flavor_preferences(constraints.preferences) and self.soft_goal_scores(recipe, constraints)[-1]:
            score += 1
            reasons.append("清淡做法偏好参考蒸、煮或焯；调味用量仍未知。")
        if not reasons:
            reasons.append("已按当前已知的食材限制和菜谱来源筛选。")
        return RuleDecision(True, reasons, score, list(dict.fromkeys(warnings)))
