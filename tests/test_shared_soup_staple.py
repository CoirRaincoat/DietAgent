"""Actual default menu regression; culinary improvement is not nutrition proof."""

from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.domain.models import Constraints, Intent, Recipe
from app.infrastructure.settings import Settings
from app.infrastructure.synthetic import load_synthetic_catalog
from app.rules.engine import RuleEngine
from tests.test_breakfast_actual_menu import ids
from tests.test_meal_structure import StructureLLM, dish, pool


def family(**updates: object) -> Constraints:
    values: dict[str, object] = dict(
        people=5,
        meal_type="晚餐",
        dish_count=6,
        soup_count=1,
        no_spicy=True,
        preferences=["清淡"],
        health_goals=["降压", "护心"],
    )
    values.update(updates)
    return Constraints.model_validate(values)


def test_actual_family_menu_replaces_extra_porridge_with_finished_rice() -> None:
    records = list(load_synthetic_catalog().recipes.values())
    original = [r.model_dump() for r in records]
    names = [
        "姜汁菠菜",
        "蒸秋季时蔬",
        "鸡肉玉米肠",
        "无糖牛奶蒸蛋",
        "藜麦南瓜小米粥",
        "西红柿豆腐羹",
    ]
    observed = [next(r for r in records if r.name == name) for name in names]
    result = MenuPlanner(RuleEngine()).plan(records, family(), current=observed)
    assert result.failure is None
    assert len(result.recipes) == 6
    assert sum("soup" in r.categories for r in result.recipes) == 1
    assert not any(r.name.endswith("粥") for r in result.recipes)
    rice = next(r for r in result.recipes if "staple" in r.categories)
    assert rice.name == "基础煮燕麦饭"
    assert "燕麦50克" in rice.raw_ingredients and "蒸好后取出" in rice.steps
    assert all(RuleEngine().evaluate(r, family()).allowed for r in result.recipes)
    assert [r.model_dump() for r in records] == original


def test_native_default_loader_uses_repair_and_protects_retry_and_user(
    tmp_path,
) -> None:
    llm = StructureLLM(
        [
            Intent(
                people=5,
                meal_type="晚餐",
                dish_count=6,
                soup_count=1,
                no_spicy=True,
                preferences=["清淡"],
                health_goals=["降压", "护心"],
                restrictions_confirmed=True,
            ),
            Intent(),
        ]
    )
    app = create_app(
        settings=Settings.model_construct(session_db=tmp_path / "api.sqlite3"), llm=llm
    )
    with TestClient(app) as client:
        request = dict(
            user_id=900003,
            message="5人晚餐，安排6道菜，其中1道汤，不辣，清淡，有降压和护心目标，没有其他忌口。",
            request_id="shared-rice",
        )
        response = client.post("/chat", json=request)
        assert response.status_code == 200
        value = response.json()
        assert value["status"] == "ok"
        assert len(value["menu"]) == 6
        assert "基础煮燕麦饭" in [r["name"] for r in value["menu"]]
        sid = value["conversation_state"]["session_id"]
        assert client.post("/chat", json={**request, "session_id": sid}).json() == value
        again = client.post(
            "/chat", json=dict(user_id=900003, session_id=sid, message="继续")
        )
        assert [r["recipe_id"] for r in again.json()["menu"]] == [
            r["recipe_id"] for r in value["menu"]
        ]
        assert client.post(
            "/chat", json=dict(user_id=900001, session_id=sid, message="继续")
        ).status_code in {404, 409}


def current() -> list[Recipe]:
    choices = pool()
    return [
        choices[0],
        choices[1],
        choices[4],
        choices[5],
        dish("小米粥", "小米200克；水1000克", "小米加水煮熟成粥。"),
        choices[8],
    ]


def test_readonly_family_does_not_change_existing_porridge() -> None:
    menu = current()
    result = MenuPlanner(RuleEngine()).plan(
        [*menu, pool()[7]],
        family(health_goals=[]),
        current=menu,
        recheck_soft_preferences=False,
    )
    assert ids(result.recipes) == ids(menu)


def test_local_other_slot_cannot_change_porridge() -> None:
    menu = current()
    result = MenuPlanner(RuleEngine()).plan(
        [*menu, *pool()],
        family(health_goals=[]),
        current=menu,
        replace_slot=1,
    )
    assert result.failure is None
    assert result.recipes[4] == menu[4]
    assert all(change["slot"] == 1 for change in result.changes)


def test_named_porridge_and_preferred_millet_are_protected() -> None:
    menu = current()
    planner = MenuPlanner(RuleEngine())
    for updates, query in [({}, ["粥"]), ({"preferred_ingredients": ["小米"]}, [])]:
        result = planner.plan(
            [*menu, pool()[7]],
            family(health_goals=[], **updates),
            current=menu,
            query_terms=query,
        )
        assert result.failure is None
        assert result.recipes[4] == menu[4]


def test_vegan_and_allergy_do_not_allow_unsafe_rice() -> None:
    menu = current()
    choices = [
        *menu,
        *pool(),
        dish("芝麻米饭", "大米200克；芝麻10克"),
        dish("鸡肉米饭", "大米200克；鸡肉100克"),
    ]
    constraints = family(health_goals=[], diet_mode="vegan", allergies=["芝麻"])
    result = MenuPlanner(RuleEngine()).plan(choices, constraints)
    assert result.failure is None
    assert all(RuleEngine().evaluate(r, constraints).allowed for r in result.recipes)
    assert any(r.name == "煮米饭" for r in result.recipes)


def test_solo_or_breakfast_or_no_soup_keeps_porridge() -> None:
    for updates in ({"people": 1}, {"meal_type": "早餐"}, {"soup_count": 0}):
        menu = current()
        if updates.get("soup_count") == 0:
            menu[-1] = pool()[2]
        result = MenuPlanner(RuleEngine()).plan(
            [*menu, pool()[7]],
            family(health_goals=[], **updates),
            current=menu,
        )
        assert result.failure is None
        assert result.recipes[4] == menu[4]
