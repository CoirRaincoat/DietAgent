"""Authored finite vocabulary contrasts, not portion or nutrition gold."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.dish_composition import dish_kind
from app.domain.entree_preferences import entree_reference_mask, meat_entree_reference
from app.domain.models import Constraints, Recipe
from app.domain.protein_food_references import named_protein_foods
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine


def dish(name: str, foods: str, steps: str = "食材蒸熟装盘。") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "晚餐"}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods,key",
    [
        ("清蒸黄鱼", "黄鱼200克；姜5克", "鱼"),
        ("清蒸黄花鱼", "黄花鱼200克", "鱼"),
        ("清蒸鲫鱼", "鲫鱼200克", "鱼"),
        ("清蒸龙利鱼", "龙利鱼200克", "鱼"),
        ("清蒸东星斑", "东星斑200克", "鱼"),
        ("蒸牛腱肉", "牛腱肉200克", "牛肉"),
        ("蒸肥牛", "肥牛200克", "牛肉"),
        ("蒸乌鸡", "乌鸡200克", "鸡肉"),
        ("蒸去骨鸭胸", "去骨鸭胸200克", "鸭肉"),
        ("蒸河虾", "河虾200克", "虾"),
    ],
)
def test_same_whole_declared_body_is_available_to_role_preference_and_mixed_reference(
    name: str,
    foods: str,
    key: str,
) -> None:
    source = dish(name, foods)
    assert source.categories == ["protein"]
    assert key in named_protein_foods(source)
    assert meat_entree_reference(source)
    assert entree_reference_mask(source) == 1
    assert dish_kind(source, Constraints(meat_dish_scope="independent_entree")) == "meat"


@pytest.mark.parametrize(
    "name,foods,allergy",
    [
        ("清蒸黄鱼", "黄鱼200克", "鱼"),
        ("清蒸东星斑", "东星斑200克", "鱼"),
        ("蒸肥牛", "肥牛200克", "牛肉"),
        ("蒸河虾", "河虾200克", "虾"),
    ],
)
def test_new_positive_reference_never_bypasses_diet_allergy_or_spice(
    name: str,
    foods: str,
    allergy: str,
) -> None:
    source = dish(name, foods)
    rules = RuleEngine()
    assert not rules.evaluate(source, Constraints(allergies=[allergy])).allowed
    assert not rules.evaluate(source, Constraints(diet_mode="ovo_lacto_vegetarian")).allowed
    assert not rules.evaluate(source, Constraints(diet_mode="vegan")).allowed
    spicy = dish(name, foods + "；辣椒5克")
    assert not rules.evaluate(spicy, Constraints(no_spicy=True)).allowed


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("肥牛饭", "肥牛100克；大米200克", "煮熟装盘食用。"),
        ("肥牛汤", "肥牛100克；水500克", "煮汤连汤盛出。"),
    ],
)
def test_forged_cached_role_never_promotes_source_staple_or_soup(
    name: str,
    foods: str,
    steps: str,
) -> None:
    source = dish(name, foods, steps)
    forged = source.model_copy(update={"categories": ["protein"]})
    assert not meat_entree_reference(forged)
    assert not named_protein_foods(forged)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("黄鱼豆腐汤", "黄鱼200克；豆腐100克；水500克", "煮汤连汤盛出。"),
        ("肥牛米饭", "肥牛100克；大米200克", "米饭煮熟后和肥牛同蒸熟食用。"),
        ("黄鱼饭", "黄鱼100克；大米200克", "米饭煮熟后和黄鱼同蒸熟食用。"),
        ("黄鱼蒸白菜", "白菜200克；黄鱼味复合酱5克", "白菜和酱蒸熟食用。"),
        ("肥牛蒸白菜", "白菜200克；肥牛汁复合调料5克", "白菜和调料蒸熟食用。"),
        ("牛肝菌", "牛肝菌200克", "蒸熟装盘。"),
        ("鱼腥草", "鱼腥草200克", "蒸熟装盘。"),
        ("鸭梨", "鸭梨200克", "蒸熟装盘。"),
        ("蒸黄鱼", "白菜200克", "蒸熟装盘。"),
        ("虾皮蒸蛋", "鸡蛋2个；虾皮5克", "蒸熟装盘。"),
    ],
)
def test_soup_staple_mismatch_compound_garnish_and_homonyms_are_not_meat_body(
    name: str,
    foods: str,
    steps: str,
) -> None:
    source = dish(name, foods, steps)
    assert not meat_entree_reference(source)
    assert not {"牛肉", "鱼", "虾", "鸡肉", "鸭肉"}.intersection(named_protein_foods(source))


def test_mixed_table_preservation_no_longer_blocks_real_yellow_fish_for_egg_and_fish() -> None:
    vegetables = [dish("蒸花菜", "花菜200克"), dish("蒸白菜", "白菜200克")]
    chicken = dish("鸡肉肠", "鸡肉200克；鸡蛋1个")
    egg = dish("蒸鸡蛋", "鸡蛋2个；水100克")
    rice = dish("米饭", "大米200克；水300克", "煮熟食用。")
    soup = dish("黄鱼豆腐汤", "黄鱼100克；豆腐100克；水500克", "煮汤连汤盛出。")
    fish = dish("清蒸黄鱼", "黄鱼200克")
    original = [*vegetables, chicken, egg, rice, soup]
    constraints = Constraints(
        people=5, dish_count=6, soup_count=1, preferred_ingredients=["鸡蛋", "鱼"], no_spicy=True
    )
    result = MenuPlanner(RuleEngine()).plan([*original, fish], constraints, current=original)
    assert not result.failure
    assert result.recipes == [*vegetables, fish, egg, rice, soup]
    assert len(result.changes) == 1
    assert not any("蛋白菜名称参考：" in warning for warning in result.warnings)


@pytest.mark.parametrize(
    "name,foods",
    [
        ("榄菜肉碎蒸菜心", "菜心200克；五花肉30克；橄榄菜5克"),
        ("红油鸡丝蒸嫩豆腐", "嫩豆腐200克；鸡脯肉30克"),
        ("肉末蒸茄子", "茄子200克；猪肉30克"),
    ],
)
def test_complete_vegetable_body_with_mince_flavor_keeps_animal_presence_not_meat_main(
    name: str,
    foods: str,
) -> None:
    source = dish(name, foods)
    assert not meat_entree_reference(source)
    assert not entree_reference_mask(source) & 1
    assert dish_kind(source) == "meat"
    assert dish_kind(source, Constraints(meat_dish_scope="independent_entree")) == "other"
    assert not RuleEngine().evaluate(source, Constraints(diet_mode="ovo_lacto_vegetarian")).allowed
