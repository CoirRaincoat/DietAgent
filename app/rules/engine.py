"""Deterministic, source-aware screening; health goals never waive hard rules."""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.domain.models import Constraints, Recipe

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
    def __init__(self, config_path: Path | None = None):
        path = Path(config_path) if config_path is not None else DEFAULT_RULES_PATH
        self.config: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.aliases: dict[str, list[str]] = self.config["aliases"]
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
            self._known_foods.update(value.get("prefer_terms", []))
        self._known_foods.update(self.config["spicy_terms"])

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
        return []

    def unresolved_allergies(self, constraints: Constraints) -> list[str]:
        return [a for a in constraints.allergies if not self._allergen_keys(a)]

    def _food_text(self, recipe: Recipe) -> str:
        # Titles and promotional labels are deliberately absent. Step-only
        # ingredients and optional ingredients must still undergo screening.
        return "\n".join(
            [recipe.raw_ingredients, *(i.name for i in recipe.ingredients), recipe.steps]
        )

    def _allergen_matches(self, text: str, key: str) -> list[str]:
        if key.startswith("specific:"):
            terms = self.aliases_for(key.split(":", 1)[1])
        else:
            rule = self.config["allergens"][key]
            terms = rule["terms"]
            for ignored in rule.get("ignore_phrases", []):
                text = text.replace(ignored, "")
        return [term for term in terms if contains_term(text, term)]

    def food_matches(self, recipe: Recipe, value: str) -> list[str]:
        """Match actual food text using aliases or a recognized food group."""
        text = self._food_text(recipe)
        groups = self._allergen_keys(value)
        if groups:
            return sorted({term for key in groups for term in self._allergen_matches(text, key)})
        return [term for term in self.aliases_for(value) if contains_term(text, term)]

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
        if not recipe.eligible or not recipe.steps.strip() or not recipe.ingredients:
            reasons.append("菜谱缺少可用食材或步骤，或属于不可直接推荐的加工组件。")
        unresolved = self.unresolved_allergies(constraints)
        if unresolved:
            reasons.append("过敏原缺少已支持映射，需要澄清：" + "、".join(unresolved))
        if constraints.allergies and "unparsed_ingredients" in recipe.quality_flags:
            reasons.append("食材解析不完整，无法核对过敏限制。")

        text = self._food_text(recipe)
        for allergy in constraints.allergies:
            matched = sorted({term for key in self._allergen_keys(allergy)
                              for term in self._allergen_matches(text, key)})
            if matched:
                reasons.append(f"过敏限制「{allergy}」命中配料或步骤：{'、'.join(matched)}。")
        if constraints.allergies:
            uncertain = [term for term in self.config["uncertain_composites"]
                         if contains_term(text, term)]
            if uncertain:
                reasons.append("复合配料成分不明确，无法确认过敏限制：" + "、".join(uncertain))
            warnings.append("仅核对菜谱文字中的已知过敏原；未验证品牌配方或烹饪交叉接触。")
        for excluded in constraints.excluded_ingredients:
            matches = self.food_matches(recipe, excluded)
            if matches:
                reasons.append(f"明确排除「{excluded}」命中配料或步骤：{'、'.join(matches)}。")
        if constraints.no_spicy:
            matches = [term for term in self.config["spicy_terms"] if contains_term(text, term)]
            if matches:
                reasons.append("不辣要求命中辣味配料：" + "、".join(matches))
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
            if self.food_matches(recipe, item):
                score += 3.0
                reasons.append(f"配料包含偏好食材「{item}」。")
        for goal in constraints.health_goals:
            rule = self.config["health_goals"].get(goal)
            if rule is None:
                warnings.append(f"健康目标「{goal}」暂未配置定性排序规则。")
                continue
            matched_categories = set(recipe.categories) & set(rule.get("prefer_categories", []))
            preferred = [term for term in rule.get("prefer_terms", []) if contains_term(text, term)]
            discouraged = [term for term in rule.get("discourage_terms", []) if contains_term(text, term)]
            methods = set(recipe.methods)
            good_methods = methods & set(rule.get("prefer_methods", []))
            bad_methods = methods & set(rule.get("discourage_methods", []))
            score += 2 * bool(matched_categories) + 2 * bool(preferred) + bool(good_methods)
            score -= 3 * bool(discouraged) + 2 * bool(bad_methods)
            if matched_categories or preferred or good_methods:
                category_names = {"vegetable": "蔬菜类别", "protein": "蛋白质来源类别", "staple": "主食类别"}
                evidence = preferred or sorted(good_methods) or [
                    category_names.get(category, category) for category in sorted(matched_categories)
                ]
                reasons.append(f"{goal}偏好排序参考：{'、'.join(evidence)}；仅为食材或做法依据。")
            if discouraged or bad_methods:
                evidence = discouraged or sorted(bad_methods)
                reasons.append(f"{goal}偏好降低该配方排序，依据：{'、'.join(evidence)}。")
            warnings.append(rule["note"])
        preference_text = " ".join(constraints.preferences)
        if "清淡" in preference_text and set(recipe.methods) & {"蒸", "煮", "焯"}:
            score += 1
            reasons.append("清淡做法偏好参考蒸、煮或焯；调味用量仍未知。")
        if not reasons:
            reasons.append("已按当前已知的食材限制和菜谱来源筛选。")
        return RuleDecision(True, reasons, score, list(dict.fromkeys(warnings)))
