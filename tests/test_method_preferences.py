"""Finite literal/source boundaries; not independent quality validation."""

import pytest

from app.agent.menu_balance import analyze_menu_balance
from app.agent.method_preferences import repair_method_preferences
from app.agent.response_copy import required_fact_ids, response_facts
from app.domain.method_preferences import (
    method_preference_warnings,
    method_reference_mask,
    method_request_clauses,
    method_swap_preserves,
    supported_method_preferences,
)
from app.domain.models import Constraints, Intent
from app.rules.engine import RuleEngine
from tests.test_explicit_method_protection import dish


@pytest.mark.parametrize(
    "text,expected",
    [
        ("想吃蒸菜", ("做法：蒸",)),
        ("这餐希望清蒸", ("做法：蒸",)),
        ("做法：不要油炸", ("做法：不要炸",)),
        ("不要炸但想要烤菜", ("做法：不要炸", "做法：烤")),
        ("注意做法多样", ("做法多样",)),
        ("做法：多样", ("做法多样",)),
        ("不要做法多样", ("不要做法多样",)),
        ("做法：全部蒸", ("做法：全部蒸",)),
        ("做法：分子料理", ("做法：分子料理",)),
        ("如果想吃蒸菜", ()),
        ("蒸菜好吗？", ()),
        ("解释蒸菜", ()),
        ("他说‘蒸菜’", ()),
        ("清淡，护心，晚餐，家常", ()),
        ("喜欢蒸鸡肉", ()),
        ("蒸烤箱", ()),
    ],
)
def test_literal_requests_not_inferred_methods(text, expected):
    assert method_request_clauses(text) == expected


@pytest.mark.parametrize(
    "steps,matched",
    [
        ("鸡肉蒸熟装盘。", True),
        ("鸡肉放入蒸烤箱，点击开始烹饪，烹饪完成装盘。", False),
        ("鸡肉不蒸，改为煮熟装盘。", False),
        ("鸡肉先蒸熟，再炒熟装盘。", False),
    ],
)
def test_real_finished_source_action_not_title_or_cached_methods(steps, matched):
    recipe = dish("清蒸鸡肉", steps).model_copy(update={"methods": ["蒸"]})
    assert bool(method_reference_mask(recipe, ["蒸"])) is matched


def test_conflicts_do_not_receive_ranking_credit():
    assert supported_method_preferences(["蒸", "不要蒸"]) == ()
    assert any("冲突" in w for w in method_preference_warnings([], ["蒸", "不要蒸"]))


def test_explicit_variety_protects_aggregates_not_old_method_identities():
    first = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    old = dish("煮白菜", "白菜煮熟装盘。", foods="白菜200克；盐1克")
    new = dish("烤白菜", "白菜烤熟装盘。", foods="白菜200克；盐1克")
    assert method_swap_preserves([first, old], 1, new, ["做法多样"])
    assert not method_swap_preserves([first, old], 1, new, ["做法多样", "煮"])


def repair(menu, candidates, constraints, *, scores=None, slot=None, allow=True):
    rules = RuleEngine()
    return repair_method_preferences(
        menu,
        candidates,
        constraints,
        goal_scores=(
            scores
            if scores is not None
            else {r.recipe_id: rules.soft_goal_scores(r, constraints) for r in [*menu, *candidates]}
        ),
        order={},
        food_matches=lambda r, t: bool(rules.food_matches(r, t)),
        replace_slot=slot,
        allow_repair=allow,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"preferred_ingredients": ["白菜"]},
        {"preferences": ["蒜香", "蒸"]},
        {"preferences": ["家常", "蒸"]},
    ],
)
def test_method_repair_cannot_erase_covered_food_flavor_or_scene(change):
    old = dish(
        "家常蒜香白菜", "白菜和蒜炒熟装盘。", "晚餐、蒜香", foods="白菜200克；大蒜2克；盐1克"
    )
    new = dish("蒸菠菜", "菠菜蒸熟装盘。", foods="菠菜200克；盐1克")
    constraints = Constraints(dish_count=1, preferences=["蒸"]).model_copy(update=change)
    assert repair([old], [new], constraints).recipes == [old]


@pytest.mark.parametrize("scores", [({}, {}), ((2, 2), (3, 1)), ((2, 2), (3,))])
def test_method_repair_cannot_trade_off_one_health_goal_or_missing_vector(scores):
    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    new = dish("蒸鸡肉", "鸡肉蒸熟装盘。")
    vectors = {} if scores == ({}, {}) else {old.recipe_id: scores[0], new.recipe_id: scores[1]}
    assert repair(
        [old],
        [new],
        Constraints(dish_count=1, preferences=["蒸"], health_goals=["护心", "降压"]),
        scores=vectors,
    ).recipes == [old]


def test_bounded_local_repair_is_stable_and_does_not_modify_sources():
    first = dish("煮白菜", "白菜煮熟装盘。", foods="白菜200克；盐1克")
    old = dish("煮菠菜", "菠菜煮熟装盘。", foods="菠菜200克；盐1克")
    new = dish("蒸青菜", "青菜蒸熟装盘。", foods="青菜200克；盐1克")
    inputs = [first, old, new]
    sources = [r.model_dump_json() for r in inputs]
    constraints = Constraints(dish_count=2, preferences=["蒸"])
    result = repair([first, old], [new], constraints, slot=2)
    assert result.recipes == [first, new] and result.changed_positions == {1}
    assert repair(result.recipes, inputs, constraints, slot=2).recipes == result.recipes
    assert repair([first, old], [new], constraints, allow=False).recipes == [first, old]
    assert sources == [r.model_dump_json() for r in inputs]
    with pytest.raises(ValueError, match="identity"):
        repair([old], [old.model_copy(update={"steps": "另一配方"})], constraints)
    with pytest.raises(ValueError, match="slot"):
        repair([old], [new], constraints, slot=2)


@pytest.mark.parametrize(
    "prefs,marker",
    [
        (["蒸"], "尚缺"),
        (["蒸", "不要蒸"], "冲突"),
        (["不要炸"], "未实现完整"),
        (["做法：全部蒸"], "暂无可靠映射"),
        (["做法多样"], "不是充分多样性"),
    ],
)
def test_method_limits_and_gaps_are_mandatory_facts(prefs, marker):
    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    facts = response_facts(
        intent=Intent(),
        constraints=Constraints(dish_count=1, preferences=prefs),
        diners=[],
        previous=[old],
        chosen=[old],
        balance=analyze_menu_balance([old]),
    )
    assert "method_preferences" in required_fact_ids(Intent(), facts)
    assert marker in facts["method_preferences"]
    assert "当前菜单无需调整" not in facts["opening"]


@pytest.mark.parametrize("stream", [False, True])
def test_transport_literal_grounding_and_minimal_model_cannot_hide_method_gap(tmp_path, stream):
    from fastapi.testclient import TestClient

    from tests.test_flavor_matching_integration import FlavorLLM, make_app

    class MethodLLM(FlavorLLM):
        async def parse(self, message, state, profile):
            return Intent(
                action="plan",
                people=1,
                meal_type="晚餐",
                dish_count=1,
                soup_count=0,
                restrictions_confirmed=True,
            )

    old = dish("煮鸡肉", "鸡肉煮熟装盘。")
    with TestClient(make_app(tmp_path, (old,), MethodLLM())) as client:
        body = {
            "model": "fangtai-meal-agent",
            "user": "900001",
            "stream": stream,
            "messages": [{"role": "user", "content": "1人晚餐1道菜，无其他忌口，想吃蒸菜。"}],
        }
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        if stream:
            import json

            chunks = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
        else:
            text = response.json()["choices"][0]["message"]["content"]
        assert "明确做法来源参考尚缺" in text and "蒸" in text
        assert "整餐全部采用" in text
