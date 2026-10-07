"""Source food exclusions tied to dish positions, not whole-meal bans."""
from collections.abc import Sequence

from app.domain.models import Constraints, Recipe
from app.rules.engine import RuleEngine, compact

_MUSHROOMS = (
    "菌菇", "蘑菇", "香菇", "金针菇", "杏鲍菇", "白玉菇", "蟹味菇", "海鲜菇",
    "口蘑", "草菇", "茶树菇", "松茸", "猴头菇", "鸡腿菇", "平菇", "牛肝菌", "木耳", "银耳",
)


def local_food_supported(food: str, rules: RuleEngine) -> bool:
    return compact(food) in {"菌菇", "菌菇菜"} or not rules.unresolved_exclusions(
        Constraints(excluded_ingredients=[food]))


def local_food_hits(recipe: Recipe, food: str, rules: RuleEngine) -> bool:
    if compact(food) in {"菌菇", "菌菇菜"}:
        # Ingredients/steps include stocks and sauces too; a title alone is not
        # proof of ingredients. This finite culinary group is NOT an allergen.
        text = "\n".join([recipe.raw_ingredients, recipe.steps, *(i.name for i in recipe.ingredients)])
        return any(name in text for name in _MUSHROOMS)
    return bool(rules.food_matches(recipe, food))


def slot_food_allowed(recipe: Recipe, slot: int, constraints: Constraints, rules: RuleEngine) -> bool:
    return all(not local_food_hits(recipe, food, rules)
               for food in constraints.slot_food_exclusions.get(slot, ()))


def scoped_food_menu_issue(menu: Sequence[Recipe], constraints: Constraints, rules: RuleEngine) -> str | None:
    for slot, foods in constraints.slot_food_exclusions.items():
        if not 1 <= slot <= len(menu):
            return "已保存的局部食材限制超出当前菜数，请先核对菜数和菜位；不自动扩大为整餐禁用。"
        if any(not local_food_supported(food, rules) for food in foods):
            return f"第{slot}道的局部食材范围尚无法可靠识别，请明确具体食材。"
        if not slot_food_allowed(menu[slot - 1], slot, constraints, rules):
            return f"第{slot}道仍含该菜位明确不要的食材，请调整该菜；不影响其他菜位的保留许可。"
    return None
