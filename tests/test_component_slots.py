"""Fixed source and authored slot-unit contrasts; not whole-catalog accuracy."""

import csv
from functools import lru_cache
from pathlib import Path

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Recipe
from app.domain.slot_components import slot_component_evidence
from app.infrastructure.data import normalize_recipes
from app.rules.engine import RuleEngine

BAD_ROWS = (20, 338, 467, 559, 651, 790, 879, 1298, 1463, 1514, 1797)


@lru_cache(maxsize=1)
def catalog() -> dict[int, Recipe]:
    path = Path(__file__).resolve().parents[1] / "dataset/recipe_kb/recipes_sample_2000.csv"
    with path.open(encoding="gb18030", newline="") as handle:
        return {r.source_row: r for r in normalize_recipes(csv.DictReader(handle)).values()}


def dish(name: str, foods: str, steps: str) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": "午餐、晚餐",
                    }
                ]
            ).values()
        )
    )


@pytest.mark.parametrize("row", BAD_ROWS)
def test_source_component_or_multi_dish_card_is_not_one_ordinary_slot(row: int) -> None:
    recipe = catalog()[row]
    assert not is_main_meal_recipe(recipe)
    assert recipe.categories == ["component"] and not recipe.eligible


@pytest.mark.parametrize("row", BAD_ROWS)
def test_only_component_candidates_fail_instead_of_faking_a_menu(row: int) -> None:
    recipe = catalog()[row]
    result = MenuPlanner(RuleEngine()).plan(
        [recipe],
        Constraints(dish_count=1, soup_count=int(row in (467, 1514))),
        query_terms=[recipe.name],
    )
    assert result.failure and not result.recipes


@pytest.mark.parametrize("row", BAD_ROWS)
def test_stale_role_metadata_cannot_reintroduce_source_into_menu_or_suggestions(row: int) -> None:
    recipe = catalog()[row].model_copy(update={"eligible": True, "categories": ["protein"]})
    good = dish("蒸鸡肉", "鸡肉200克；盐1克", "鸡肉蒸熟装盘。")
    assert not is_main_meal_recipe(recipe)
    result = MenuPlanner(RuleEngine()).plan(
        [recipe, good],
        Constraints(dish_count=1),
        current=[recipe],
        query_terms=[recipe.name],
    )
    assert result.failure is None and result.recipes == [good]
    assert (
        replacement_candidates(
            [good],
            [recipe],
            "component-slot-fixture",
            constraints=Constraints(dish_count=1),
        )
        == []
    )


@pytest.mark.parametrize("row", [1553, 1629])
def test_marketing_set_name_does_not_ban_completed_assembled_or_one_plate_course(row: int) -> None:
    recipe = catalog()[row]
    assert is_main_meal_recipe(recipe)
    assert MenuPlanner(RuleEngine()).plan([recipe], Constraints(dish_count=1)).recipes == [recipe]


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("肉酱拌面", "面条100克；猪肉100克；盐1克", "将面条煮熟。肉末炒熟成肉酱，和面条拌匀装盘。"),
        ("烧烤酱烤鸡", "鸡肉200克；烧烤酱20克", "鸡肉涂酱烤熟装盘。"),
        ("鸡胸肉配烧烤酱", "鸡胸肉200克；烧烤酱20克", "鸡胸肉煎熟装盘，配少许烧烤酱。"),
        ("鸡肉配肉酱", "鸡肉200克；猪肉50克", "鸡肉煎熟装盘，肉末炒熟作为酱汁淋上。"),
        ("绞肉", "猪肉200克；盐1克", "猪肉绞碎后炒熟装盘食用。"),
        ("猪肉末", "猪肉200克；盐1克", "肉末蒸熟后装盘。"),
        ("虾泥", "虾200克；盐1克", "虾搅打成泥，无需蒸煮。随后炒熟装盘。"),
        ("套餐里的蒸鸡肉", "鸡肉200克；盐1克", "鸡肉蒸熟后装盘，这里只是一道鸡肉。"),
        ("不是锅底的番茄汤", "番茄200克；水500克；盐1克", "将番茄和水煮熟出锅。"),
    ],
)
def test_completed_entrees_and_component_words_used_as_parts_are_retained(
    name: str, foods: str, steps: str
) -> None:
    recipe = dish(name, foods, steps)
    assert is_main_meal_recipe(recipe)


@pytest.mark.parametrize(
    "name,foods,steps,kind",
    [
        ("牛肉酱", "牛肉200克；盐1克", "肉末炒熟熬煮成酱即可食用。", "standalone_condiment"),
        ("番茄汤底", "番茄200克；水500克", "煮熟出锅即可享用。", "standalone_condiment"),
        ("绞肉", "猪肉200克", "猪肉绞碎，烹饪结束取出即可使用。", "raw_processing"),
        ("虾泥", "虾200克", "虾搅打成泥备用，无需蒸煮。", "raw_processing"),
        (
            "二人食三菜一汤",
            "鸡肉200克；水500克",
            "将四道菜放入机器开始烹饪。",
            "unsplit_multi_course",
        ),
    ],
)
def test_component_kind_is_narrow_source_evidence(
    name: str, foods: str, steps: str, kind: str
) -> None:
    recipe = dish(name, foods, steps)
    evidence = slot_component_evidence(recipe.name, recipe.steps)
    assert evidence is not None and evidence.kind == kind
    assert evidence.basis in (recipe.name, recipe.steps)
    assert not is_main_meal_recipe(recipe)


def test_unknown_marketing_bundle_is_not_invented_as_separate_courses() -> None:
    assert slot_component_evidence("家常套餐", "食材准备后按机器开始烹饪。") is None
    assert slot_component_evidence("一人食1菜0汤", "鸡肉蒸熟装盘。") is None
    assert slot_component_evidence("一人食零菜一汤", "番茄煮汤出锅。") is None


def test_local_replacement_keeps_other_valid_slots_and_hard_gates() -> None:
    old = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。")
    keep = dish("炒白菜", "白菜200克", "白菜炒熟装盘。")
    good = dish("蒸鱼", "鱼肉200克", "鱼肉蒸熟装盘。")
    spicy = dish("辣炒鸡肉", "鸡肉200克；小米椒10克", "鸡肉炒熟装盘。")
    allergen = dish("炒虾", "虾200克", "虾炒熟装盘。")
    bad = catalog()[651].model_copy(update={"eligible": True, "categories": ["protein"]})
    result = MenuPlanner(RuleEngine()).plan(
        [old, keep, good, spicy, allergen, bad],
        Constraints(dish_count=2, no_spicy=True, allergies=["虾"]),
        current=[old, keep],
        replace_slot=1,
    )
    assert result.failure is None and result.recipes == [good, keep]
    assert all(change["slot"] == 1 for change in result.changes)


def test_local_edit_with_out_of_scope_component_requires_whole_menu_permission() -> None:
    old = dish("蒸鸡肉", "鸡肉200克", "鸡肉蒸熟装盘。")
    good = dish("蒸鱼", "鱼肉200克", "鱼肉蒸熟装盘。")
    other = dish("炒白菜", "白菜200克", "白菜炒熟装盘。")
    bad = catalog()[651].model_copy(update={"eligible": True, "categories": ["protein"]})
    result = MenuPlanner(RuleEngine()).plan(
        [old, good, other, bad],
        Constraints(dish_count=2),
        current=[old, bad],
        replace_slot=1,
    )
    assert result.failure and not result.recipes
    assert "其他菜位" in result.failure and "确认" in result.failure
