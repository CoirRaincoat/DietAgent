import pytest

from app.domain.models import Constraints, Ingredient, Recipe
from app.nutrition.qualitative import analyze
from app.rules.engine import RuleEngine


def make_recipe(ingredients, *, name="测试菜", steps="放入锅中煮熟。", labels=None, **kwargs):
    return Recipe(
        recipe_id="FT-R-test", name=name, source_row=1, fingerprint="test",
        raw_ingredients="、".join(ingredients),
        ingredients=[Ingredient(raw=value, name=value) for value in ingredients],
        steps=steps, labels=labels or [], **kwargs,
    )


@pytest.fixture(scope="module")
def rules():
    return RuleEngine()


@pytest.mark.parametrize("allergy,ingredient", [
    ("海鲜", "东星斑"), ("海鲜", "花甲"), ("大豆", "素鸡"), ("豆制品", "香干"), ("坚果", "松仁"),
    ("海鲜", "海米"), ("海鲜", "干贝"), ("海鲜", "鲈鱼"),
    ("shellfish", "牡蛎"), ("虾", "虾皮"), ("蟹", "蟹黄"), ("螃蟹", "蟹黄"),
    ("花生", "花生油"), ("坚果", "核桃仁"), ("坚果", "花生米"),
    ("大豆", "生抽"), ("豆类", "蚕豆"), ("牛奶", "黄油"),
    ("鸡蛋", "蛋清"), ("芝麻", "香油"), ("小麦", "面粉"),
    ("鱼", "木鱼花"), ("芒果", "芒果"), ("啤酒", "啤酒"), ("豆类", "红豆"),
])
def test_allergen_derivatives_and_groups_are_hard_filters(rules, allergy, ingredient):
    decision = rules.evaluate(make_recipe([ingredient]), Constraints(allergies=[allergy]))
    assert not decision.allowed
    assert any("过敏限制" in reason for reason in decision.reasons)


def test_step_only_and_optional_allergens_are_filtered(rules):
    recipe = make_recipe(["茄子"], steps="茄子蒸熟，最后可选撒上花生碎。")
    assert not rules.evaluate(recipe, Constraints(allergies=["花生"])).allowed
    assert not rules.evaluate(recipe, Constraints(excluded_ingredients=["花生"])).allowed


def test_title_and_health_labels_do_not_create_food_or_override_rules(rules):
    safe = make_recipe(["茄子", "醋"], name="鱼香茄子", steps="茄子蒸熟，拌醋。")
    assert rules.evaluate(safe, Constraints(excluded_ingredients=["鱼"])).allowed
    unsafe = make_recipe(["虾"], labels=["低敏", "适合海鲜过敏", "降糖抗癌"])
    assert not rules.evaluate(unsafe, Constraints(allergies=["海鲜"])).allowed


def test_unknown_allergy_does_not_silently_pass(rules):
    result = rules.evaluate(make_recipe(["白菜"]), Constraints(allergies=["神秘食材"]))
    assert not result.allowed
    assert "缺少已支持映射" in " ".join(result.reasons)


def test_unparsed_or_uncertain_ingredients_require_allergy_review(rules):
    recipe = make_recipe(["白菜"], quality_flags=["unparsed_ingredients"])
    assert not rules.evaluate(recipe, Constraints(allergies=["花生"])).allowed
    recipe = make_recipe(["白菜", "火锅底料"])
    assert not rules.evaluate(recipe, Constraints(allergies=["花生"])).allowed


def test_no_spicy_uses_ingredients_and_steps(rules):
    recipe = make_recipe(["豆腐"], steps="煮熟后加入辣椒油。")
    assert not rules.evaluate(recipe, Constraints(no_spicy=True)).allowed


def test_inventory_is_explicit_including_seasoning_and_step_additions(rules):
    recipe = make_recipe(["西红柿", "鸡蛋", "盐"], steps="西红柿与鸡蛋炒熟后加盐。")
    assert rules.evaluate(recipe, Constraints(inventory=["番茄", "鸡蛋", "食盐"])).allowed
    assert not rules.evaluate(recipe, Constraints(inventory=["番茄", "鸡蛋"])).allowed
    assert not rules.evaluate(recipe, Constraints(inventory=[])).allowed
    hidden = make_recipe(["白菜"], steps="白菜蒸熟后撒花生米。")
    assert not rules.evaluate(hidden, Constraints(inventory=["白菜"])).allowed


def test_strict_time_is_not_guessed_from_step_time(rules):
    recipe = make_recipe(["白菜"], steps="白菜腌制一晚，再煮5分钟。")
    result = rules.evaluate(recipe, Constraints(max_minutes=10))
    assert not result.allowed
    assert "总烹饪时间" in " ".join(result.reasons)


def test_health_goals_rank_but_do_not_invent_disease_bans(rules):
    goal = Constraints(health_goals=["控糖", "降压"])
    plain = rules.evaluate(make_recipe(["白菜"], categories=["vegetable"]), goal)
    sweet = rules.evaluate(make_recipe(["白菜", "白糖", "酱油"], categories=["vegetable"]), goal)
    assert plain.allowed and sweet.allowed
    assert plain.score > sweet.score
    assert any("不能判断" in warning for warning in sweet.warnings)


def test_qualitative_notes_do_not_repeat_promotional_labels():
    recipe = make_recipe(["鸡蛋", "盐"], labels=["降血压", "抗癌"], categories=["protein"])
    notes = " ".join(analyze(recipe, Constraints(health_goals=["增肌"])))
    assert "蛋白质来源" in notes
    assert "抗癌" not in notes and "降血压" not in notes
    assert "未计算蛋白质" in notes


def test_plant_milk_does_not_imply_dairy(rules):
    assert rules.evaluate(make_recipe(["椰奶"]), Constraints(allergies=["牛奶"])).allowed

@pytest.mark.parametrize("ingredient", [
    "蒸鱼豉油", "鸡腿菇", "鱼露", "鸡精", "蟹味菇", "鸡头米",
])
def test_nutrition_does_not_promote_condiments_or_lookalikes_to_protein(ingredient):
    recipe = make_recipe([ingredient, "白菜"], name="鱼香肉丝", categories=["protein"])
    notes = analyze(recipe, Constraints())
    assert not any(note.startswith("配料含蛋白质来源") for note in notes)


def test_nutrition_names_only_actual_protein_ingredients():
    recipe = make_recipe(["蒸鱼豉油", "鸡腿菇", "白菜", "鸡蛋", "豆腐"])
    protein_notes = [
        note for note in analyze(recipe, Constraints())
        if note.startswith("配料含蛋白质来源")
    ]
    assert protein_notes == ["配料含蛋白质来源：鸡蛋、豆腐；未计算蛋白质含量。"]