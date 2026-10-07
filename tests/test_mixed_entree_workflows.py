"""Actual planner/API behaviour for mixed requests, not nutrition or quality grades."""

import pytest

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.domain.models import Constraints, Intent, UserProfile
from app.infrastructure.data import DataCatalog
from app.rules.engine import RuleEngine
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_component_slots import dish


def public_pool():
    return [
        dish("蒸白菜", "白菜200克；水100毫升", "白菜蒸熟后装盘。"),
        dish("炒胡萝卜", "胡萝卜200克；食用油5克", "胡萝卜炒熟后装盘。"),
        dish("蒸鸡蛋", "鸡蛋150克；水100毫升", "鸡蛋蒸熟后装盘。"),
        dish("温泉蛋", "鸡蛋150克；水100毫升", "鸡蛋煮熟后食用。"),
        dish("枸杞蒸鸡蛋", "鸡蛋150克；枸杞5克；水100毫升", "鸡蛋枸杞蒸熟后装盘。"),
        dish("蒸鸡肉", "鸡胸肉200克；盐1克", "鸡胸肉蒸熟后装盘。"),
        dish("蒸猪肉", "猪肉200克；盐1克", "猪肉蒸熟后装盘。"),
        dish("蒸米饭", "大米200克；水300毫升", "大米加水蒸熟后食用。"),
        dish("冬瓜汤", "冬瓜200克；水600毫升", "冬瓜加水煮汤后食用。"),
        dish("辣椒鸡肉", "鸡肉200克；辣椒5克", "鸡肉辣椒蒸熟后装盘。"),
        dish("清蒸虾", "虾200克", "虾蒸熟后装盘。"),
    ]


def context(**extra):
    return Constraints(
        people=5,
        dish_count=6,
        soup_count=1,
        meal_type="晚餐",
        no_spicy=True,
        allergies=["虾"],
        health_goals=["降压", "护心"],
        **extra,
    )


def names(menu):
    return [r.name for r in menu]


def test_shared_meal_does_not_fill_both_protein_slots_with_eggs_when_safe_meat_exists():
    result = MenuPlanner(RuleEngine()).plan(public_pool(), context())
    assert result.failure is None
    assert {"蒸鸡肉", "蒸猪肉"}.intersection(names(result.recipes))
    assert len(result.recipes) == 6 and names(result.recipes).count("冬瓜汤") == 1
    assert "辣椒鸡肉" not in names(result.recipes) and "清蒸虾" not in names(result.recipes)


def test_new_joint_request_repairs_retained_all_egg_menu_without_losing_soups_or_methods():
    pool = public_pool()
    current = [pool[i] for i in (0, 1, 2, 3, 7, 8)]
    result = MenuPlanner(RuleEngine()).plan(
        pool, context(preferences=["注意荤素和做法多样"]), current=current
    )
    assert result.failure is None and {"蒸鸡肉", "蒸猪肉"}.intersection(names(result.recipes))
    assert [result.recipes[i] for i in (0, 1, 4, 5)] == [current[i] for i in (0, 1, 4, 5)]
    assert any("荤素结构" in warning for warning in result.warnings)


def test_incidental_vegetable_credit_cannot_remove_last_or_redundant_meat_reference():
    pool = public_pool()
    planner = MenuPlanner(RuleEngine())
    one = [pool[i] for i in (0, 1, 5, 2, 7, 8)]
    result = planner.plan(pool, context(), current=one)
    assert "蒸鸡肉" in names(result.recipes)
    two = [pool[i] for i in (0, 1, 5, 6, 7, 8)]
    improved = planner.plan(pool, context(), current=two)
    assert {"蒸鸡肉", "蒸猪肉"} <= set(names(improved.recipes))
    assert improved.recipes == two


def test_plain_continue_does_not_retroactively_churn_all_egg_menu():
    pool = public_pool()
    current = [pool[i] for i in (0, 1, 2, 3, 7, 8)]
    result = MenuPlanner(RuleEngine()).plan(
        pool, context(), current=current, recheck_soft_preferences=False
    )
    assert result.recipes == current
    assert any("尚缺" in warning and "肉鱼" in warning for warning in result.warnings)


def test_local_rice_edit_never_repairs_unrelated_egg_slots():
    pool = public_pool()
    alternate = dish("蒸小米饭", "小米200克；水300毫升", "小米加水蒸熟后食用。")
    pool.append(alternate)
    current = [pool[i] for i in (0, 1, 2, 3, 7, 8)]
    result = MenuPlanner(RuleEngine()).plan(
        pool, context(preferences=["荤素搭配"]), current=current, replace_slot=5
    )
    assert result.failure is None
    assert result.recipes[4] == alternate
    assert all(result.recipes[i] == current[i] for i in (0, 1, 2, 3, 5))
    assert any("尚缺" in warning and "肉鱼" in warning for warning in result.warnings)


def test_missing_safe_meat_is_disclosed_not_backfilled_with_spicy_or_allergic_dishes():
    pool = [r for r in public_pool() if r.name not in {"蒸鸡肉", "蒸猪肉"}]
    result = MenuPlanner(RuleEngine()).plan(pool, context())
    assert result.failure is None
    assert "辣椒鸡肉" not in names(result.recipes) and "清蒸虾" not in names(result.recipes)
    assert any("尚缺" in warning and "肉鱼" in warning for warning in result.warnings)


def test_optional_suggestions_never_remove_only_meat_reference():
    pool = public_pool()
    current = [pool[i] for i in (5, 0, 1, 2, 7, 8)]
    rules = RuleEngine()
    safe = [r for r in pool if rules.evaluate(r, context()).allowed]
    suggestions = replacement_candidates(
        current, safe, "mixed-suggestions", constraints=context(), rules=rules
    )
    assert suggestions
    assert all(r.name == "蒸猪肉" for r in suggestions)


def test_explicit_new_method_does_not_erase_mixed_structure_and_can_use_same_meat_body():
    pool = public_pool()
    current = [pool[i] for i in (0, 1, 5, 2, 7, 8)]
    egg = dish("烤鸡蛋", "鸡蛋150克", "鸡蛋烤熟后装盘。")
    c = context(preferences=["做法：烤"])
    only_egg = MenuPlanner(RuleEngine()).plan([*pool, egg], c, current=current)
    assert "蒸鸡肉" in names(only_egg.recipes)
    # It may use another egg slot, but cannot remove the only meat dish.
    meat = dish("烤鸡肉", "鸡胸肉200克；盐1克", "鸡胸肉烤熟后装盘。")
    both = MenuPlanner(RuleEngine()).plan([*pool, egg, meat], c, current=current)
    assert {"蒸鸡肉", "烤鸡肉"}.intersection(names(both.recipes))
    assert {"烤鸡蛋", "烤鸡肉"}.intersection(names(both.recipes))


def test_whole_meal_vegetarian_and_explicit_zero_meat_override_shared_default():
    pool = public_pool()
    for fields in ({"diet_mode": "ovo_lacto_vegetarian"}, {"meat_dish_count": 0}):
        result = MenuPlanner(RuleEngine()).plan(pool, context(preferences=["荤素搭配"], **fields))
        assert result.failure is None
        assert not {"蒸鸡肉", "蒸猪肉", "清蒸虾", "辣椒鸡肉"}.intersection(names(result.recipes))
        assert any("不擅自加肉" in warning for warning in result.warnings)


def test_api_grounds_joint_request_when_adapter_omits_preferences_and_replays_after_restart(
    tmp_path,
):
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2
    )
    catalog = DataCatalog(
        profiles={3: profile, 4: profile.model_copy(update={"user_id": 4})},
        recipes={r.recipe_id: r for r in public_pool()},
        quality_report={},
    )

    class OpeningOnlyLLM(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    llm = OpeningOnlyLLM(
        [
            complete_intent(
                people=5,
                dish_count=6,
                soup_count=1,
                no_spicy=True,
                allergies=["虾"],
                health_goals=["降压", "护心"],
            )
        ]
    )
    payload = {
        "user_id": 3,
        "request_id": "mixed-1",
        "message": "5个人吃晚餐，总共6道菜，其中1道汤，不辣且虾过敏，注意荤素和做法多样。",
    }
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json=payload).json()
        assert result["status"] == "ok"
        assert "肉鱼菜主体来源参考" in result["reason"]
        assert "未核验食材比例" in result["reason"]
        constraints = result["conversation_state"]["constraints"]
        assert {"荤素搭配", "做法多样"} <= set(constraints["preferences"])
        assert {"蒸鸡肉", "蒸猪肉"}.intersection(r["name"] for r in result["menu"])
        sid = result["conversation_state"]["session_id"]
        retry = client.post("/chat", json={**payload, "session_id": sid}).json()
        assert retry == result
    restarted = ScriptedLLM([Intent(action="explain")])
    with client_for(tmp_path, catalog, restarted) as client:
        explained = client.post(
            "/chat", json={"user_id": 3, "session_id": sid, "message": "解释这套餐"}
        ).json()
        assert explained["menu"] == result["menu"]
        assert {"荤素搭配", "做法多样"} <= set(
            explained["conversation_state"]["constraints"]["preferences"]
        )
        denied = client.post("/chat", json={"user_id": 4, "session_id": sid, "message": "继续"})
        assert denied.status_code == 409


@pytest.mark.parametrize("zero_meat", [False, True])
def test_api_cannot_hide_missing_or_conflicting_mixed_reference_when_model_selects_opening(
    tmp_path, zero_meat
):
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2
    )
    pool = public_pool()
    # No permitted meat body: the remaining chicken is spicy and shrimp is allergenic.
    pool = [r for r in pool if r.name not in {"蒸鸡肉", "蒸猪肉"}]
    catalog = DataCatalog(
        profiles={3: profile}, recipes={r.recipe_id: r for r in pool}, quality_report={}
    )

    class OpeningOnlyLLM(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    extra = {"meat_dish_count": 0, "vegetarian_dish_count": 4} if zero_meat else {}
    llm = OpeningOnlyLLM(
        [
            complete_intent(
                people=5, dish_count=6, soup_count=1, no_spicy=True, allergies=["虾"], **extra
            )
        ]
    )
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 3,
                "message": (
                    "5人晚餐，6道菜其中1道汤，不辣、虾过敏，注意荤素和做法多样。"
                    + ("0道荤菜、4道素菜，另有主食和汤。" if zero_meat else "")
                ),
            },
        ).json()
    assert result["status"] == "ok"
    assert len(result["menu"]) == 6
    assert not {"蒸鸡肉", "蒸猪肉", "辣椒鸡肉", "清蒸虾"}.intersection(
        r["name"] for r in result["menu"]
    )
    assert "荤素来源参考仍有缺口或冲突" in result["reason"]
    assert ("不擅自加肉" if zero_meat else "不宣称已满足") in result["reason"]
