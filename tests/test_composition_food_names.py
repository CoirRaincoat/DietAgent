"""Complete ingredient names and independently read original-source menu cases."""

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.domain.dish_composition import non_meat_source_kind
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def dish(food: str, steps: str = "炒熟装盘。") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "家常配菜",
                        "食材清单": f"青菜200克；{food}10克；盐1克",
                        "烹饪步骤": steps,
                        "label": "午餐、晚餐",
                    }
                ]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "food",
    [
        "白糖",
        "白砂糖",
        "红糖",
        "金针菇",
        "葱花",
        "大葱白",
        "黄豆芽",
        "绿豆芽",
        "娃娃菜",
        "西芹",
        "鲜百合",
        "新鲜香菇",
        "切碎金针菇",
        "金针菇末",
        "桂圆干",
    ],
)
def test_complete_no_meat_names_not_destructively_split(food: str) -> None:
    recipe = dish(food)
    snapshot = recipe.model_dump(mode="json")
    assert non_meat_source_kind(recipe) == "vegetarian"
    assert recipe.model_dump(mode="json") == snapshot


@pytest.mark.parametrize(
    "food,expected",
    [
        ("素香肠", "other"),
        ("植物鸡肉香肠", "other"),
        ("香菇复合酱", "other"),
        ("鸡粉", "other"),
        ("不明调味粉", "other"),
        ("鸡腿菇猪油酱", "meat"),
        ("香菇神秘酱", "other"),
        ("海鲜酱油", "other"),
        ("白糖不明配方", "other"),
        ("红糖不明配方", "other"),
        ("红糖复合酱", "other"),
        ("红糖鸡粉", "other"),
        ("红糖猪油", "meat"),
        ("神秘金针菇", "other"),
        ("广式香肠", "meat"),
        ("花甲", "meat"),
    ],
)
def test_whole_name_and_animal_evidence_boundaries(food: str, expected: str) -> None:
    assert non_meat_source_kind(dish(food)) == expected


@pytest.fixture(scope="module")
def original_recipes() -> dict[int, Recipe]:
    root = Path(__file__).resolve().parents[1]
    with (root / "dataset/recipe_kb/recipes_sample_2000.csv").open(
        encoding="gb18030", newline=""
    ) as stream:
        return {r.source_row: r for r in normalize_recipes(csv.DictReader(stream)).values()}


# Expected facts are an independent reading of the original ingredient/step text,
# not calls to the production classification, category, or source-label heuristics.
@pytest.mark.parametrize(
    "row,name,kind",
    [
        (107, "娃娃菜炒香菇", "vegetarian"),
        (162, "卤香菇", "vegetarian"),
        (166, "葱油金针菇", "vegetarian"),
        (303, "西芹炒百合", "vegetarian"),
        (160, "广式香肠炒莴笋", "meat"),
        (55, "锡纸盒花甲", "meat"),
        (320, "奶汁烤大白菜", "meat"),
        (989, "蒜蓉蒸娃娃菜", "other"),
    ],
)
def test_independently_read_original_sources(
    original_recipes: dict[int, Recipe], row: int, name: str, kind: str
) -> None:
    recipe = original_recipes[row]
    assert recipe.name == name
    assert non_meat_source_kind(recipe) == kind


@pytest.mark.parametrize(
    "meat,vegetarian,rows",
    [
        (0, 3, (107, 162, 303, 166, 55, 989)),
        (2, 2, (107, 162, 303, 160, 320, 166, 55, 989)),
    ],
)
def test_original_recognition_is_consumed_by_explicit_menu_quotas(
    original_recipes: dict[int, Recipe], meat: int, vegetarian: int, rows: tuple[int, ...]
) -> None:
    result = MenuPlanner(RuleEngine()).plan(
        [original_recipes[row] for row in rows],
        Constraints(
            dish_count=meat + vegetarian,
            meat_dish_count=meat,
            vegetarian_dish_count=vegetarian,
            no_spicy=True,
        ),
    )
    assert result.failure is None
    # Independent raw-source membership oracle. In particular 奶汁白菜 contains pork.
    assert sum(r.source_row in {160, 320} for r in result.recipes) == meat
    assert sum(r.source_row in {107, 162, 303} for r in result.recipes) == vegetarian
    assert not {166, 55, 989} & {r.source_row for r in result.recipes}


def test_whole_meal_diet_uses_names_without_weakening_spicy_or_unknown_rules(
    original_recipes: dict[int, Recipe],
) -> None:
    rules = RuleEngine()
    constraints = Constraints(diet_mode="vegan", no_spicy=True)
    for row in (107, 162, 303):
        assert rules.evaluate(original_recipes[row], constraints).allowed
    for row in (166, 55, 160, 320, 989):
        assert not rules.evaluate(original_recipes[row], constraints).allowed
    assert not rules.evaluate(
        dish("花生油"), constraints.model_copy(update={"allergies": ["花生"]})
    ).allowed


def test_unverified_names_never_fill_vegetarian_quota() -> None:
    result = MenuPlanner(RuleEngine()).plan(
        [dish(food) for food in ("素香肠", "植物鸡肉香肠", "鸡粉", "不明调味粉")],
        Constraints(dish_count=1, meat_dish_count=0, vegetarian_dish_count=1),
    )
    assert result.failure is not None
    assert result.recipes == []


def test_original_brown_sugar_vegetables_pass_without_inventing_a_tofu_entree(
    original_recipes: dict[int, Recipe],
) -> None:
    from app.domain.protein_food_references import protein_food_mask

    recipe = original_recipes[1810]
    snapshot = recipe.model_dump(mode="json")
    assert recipe.name == "蒸秋季时蔬"
    assert "豆腐60克" in recipe.raw_ingredients and "红糖2克" in recipe.raw_ingredients
    assert non_meat_source_kind(recipe) == "vegetarian"
    rules = RuleEngine()
    c = Constraints(diet_mode="vegan", no_spicy=True, soup_count=0)
    assert rules.evaluate(recipe, c).allowed
    assert rules.food_matches(recipe, "豆腐")
    assert protein_food_mask(recipe, ("豆腐",)) == 0
    assert not rules.evaluate(recipe, c.model_copy(update={"allergies": ["大豆"]})).allowed
    assert recipe.model_dump(mode="json") == snapshot


def test_brown_sugar_does_not_hide_step_animal_or_unknown_additions() -> None:
    c = Constraints(diet_mode="vegan", no_spicy=True)
    for steps in ("炒熟后加猪油装盘。", "炒熟后加鸡蛋液装盘。", "炒熟后加未知蘸料。", "炒熟后加辣椒油。"):
        assert not RuleEngine().evaluate(dish("红糖", steps), c).allowed


class SourceMenuLLM(BaseLLM):
    """Scripted extraction only; no model understanding or latency evidence."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        return Intent(
            action="plan",
            people=3,
            meal_type="晚餐",
            dish_count=3,
            soup_count=0,
            no_spicy=True,
            restrictions_confirmed=True,
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def source_app(tmp_path: Path, originals: dict[int, Recipe]) -> Any:
    recipes = [originals[row] for row in (107, 162, 303, 166, 55, 160, 320, 989)]
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    settings = Settings.model_construct(session_db=tmp_path / "source-menu.db")
    return create_app(
        settings,
        SourceMenuLLM(),
        DataCatalog({900001: profile}, {r.recipe_id: r for r in recipes}, {}),
        SessionStore(settings.database_path),
    )


def test_source_menu_http_and_restart_do_not_merely_store_tags(
    tmp_path: Path,
    original_recipes: dict[int, Recipe],
) -> None:
    with TestClient(source_app(tmp_path, original_recipes)) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "3人晚餐共3道，0荤3素，不辣，无其他忌口。"}
        ).json()
    assert first["status"] == "ok"
    expected = {original_recipes[row].recipe_id for row in (107, 162, 303)}
    assert {r["recipe_id"] for r in first["menu"]} == expected
    with TestClient(source_app(tmp_path, original_recipes)) as client:
        retry = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "继续",
                "session_id": first["conversation_state"]["session_id"],
            },
        ).json()
    assert retry["status"] == "ok"
    assert [r["recipe_id"] for r in retry["menu"]] == [r["recipe_id"] for r in first["menu"]]


def test_source_menu_stream_renders_only_independently_verified_no_meat_cases(
    tmp_path: Path,
    original_recipes: dict[int, Recipe],
) -> None:
    with TestClient(source_app(tmp_path, original_recipes)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "3人晚餐共3道，0荤3素，不辣，无其他忌口。"}
                ],
            },
        )
    assert response.status_code == 200
    events = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1] == "[DONE]"
    text = "".join(
        json.loads(event)["choices"][0]["delta"].get("content", "") for event in events[:-1]
    )
    for row in (107, 162, 303):
        assert original_recipes[row].name in text
    for row in (166, 55, 160, 320, 989):
        assert original_recipes[row].name not in text
    assert "荤菜 0 道、素菜 3 道" in text
