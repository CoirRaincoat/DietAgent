"""Public authored development workflows, not consumption or independent labels."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.meal_history import explicit_new_meal, recommendation_counts
from app.domain.models import Constraints, Intent, SessionState, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.sessions import SessionStore
from app.rules.engine import RuleEngine
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_component_slots import dish


@pytest.mark.parametrize(
    "text",
    [
        "安排明天晚餐",
        "请推荐下一餐",
        "规划新的一餐",
        "帮我安排后天午餐菜单",
        "安排另一餐，沿用原要求",
    ],
)
def test_affirmative_new_meal_boundaries(text):
    assert explicit_new_meal(text)


@pytest.mark.parametrize(
    "text",
    [
        "继续",
        "明天具体吃什么？",
        "换第二道",
        "不要安排明天晚餐",
        "他说，安排明天晚餐",
        "如果可以，安排明天晚餐",
        "例如，安排明天晚餐",
        "“安排明天晚餐”",
        "安排明天晚餐还是下一餐",
        "再来一份晚餐",
        "明天晚餐可能要换",
        "解释下一餐",
        "菜谱中写着，安排明天晚餐",
        "菜单说明如下，安排下一餐",
    ],
)
def test_non_authorizing_boundaries(text):
    assert not explicit_new_meal(text)


def pool():
    return [
        dish(n, f"{f}200克；水100毫升", f"{f}蒸熟后装盘。")
        for n, f in (
            ("蒸白菜", "白菜"),
            ("蒸菠菜", "菠菜"),
            ("蒸青菜", "青菜"),
            ("蒸鸡蛋", "鸡蛋"),
            ("蒸豆腐", "豆腐"),
            ("蒸鸡肉", "鸡胸肉"),
            ("蒸大米饭", "大米"),
            ("蒸小米饭", "小米"),
        )
    ]


def catalog():
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2
    )
    recipes = pool() + [
        dish("花生米饭", "花生20克；大米200克", "花生和大米蒸熟食用。"),
        dish("辣椒白菜", "白菜200克；辣椒10克", "白菜辣椒蒸熟食用。"),
    ]
    return DataCatalog(
        profiles={3: profile, 4: profile.model_copy(update={"user_id": 4})},
        recipes={r.recipe_id: r for r in recipes},
        quality_report={},
    )


def names(response):
    return [r["name"] for r in response["menu"]]


def first_intent():
    return complete_intent(no_spicy=True, allergies=["花生"])


def test_history_counts_are_bounded_exact_names_not_consumption():
    assert recommendation_counts([["蒸白菜", "蒸白菜"], ["蒸菠菜"]]) == {"蒸白菜": 1, "蒸菠菜": 2}
    assert recommendation_counts([["旧菜"]] * 10 + [["蒸白菜"]])["旧菜"] == 28
    assert recommendation_counts([["a b"]])["ab"] == 1
    legacy = SessionState(session_id="a" * 32, user_id=3)
    assert legacy.recent_recommendations == [] and legacy.meal_sequence == 0


def test_direct_rotation_has_gain_only_in_otherwise_equal_peers():
    planner = MenuPlanner(RuleEngine())
    c = Constraints(no_spicy=True, allergies=["花生"])
    first = planner.plan(pool(), c)
    rotated = planner.plan(pool(), c, recent_recipe_names=[[r.name for r in first.recipes]])
    assert first.failure is None and rotated.failure is None
    assert [r.name for r in first.recipes] != [r.name for r in rotated.recipes]
    assert sum(r.name in {a.name for a in first.recipes} for r in rotated.recipes) < 3
    assert (
        planner.plan(
            pool(),
            c,
            current=rotated.recipes,
            recheck_soft_preferences=False,
            recent_recipe_names=[[r.name for r in first.recipes]],
        ).recipes
        == rotated.recipes
    )


def test_api_explicit_next_meal_rotates_and_plain_continue_replays_and_restarts(tmp_path):
    data = catalog()
    llm = ScriptedLLM([first_intent(), Intent(meal_type="晚餐"), Intent()])
    with client_for(tmp_path, data, llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，不辣，花生过敏，安排3道菜"}
        ).json()
        assert first["status"] == "ok"
        sid = first["conversation_state"]["session_id"]
        request = {
            "user_id": 3,
            "session_id": sid,
            "request_id": "meal-2",
            "message": "安排明天晚餐，沿用原要求",
        }
        second = client.post("/chat", json=request).json()
        assert second["status"] == "ok" and names(second) != names(first)
        state = second["conversation_state"]
        assert state["meal_sequence"] == 1 and state["recent_recommendations"][0][
            "recipe_names"
        ] == names(first)
        assert state["constraints"]["no_spicy"] and state["constraints"]["allergies"] == ["花生"]
        assert client.post("/chat", json=request).json() == second
        assert llm.parse_calls == 2
        continued = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "继续"}
        ).json()
        assert names(continued) == names(second)
        assert (
            continued["conversation_state"]["recent_recommendations"]
            == state["recent_recommendations"]
        )
    saved = SessionStore(tmp_path / "state.db").get(sid, 3)
    assert saved.meal_sequence == 1 and saved.recent_recommendations[0].recipe_names == names(first)
    with client_for(tmp_path, data, ScriptedLLM([Intent(action="explain")])) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        explained = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "解释菜单"}
        ).json()
        assert names(explained) == names(second)
        assert (
            client.post(
                "/chat", json={"user_id": 4, "session_id": sid, "message": "继续"}
            ).status_code
            == 409
        )


def test_default_api_honors_explicit_next_meal_without_a_test_toggle(tmp_path):
    with client_for(
        tmp_path, catalog(), ScriptedLLM([first_intent(), Intent(meal_type="晚餐")])
    ) as client:
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，不辣，花生过敏"}
        ).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "安排明天晚餐，沿用原要求",
            },
        ).json()
        assert names(second) != names(first)
        assert second["conversation_state"]["recent_recommendations"][0][
            "recipe_names"
        ] == names(first)
        assert second["conversation_state"]["meal_sequence"] == 1


@pytest.mark.parametrize("action", ["explain", "replace"])
def test_model_action_cannot_convert_a_local_or_explanation_into_new_meal(tmp_path, action):
    intent = Intent(action=action, replace_slot=2 if action == "replace" else None)
    with client_for(tmp_path, catalog(), ScriptedLLM([first_intent(), intent])) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，不辣，花生过敏"}
        ).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 3,
                "session_id": first["conversation_state"]["session_id"],
                "message": "安排明天晚餐",
            },
        ).json()
        assert second["conversation_state"]["meal_sequence"] == 0
        assert second["conversation_state"]["recent_recommendations"] == []
        assert names(second)[0] == names(first)[0] and names(second)[2] == names(first)[2]


def test_failed_new_meal_is_not_archived_and_unknown_allergy_is_not_waived(tmp_path):
    llm = ScriptedLLM([first_intent(), Intent(allergies=["神秘配料"]), Intent()])
    with client_for(tmp_path, catalog(), llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，不辣，花生过敏"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        failed = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "安排下一餐，神秘配料过敏"}
        ).json()
        assert failed["status"] == "clarification_required" and failed["menu"] == []
        assert failed["conversation_state"]["pending_allergy"]
        again = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "安排下一餐，沿用原要求"}
        ).json()
        assert again["menu"] == [] and again["conversation_state"]["pending_allergy"]
        assert len(again["conversation_state"]["recent_recommendations"]) == 1
        assert again["conversation_state"]["recent_recommendations"][0]["recipe_names"] == names(
            first
        )
        assert again["conversation_state"]["constraints"]["allergies"] == ["花生"]


def test_history_window_survives_more_than_eight_new_meals_without_counting_replay(tmp_path):
    llm = ScriptedLLM([first_intent()] + [Intent(meal_type="晚餐") for _ in range(10)])
    with client_for(tmp_path, catalog(), llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post(
            "/chat", json={"user_id": 3, "message": "1人晚餐，不辣，花生过敏"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        earlier = [names(first)]
        for i in range(10):
            request = dict(
                user_id=3, session_id=sid, request_id=f"next-{i}", message="安排下一餐，沿用原要求"
            )
            result = client.post("/chat", json=request).json()
            assert result["status"] == "ok"
            assert [
                v["recipe_names"] for v in result["conversation_state"]["recent_recommendations"]
            ] == earlier[-8:]
            assert client.post("/chat", json=request).json() == result
            earlier.append(names(result))
        assert result["conversation_state"]["meal_sequence"] == 10
        assert len(result["conversation_state"]["recent_recommendations"]) == 8
        assert llm.parse_calls == 11


def test_unique_explicit_food_requirement_cannot_be_traded_for_novelty():
    records = pool()
    c = Constraints(dish_count=1, preferred_ingredients=["鸡胸肉"], health_goals=["增肌"])
    planner = MenuPlanner(RuleEngine())
    baseline = planner.plan(records, c)
    rotated = planner.plan(records, c, recent_recipe_names=[[r.name for r in baseline.recipes]])
    assert baseline.failure is None
    assert rotated.recipes == baseline.recipes
