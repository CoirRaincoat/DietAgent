"""Ingredient-traceable qualitative nutrition; amounts are never estimated."""

from functools import lru_cache

from app.domain.models import Constraints, Ingredient, Recipe
from app.domain.recipe_origin import display_source_row, is_generated_recipe
from app.infrastructure.data import _VEGETABLE_HOMONYMS, _has_protein_source
from app.nutrition.models import (
    GoalMatch,
    IngredientContribution,
    MenuNutrition,
    NutritionRisk,
    NutritionSource,
    RecipeNutrition,
)
from app.rules.engine import RuleEngine, contains_term

# Food names establish possible sources, never concentrations or doses.
# Unknown names remain unclassified; recipe titles and labels are not evidence.
_CARBOHYDRATE_FOODS = (
    "大米", "粳米", "籼米", "米饭", "糯米", "糙米", "小米", "燕麦", "藜麦", "荞麦",
    "全麦", "面粉", "面条", "挂面", "意面", "意大利面", "通心粉", "乌冬", "米粉",
    "粉丝", "粉条", "淀粉", "馒头", "面包", "吐司", "饺子皮", "馄饨皮", "年糕",
    "土豆", "马铃薯", "红薯", "紫薯", "山药", "淮山", "芋头", "芋艿", "莲藕",
    "玉米", "南瓜", "红豆", "绿豆", "豌豆", "鹰嘴豆", "白糖", "白砂糖", "砂糖",
    "冰糖", "红糖", "蜂蜜", "糖浆",
)
_FIBER_FOODS = (
    "白菜", "生菜", "青菜", "菠菜", "油麦菜", "西兰花", "西蓝花", "花菜", "菜花",
    "芥蓝", "卷心菜", "包菜", "芹菜", "空心菜", "苋菜", "荠菜", "茄子", "番茄",
    "西红柿", "黄瓜", "丝瓜", "冬瓜", "苦瓜", "南瓜", "西葫芦", "萝卜", "芦笋",
    "竹笋", "冬笋", "春笋", "蘑菇", "香菇", "木耳", "银耳", "秋葵", "莴笋",
    "菜心", "韭菜", "莲藕", "藕", "豆芽", "金针菇", "口蘑", "塔菜", "娃娃菜",
    "山药", "淮山", "海带", "紫菜", "豇豆", "刀豆", "毛豆", "青椒", "甜椒",
    "土豆", "马铃薯", "红薯", "紫薯", "芋头",
    "芋艿", "玉米", "糙米", "燕麦", "藜麦", "全麦", "红豆", "绿豆", "黑豆",
    "黄豆", "大豆", "豌豆", "鹰嘴豆", "花生", "核桃", "杏仁", "腰果", "芝麻",
    "苹果", "梨", "香蕉", "芒果", "草莓", "蓝莓", "葡萄", "桃", "橙", "柚",
    "猕猴桃", "奇异果", "牛油果", "红枣", "莲子", *_VEGETABLE_HOMONYMS,
)
_FAT_OILS = (
    "食用油", "植物油", "橄榄油", "菜籽油", "玉米油", "花生油", "芝麻油", "香油",
    "麻油", "葵花籽油", "大豆油", "黄油", "猪油", "奶油",
)
_FAT_FOODS = (*_FAT_OILS, "核桃", "花生", "杏仁", "腰果", "松子", "榛子", "芝麻", "牛油果")
_PLANT_PROTEINS = ("黄豆", "大豆", "毛豆", "黑豆", "红豆", "绿豆", "豌豆", "鹰嘴豆")
_MILK_PROTEINS = ("牛奶", "鲜奶", "纯牛奶", "酸奶", "奶粉", "豆浆", "奶酪", "乳酪", "芝士")
_CONDIMENT_MARKERS = ("酱", "汁", "汤", "精", "调味", "料包", "鸡血藤", "鸡冠花")
_SUGAR_SOURCES = ("白糖", "白砂糖", "砂糖", "冰糖", "红糖", "蜂蜜", "糖浆", "炼乳", "炼奶")
_SODIUM_SOURCES = ("盐", "酱油", "生抽", "老抽", "蚝油", "豆瓣酱", "豆豉", "腐乳", "咸菜", "腌菜")
_ROLE_LABELS = {
    "protein": "蛋白质来源",
    "carbohydrate": "碳水化合物来源",
    "fat": "脂肪来源",
    "dietary_fiber": "膳食纤维来源",
}
_LIMITATIONS = [
    "仅按已解析的真实食材名称与记录的做法识别可能来源；未识别不表示该营养成分不存在。",
    "缺少可核验的食物成分、烹调损耗和成品分餐数据，不计算精确营养量、占比或摄入达标率。",
    "健康目标匹配是食材与做法的定性排序解释，不表示低糖、低钠、低热量或医疗效果。",
]


@lru_cache(maxsize=1)
def _rules() -> RuleEngine:
    return RuleEngine()


def _unique(values):
    return list(dict.fromkeys(values))


def _has(name: str, terms) -> bool:
    return any(contains_term(name, term) for term in terms)


def _roles(ingredient: Ingredient) -> list[str]:
    name = ingredient.name
    condiment = _has(name, _CONDIMENT_MARKERS)
    oil = name == "油" or _has(name, _FAT_OILS)
    roles = []
    # The shared predicate ignores plant/spice homonyms without discarding the
    # genuine protein text in a mixed ingredient (e.g. 肉桂鸡翅).
    if not condiment and not oil and not _has(name, ("蛋糕", "蛋挞")):
        if _has_protein_source([ingredient]) or _has(name, _PLANT_PROTEINS + _MILK_PROTEINS):
            roles.append("protein")
    if not condiment and not oil and _has(name, _CARBOHYDRATE_FOODS):
        roles.append("carbohydrate")
    if not condiment and (name == "油" or _has(name, _FAT_FOODS)):
        roles.append("fat")
    refined = _has(name, ("淀粉", "粉丝", "粉条", "燕麦奶", "杏仁奶", "花生奶"))
    if not condiment and not oil and not refined and _has(name, _FIBER_FOODS):
        roles.append("dietary_fiber")
    return roles


def _source_names(contributions: list[IngredientContribution], role: str) -> list[str]:
    return _unique(item.ingredient_name for item in contributions if role in item.roles)


def _goal_matches(recipes: list[Recipe], constraints: Constraints,
                  contributions: list[IngredientContribution]) -> list[GoalMatch]:
    rules = _rules()
    # Goal ordering and explanation inspect the same source evidence. Nutrient
    # source contributions remain separate and never turn into nutrient doses.
    output = []
    for goal in _unique(constraints.health_goals):
        rule = rules.config["health_goals"].get(goal)
        if rule is None:
            output.append(GoalMatch(
                goal=goal, status="insufficient_data",
                limitation=f"健康目标『{goal}』暂无已配置规则，不能判断匹配程度。",
            ))
            continue
        evidence = [(recipe, rules.goal_evidence(recipe, goal)) for recipe in recipes]
        preferred = _unique(name for _, item in evidence if item is not None
                            for name in [*item.preferred_foods, *item.category_foods])
        ranked_preferred = _unique(
            name for _, item in evidence if item is not None
            for name in [
                *(item.category_foods if item.category_rank_enabled else ()),
                *(name for name in item.preferred_foods
                  if item.preferred_rank_terms is None
                  or any(contains_term(name, term) for term in item.preferred_rank_terms)),
            ]
        )
        auxiliary_foods = [name for name in preferred if name not in ranked_preferred]
        discouraged = _unique(name for _, item in evidence if item is not None
                              for name in item.discouraged_foods)
        attention = _unique(name for _, item in evidence if item is not None for name in item.attention_foods)
        good_methods = _unique(method for _, item in evidence if item is not None for method in item.good_methods)
        bad_methods = _unique(method for _, item in evidence if item is not None for method in item.bad_methods)
        reasons = []
        if ranked_preferred:
            # Membership in a category/ranking reference is not proof of the
            # amount, culinary main ingredient or share of an individual food.
            reasons.append("排序规则所参考的已声明食材：" + "、".join(ranked_preferred) + "；未核主辅比例，不据此评整道菜的健康功效。")
        if auxiliary_foods:
            reasons.append("其他已声明食材：" + "、".join(auxiliary_foods) + "；仅保留来源事实，不凭辅料给整道菜加健康分。")
        if good_methods:
            reasons.append("记录的做法符合当前定性偏好：" + "、".join(good_methods) + "；调味与用量仍需核对。")
        if discouraged:
            reasons.append("当前目标需关注的配料：" + "、".join(discouraged) + "；不据此判定摄入量。")
        if attention:
            reasons.append("含钠来源待核：" + "、".join(attention) + "；仅提醒，不因出现而扣分，不判定高钠或低钠。")
        if bad_methods:
            reasons.append("当前目标需关注的做法：" + "、".join(bad_methods) + "；不能由做法推算热量。")
        for recipe, item in evidence:
            if item is not None and item.raw_cautions:
                reasons.append(f"《{recipe.name}》原始配料声明需关注：" + "、".join(item.raw_cautions) + "；解析不完整时不能遗漏。")
            if item is not None and item.step_cautions:
                reasons.append(f"《{recipe.name}》步骤声明需关注：" + "、".join(item.step_cautions) + "；未计算添加量。")
            if item is not None and item.raw_attention:
                reasons.append(f"《{recipe.name}》原始配料声明含钠来源待核：" + "、".join(item.raw_attention) + "；未核品牌、用量与分餐，仅提醒、不因出现而扣分。")
            if item is not None and item.step_attention:
                reasons.append(f"《{recipe.name}》步骤声明含钠来源待核：" + "、".join(item.step_attention) + "；未计算添加量，仅提醒、不因出现而扣分。")
        status = "caution" if any(item is not None and item.has_caution for _, item in evidence) else (
            "preference_match" if preferred or good_methods else "insufficient_data"
        )
        sources = [NutritionSource(source_id=source_id,
                                   title=rules.config["sources"][source_id]["title"],
                                   url=rules.config["sources"][source_id]["url"])
                   for source_id in rule.get("sources", [])]
        output.append(GoalMatch(
            goal=goal, status=status, ingredient_names=_unique([*preferred, *discouraged, *attention]),
            methods=_unique([*good_methods, *bad_methods]), reasons=reasons,
            limitation=rule["note"], sources=sources,
        ))
    return output


def analyze_recipe(recipe: Recipe, constraints: Constraints) -> RecipeNutrition:
    """Return food-source facts, never estimates or an override of rule checks."""
    contributions = []
    seen = set()
    for ingredient in recipe.ingredients:
        if ingredient.name in seen:
            continue
        seen.add(ingredient.name)
        roles = _roles(ingredient)
        if roles:
            contributions.append(IngredientContribution(
                recipe_id=recipe.recipe_id, source_row=display_source_row(recipe),
                ingredient_name=ingredient.name, roles=roles,
                explanation="根据食材名称识别为" + "、".join(_ROLE_LABELS[role] for role in roles)
                            + "；未推算营养含量。",
                quantity_recorded=ingredient.quantity is not None and ingredient.unit is not None,
            ))
    source_names = {role: _source_names(contributions, role) for role in _ROLE_LABELS}
    risks = []

    def risk(code, message, names=()):
        risks.append(NutritionRisk(code=code, message=message,
                                   ingredient_names=_unique(names), recipe_ids=[recipe.recipe_id]))

    missing_amounts = [item.name for item in recipe.ingredients
                       if item.quantity is None or item.unit is None]
    if missing_amounts:
        risk("quantity_incomplete", "部分食材没有可解析的数量或单位，不能据此计算个人摄入量。", missing_amounts)
    if not recipe.ingredients or "unparsed_ingredients" in recipe.quality_flags:
        risk("ingredients_incomplete", "食材记录或解析不完整，来源识别可能遗漏，需核对原始菜谱。")
    composites = [item.name for item in recipe.ingredients
                  if _has(item.name, _rules().config["uncertain_composites"])
                  or _has(item.name, ("酱", "高汤", "调味", "料包"))]
    if composites:
        risk("composite_unknown", "复合配料的品牌配方不明，不能确定其营养组成及隐藏过敏原。", composites)
    sugars = [item.name for item in recipe.ingredients if _has(item.name, _SUGAR_SOURCES)]
    if sugars:
        risk("added_sugar_source", "配料含添加糖来源；未计算成品总糖及个人摄入量。", sugars)
    sodium = [item.name for item in recipe.ingredients if _has(item.name, _SODIUM_SOURCES)]
    if sodium:
        risk("sodium_source", "配料含盐或含钠调味品；品牌成分和成品分餐数据不足，不能判断钠摄入量。", sodium)
    if constraints.allergies:
        risk("allergen_scope", "规则只核对已知菜谱文本；品牌配方、交叉接触仍需另行确认。")
    decision = _rules().evaluate(recipe, constraints)
    if not decision.allowed:
        risk("constraint_conflict", "该菜谱未通过当前规则核对：" + "；".join(decision.reasons))
    suitable = [label + "可追溯到：" + "、".join(source_names[role]) + "；份量和实际摄入量待确认。"
                for role, label in _ROLE_LABELS.items() if source_names[role]] if decision.allowed else []
    return RecipeNutrition(
        recipe_id=recipe.recipe_id, source_row=display_source_row(recipe),
        protein_sources=source_names["protein"], carbohydrate_sources=source_names["carbohydrate"],
        fat_sources=source_names["fat"], dietary_fiber=source_names["dietary_fiber"],
        ingredient_contributions=contributions,
        goal_matches=_goal_matches([recipe], constraints, contributions),
        suitable_reasons=suitable, risks=risks, limitations=list(_LIMITATIONS) + (
            ["本菜为本地生成方案，未经过厨房试做；食材仅为方案声明，不是原库配方或实测营养依据。"]
            if is_generated_recipe(recipe) else []
        ),
    )


def analyze_menu(recipes: list[Recipe], constraints: Constraints) -> MenuNutrition:
    """Aggregate sources and provenance without summing guessed nutrients."""
    unique_recipes = list({recipe.recipe_id: recipe for recipe in recipes}.values())
    analyses = [analyze_recipe(recipe, constraints) for recipe in unique_recipes]
    contributions = [item for analysis in analyses for item in analysis.ingredient_contributions]
    sources = {role: _source_names(contributions, role) for role in _ROLE_LABELS}
    risks = [risk for analysis in analyses for risk in analysis.risks]
    suitable = []
    if analyses and not any(risk.code == "constraint_conflict" for risk in risks):
        for role, label in _ROLE_LABELS.items():
            if sources[role]:
                suitable.append("本餐" + label + "包括：" + "、".join(sources[role]) + "。")
    if not analyses:
        risks.append(NutritionRisk(code="empty_menu", message="没有可分析的菜谱，不能评估本餐组成。"))
    elif any(not sources[role] for role in _ROLE_LABELS):
        missing = [_ROLE_LABELS[role] for role in _ROLE_LABELS if not sources[role]]
        risks.append(NutritionRisk(
            code="unidentified_sources", message="本餐尚未识别" + "、".join(missing)
                + "；可能受食材解析与词表覆盖限制，不表示营养成分不存在。",
            recipe_ids=[recipe.recipe_id for recipe in unique_recipes],
        ))
    return MenuNutrition(
        recipe_ids=[recipe.recipe_id for recipe in unique_recipes],
        protein_sources=sources["protein"], carbohydrate_sources=sources["carbohydrate"],
        fat_sources=sources["fat"], dietary_fiber=sources["dietary_fiber"],
        ingredient_contributions=contributions,
        goal_matches=_goal_matches(unique_recipes, constraints, contributions),
        suitable_reasons=suitable, risks=risks,
        limitations=[*_LIMITATIONS, "本餐汇总仅合并已识别来源，不代表膳食均衡或个体目标已达标。"],
        recipe_analyses=analyses,
    )
