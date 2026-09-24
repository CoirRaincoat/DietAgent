"""Evidence-limited food and method notes; no invented nutrient amounts."""

from functools import lru_cache

from app.domain.models import Constraints, Recipe
from app.infrastructure.data import _has_protein_source
from app.nutrition.structured import analyze_menu, analyze_recipe
from app.rules.engine import RuleEngine, contains_term

__all__ = ["analyze", "analyze_menu", "analyze_recipe"]


@lru_cache(maxsize=1)
def _rules() -> RuleEngine:
    return RuleEngine()


def analyze(recipe: Recipe, constraints: Constraints) -> list[str]:
    rules = _rules()
    names = [item.name for item in recipe.ingredients]
    text = "、".join(names)
    notes: list[str] = []
    # Reuse the same pure ingredient evidence predicate as data classification.
    # Titles, condiments and misleading names (e.g. 鸡腿菇) cannot add protein facts.
    protein_sources = [
        item.name for item in recipe.ingredients if _has_protein_source([item])
    ]
    if protein_sources:
        notes.append("配料含蛋白质来源：" + "、".join(protein_sources[:3]) + "；未计算蛋白质含量。")
    if "vegetable" in recipe.categories:
        notes.append("含蔬菜类食材，可作为本餐食物多样性的一部分；份量需另行确定。")
    if "staple" in recipe.categories:
        notes.append("包含主食类食材；尚未计算碳水化合物或个人分配量。")
    if recipe.methods:
        notes.append("记录的烹饪方式：" + "、".join(recipe.methods) + "；做法本身不能证明低油或低热量。")
    added_sugars = [term for term in ["糖", "蜂蜜", "糖浆", "炼乳"] if contains_term(text, term)]
    if added_sugars:
        notes.append("配料含添加糖来源；用量及食物本身含糖未知，不能判断整道菜总糖。")
    if any(contains_term(text, term) for term in ["盐", "酱油", "生抽", "老抽", "蚝油", "豆瓣酱"]):
        notes.append("配料含盐或含钠调味品；没有可靠用量，不能判断整道菜钠含量。")
    for goal in constraints.health_goals:
        rule = rules.config["health_goals"].get(goal)
        if rule:
            notes.append(rule["note"])
    notes.append("本分析为菜谱食材与做法的定性说明；未使用原始标签中的健康功效宣称。")
    return list(dict.fromkeys(notes))
