"""Observed shared-dinner repair and hard/request/edit boundaries; no real LLM."""

from fastapi.testclient import TestClient

from app.agent.meal_structure import repair_shared_soup_entree
from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.domain.models import Constraints, Intent, Recipe
from app.domain.protein_food_references import named_protein_foods
from app.infrastructure.settings import Settings
from app.infrastructure.synthetic import load_synthetic_catalog
from app.rules.engine import RuleEngine
from tests.test_meal_structure import StructureLLM, dish, pool
from tests.test_shared_soup_staple import family


def menu() -> list[Recipe]:
    choices = pool()
    return [
        choices[0],
        choices[1],
        choices[4],
        dish("牛奶蒸蛋", "鸡蛋2个；牛奶200克", "将蛋液混匀，蒸熟成蛋羹后装盘。"),
        choices[7],
        choices[8],
    ]


def repair(current, choices, constraints=None, **options):
    return repair_shared_soup_entree(
        current,
        choices,
        constraints or family(health_goals=[]),
        scores={},
        order={},
        food_matches=lambda r, term: bool(RuleEngine().food_matches(r, term)),
        **options,
    )


def test_source_menu_final_repair_changes_only_unrequested_custard() -> None:
    records = list(load_synthetic_catalog().recipes.values())
    source = [r.model_dump() for r in records]
    names = [
        "姜汁菠菜",
        "蒸秋季时蔬",
        "鸡肉玉米肠",
        "无糖牛奶蒸蛋",
        "基础煮燕麦饭",
        "西红柿豆腐羹",
    ]
    old = [next(r for r in records if r.name == name) for name in names]
    rules = RuleEngine()
    safe = [r for r in records if rules.evaluate(r, family()).allowed]
    plan = repair_shared_soup_entree(
        old,
        safe,
        family(),
        scores={r.recipe_id: rules.evaluate(r, family()).score for r in safe},
        order={},
        food_matches=lambda r, term: bool(rules.food_matches(r, term)),
    )
    assert [i for i, (a, b) in enumerate(zip(old, plan.recipes)) if a != b] == [3]
    assert not any(term in plan.recipes[3].name for term in ("蒸蛋", "蒸水蛋", "蛋羹", "温泉蛋"))
    assert named_protein_foods(plan.recipes[3])
    assert plan.recipes[3].model_dump() in source
    assert all(RuleEngine().evaluate(r, family()).allowed for r in plan.recipes)
    assert (
        len(plan.recipes) == 6
        and sum("soup" in r.categories for r in plan.recipes) == 1
    )
    assert source == [r.model_dump() for r in records]


def test_explicit_custard_or_milk_is_not_silently_removed() -> None:
    current = menu()
    new = dish("煎牛肉", "牛肉200克", "牛肉煎熟后装盘。")
    for context, query in [
        (family(preferences=["要蒸蛋"]), ()),
        (family(preferred_ingredients=["牛奶"]), ()),
        (family(), ("蛋羹",)),
    ]:
        assert repair(current, [new], context, query_terms=query).recipes == current


def test_readonly_local_permissions_and_other_meals_are_protected() -> None:
    current = menu()
    new = dish("煎牛肉", "牛肉200克", "牛肉煎熟后装盘。")
    for context, options in [
        (family(), {"allow_repair": False}),
        (family(), {"replace_slot": 1}),
        (family(people=1), {}),
        (family(meal_type="早餐"), {}),
        (family(soup_count=0), {}),
        (family(dish_count=5), {}),
    ]:
        assert repair(current, [new], context, **options).recipes == current
    local = repair(current, [new], replace_slot=4)
    assert local.changed_indices == {3} and local.recipes[3] == new
    assert all(local.recipes[i] == current[i] for i in (0, 1, 2, 4, 5))


def test_formed_body_is_not_invented_from_title_or_negated_preparation() -> None:
    current = menu()
    title_only = dish("蒸肉饼", "肉末200克", "食材备好，选择开始烹饪，结束享用。")
    negated = dish("蒸肉饼", "肉末200克", "肉沫不用压实。选择开始烹饪。")
    puree = dish("牛肉泥", "牛肉200克", "将牛肉煮熟，打成泥。")
    for choice in (title_only, negated, puree):
        assert repair(current, [choice]).recipes == current


def test_existing_final_rules_and_explicit_meat_count_are_preserved() -> None:
    records = list(load_synthetic_catalog().recipes.values())
    for context in [
        family(allergies=["鸡蛋"]),
        family(allergies=["牛奶"]),
        family(diet_mode="vegan"),
        family(diet_mode="ovo_lacto_vegetarian"),
        family(meat_dish_count=1, vegetarian_dish_count=3),
    ]:
        plan = MenuPlanner(RuleEngine()).plan(records, context)
        assert plan.failure is None
        assert all(RuleEngine().evaluate(r, context).allowed for r in plan.recipes)
        assert (
            len(plan.recipes) == 6
            and sum("soup" in r.categories for r in plan.recipes) == 1
        )


def test_native_and_compat_default_remove_unrequested_soft_egg_preserve_retry_and_owner(
    tmp_path,
) -> None:
    class MinimalExplanation(StructureLLM):
        async def explain(self, facts):
            return ["opening"]

    intent = Intent(
        people=5,
        meal_type="晚餐",
        dish_count=6,
        soup_count=1,
        no_spicy=True,
        preferences=["清淡"],
        health_goals=["降压", "护心"],
        restrictions_confirmed=True,
    )
    llm = MinimalExplanation([intent, Intent(), intent])
    settings = Settings(
        _env_file=None,
        local_profile_path=None,
        deepseek_api_key="",
        session_db=tmp_path / "api.sqlite3",
    )
    with TestClient(create_app(settings=settings, llm=llm)) as client:
        request = {
            "user_id": 900003,
            "message": "5人晚餐，6道菜含1汤，清淡不辣，没有其他忌口",
            "request_id": "solid-main",
        }
        first = client.post("/chat", json=request)
        assert first.status_code == 200 and first.json()["status"] == "ok"
        value = first.json()
        assert not any(term in r["name"] for r in value["menu"] for term in ("蒸蛋", "蒸水蛋", "蛋羹", "温泉蛋"))
        assert len(value["menu"]) == 6
        agent = client.app.state.agent
        records = [agent.catalog.recipes[r["recipe_id"]] for r in value["menu"]]
        assert sum("soup" in r.categories for r in records) == 1
        context = Constraints.model_validate(value["conversation_state"]["constraints"])
        assert all(agent.rules.evaluate(r, context).allowed for r in records)
        sid = value["conversation_state"]["session_id"]
        assert client.post("/chat", json={**request, "session_id": sid}).json() == value
        again = client.post(
            "/chat", json={"user_id": 900003, "session_id": sid, "message": "继续"}
        ).json()
        assert [r["recipe_id"] for r in again["menu"]] == [
            r["recipe_id"] for r in value["menu"]
        ]
        assert again["conversation_state"]["constraints"] == value["conversation_state"]["constraints"]
        assert (
            client.post(
                "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
            ).status_code
            == 409
        )
        compat = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900003",
                "messages": [{"role": "user", "content": request["message"]}],
            },
        )
        assert compat.status_code == 200
        text = compat.json()["choices"][0]["message"]["content"]
        assert all(r["name"] in text for r in value["menu"])
        assert "不能判定低钠" in text
