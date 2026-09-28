"""Deterministic whole-menu balance checks over traceable recipe metadata."""

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from app.domain.models import Recipe

CATEGORY_KEYS = ("protein", "vegetable", "staple", "soup")
COLD_DISH_MARKERS = ("凉拌", "冷拌", "冰镇")
HOT_METHODS = {"蒸", "煮", "炖", "炒", "烤", "煎", "炸", "焖"}
_SERVING_TEMPERATURE = re.compile(
    r"(?P<hot>趁热(?:食用|享用|吃)|热食|热吃)"
    r"|(?P<cold>冷食|冷吃|凉吃)"
)
# Equipment and previously cooked ingredients are not new heating operations.
_HEATING_ACTION = (
    r"(?:蒸(?!盘|锅|架|笼|鱼豉油)|煮|炖|炒(?!锅)|烤(?!箱|盘|架)|煎|炸|焖)"
    r"(?!好的|好了|过的|完的)"
)
_TEMPERATURE_ACTION = re.compile(
    rf"(?P<hot>{_HEATING_ACTION}|开始烹饪)"
    r"|(?P<cold>凉拌|冷拌|冰镇|冷藏|放凉|晾凉|冷却|过凉水|泡冰水)"
)
_SERVING_ACTION = re.compile(r"食用|享用|品尝|开吃|装盘|即可")


@dataclass(frozen=True)
class MenuBalance:
    """Internal deterministic planning signal; never part of the API schema."""

    status: Literal["balanced", "partial", "limited"]
    score: int
    category_counts: dict[str, int] = field(default_factory=dict)
    method_counts: dict[str, int] = field(default_factory=dict)
    temperature_counts: dict[str, int] = field(default_factory=dict)
    strengths: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def serving_temperature(recipe: Recipe) -> str:
    """Use serving evidence and ordered steps, never unordered method tags alone.

    This remains a text heuristic, not a measurement. A named cold dish can
    include hot dressing preparation; cooling a filling or refrigerating a
    marinade does not by itself establish the finished dish's temperature.
    """
    steps = recipe.steps
    if re.search(
        r"(?:趁热(?:食用|享用)?|热食|热吃)[^。；;\n]{0,16}(?:或|也可)"
        r"[^。；;\n]{0,16}(?:放凉|晾凉|冷藏|冷食|冷吃)", steps
    ):
        return "unknown"
    serving = list(_SERVING_TEMPERATURE.finditer(steps))
    if serving:
        # An explicitly optional serving temperature has no single answer.
        if len({match.lastgroup for match in serving}) > 1 and re.search(
            r"均可|都可|也可|或|或者", steps
        ):
            return "unknown"
        final_serving = serving[-1]
        later_actions = list(_TEMPERATURE_ACTION.finditer(steps, final_serving.end()))
        if not later_actions:
            return final_serving.lastgroup or "unknown"

    # The dish name describes the finished dish, unlike a later hot oil/sauce
    # step. Explicit serving-temperature instructions above take precedence.
    if any(marker in recipe.name for marker in COLD_DISH_MARKERS):
        return "cold"

    actions = [
        match for match in _TEMPERATURE_ACTION.finditer(steps)
        if match.group() != "开始烹饪" or HOT_METHODS & set(recipe.methods)
    ]
    if actions:
        last = actions[-1]
        if last.lastgroup == "hot":
            return "hot"
        # Letting hot food cool slightly does not establish a cold serving.
        if re.search(r"(?:稍微|稍稍|稍|略微|略)$", steps[:last.start()]):
            return "unknown"
        # Cooling is final-serving evidence only when followed by serving,
        # rather than an unfinished preparation or storage instruction.
        tail = steps[last.end():]
        if _SERVING_ACTION.search(tail) and not re.search(
            r"备用|保存|储存|腌制|馅料|面团", tail
        ):
            return "cold"
        return "unknown"
    if re.search(_HEATING_ACTION, recipe.name):
        return "hot"
    return "unknown"


def _targets(dish_count: int) -> tuple[int, int, int, int]:
    vegetable = 2 if dish_count >= 4 else int(dish_count >= 2)
    protein = int(dish_count >= 2)
    staple = int(dish_count >= 3)
    methods = min(3, dish_count)
    return vegetable, protein, staple, methods


def balance_rank(recipes: Sequence[Recipe], target_count: int | None = None) -> tuple[int, ...]:
    """Return a higher-is-better lexicographic rank for deterministic selection."""
    target = target_count if target_count is not None else len(recipes)
    vegetable_target, protein_target, staple_target, method_target = _targets(target)
    category_counts = Counter(
        category
        for recipe in recipes
        for category in set(recipe.categories)
        if category in CATEGORY_KEYS
    )
    method_counts = Counter(method for recipe in recipes for method in set(recipe.methods))
    temperatures = Counter(serving_temperature(recipe) for recipe in recipes)
    known_methods = sum(method_counts.values())
    dominant_method = max(method_counts.values(), default=0)
    category_types = sum(category_counts[key] > 0 for key in CATEGORY_KEYS[:3])
    temperature_mix = int(temperatures["hot"] > 0 and temperatures["cold"] > 0)
    cold_target_met = int(target < 4 or temperatures["cold"] > 0)
    return (
        min(category_counts["vegetable"], vegetable_target),
        min(category_counts["protein"], protein_target),
        min(category_counts["staple"], staple_target),
        category_types,
        # Once the three roles are covered, extra protein dishes and extra
        # staples should not displace a complementary vegetable dish just
        # because their preparation method is different.
        -max(0, category_counts["protein"] - protein_target) if protein_target else 0,
        -max(0, category_counts["staple"] - staple_target) if staple_target else 0,
        min(len(method_counts), method_target),
        cold_target_met,
        temperature_mix,
        -max(0, dominant_method - 1),
        -max(0, known_methods - len(recipes)),
    )


def analyze_menu_balance(recipes: Sequence[Recipe]) -> MenuBalance:
    """Describe category, method, and temperature balance without invented amounts."""
    count = len(recipes)
    category_counts = Counter(
        category
        for recipe in recipes
        for category in set(recipe.categories)
        if category in CATEGORY_KEYS
    )
    method_counts = Counter(method for recipe in recipes for method in set(recipe.methods))
    temperature_counts = Counter(serving_temperature(recipe) for recipe in recipes)
    categories = {key: category_counts[key] for key in CATEGORY_KEYS}
    temperatures = {
        "hot": temperature_counts["hot"],
        "cold": temperature_counts["cold"],
        "unknown": temperature_counts["unknown"],
    }
    vegetable_target, protein_target, staple_target, method_target = _targets(count)
    strengths: list[str] = []
    gaps: list[str] = []
    limitations = [
        "菜品类别、做法和冷热属性来自菜谱文字的启发式识别，不代表营养含量或实际出餐温度。",
        "菜谱缺少统一份量，不能据此计算荤素重量比例或个人摄入量。",
    ]
    score = 100 if count else 0

    vegetable_gap = max(0, vegetable_target - categories["vegetable"])
    protein_gap = max(0, protein_target - categories["protein"])
    staple_gap = max(0, staple_target - categories["staple"])
    if vegetable_gap:
        gaps.append(f"按 {count} 道菜的工程规则，蔬菜类菜品还缺 {vegetable_gap} 道。")
        score -= 15 * vegetable_gap
    if protein_gap:
        gaps.append("未识别到蛋白质来源菜品。")
        score -= 20 * protein_gap
    if staple_gap:
        gaps.append("未识别到主食类菜品；请确认主食是否另行安排。")
        score -= 10 * staple_gap
    if not vegetable_gap and not protein_gap and count >= 2:
        strengths.append("已覆盖蔬菜类菜品和蛋白质来源菜品。")
    if not staple_gap and staple_target:
        strengths.append("已识别主食类菜品。")

    method_gap = max(0, method_target - len(method_counts))
    if method_gap:
        gaps.append(f"已识别的烹饪方式种类不足，距离当前目标还差 {method_gap} 种。")
        score -= 8 * method_gap
    elif method_target:
        strengths.append(f"已覆盖 {len(method_counts)} 种可识别烹饪方式。")

    if count >= 4 and not temperatures["cold"]:
        gaps.append("未识别到有明确冷食证据的菜品，冷热搭配仅能确认热菜部分。")
        score -= 5
    elif temperatures["hot"] and temperatures["cold"]:
        strengths.append("菜谱文字中同时存在热菜和冷食证据，可形成冷热搭配。")
    if temperatures["unknown"]:
        limitations.append(
            f"有 {temperatures['unknown']} 道菜缺少明确冷热证据，无法判断时保持未知而不推断。"
        )

    if count and not any(categories.values()) and not method_counts:
        gaps.append("菜谱缺少可用的类别和做法元数据，无法判断整桌搭配。")
        score = min(score, 40)
    score = max(0, min(100, score))
    if not count or (count and not any(categories.values()) and not method_counts):
        status = "limited"
    elif score >= 80 and not gaps:
        status = "balanced"
    elif score >= 55:
        status = "partial"
    else:
        status = "limited"
    return MenuBalance(
        status=status,
        score=score,
        category_counts=categories,
        method_counts=dict(method_counts),
        temperature_counts=temperatures,
        strengths=strengths,
        gaps=gaps,
        limitations=limitations,
    )


def balance_summary(balance: MenuBalance) -> str:
    """Render a concise user-facing summary without internal scores or unknown counts."""
    categories = balance.category_counts
    labels = {
        "vegetable": "蔬菜类菜",
        "protein": "蛋白质来源菜",
        "staple": "主食",
        "soup": "汤",
    }
    present = [
        f"{count} 道{labels[key]}"
        for key in CATEGORY_KEYS
        if (count := categories.get(key, 0))
    ]
    if not present and not balance.method_counts:
        return "现有菜谱标签不足，暂时无法完整说明菜品搭配。"
    category_text = "、".join(present) if present else "现有菜品"
    methods = "、".join(list(balance.method_counts)[:4])
    if methods:
        return f"搭配上包含{category_text}，做法有{methods}。"
    return f"搭配上包含{category_text}。"
