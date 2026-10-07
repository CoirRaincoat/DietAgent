"""Explicit named food vs sodium-presence proxies, not clinical gold labels."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.planner import MenuPlanner
from app.agent.protein_food_preferences import repair_protein_food_preferences
from app.agent.response_copy import required_fact_ids
from app.api.main import create_app
from app.domain.heart_protein_reference import named_food_tradeoff
from app.domain.models import Constraints, Intent, Recipe, ScopedMethod
from app.domain.protein_food_references import named_protein_foods
from app.infrastructure.data import normalize_recipes
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def dish(name: str, foods: str, steps: str = "食材蒸熟装盘。") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": "晚餐、清淡",
                    }
                ]
            ).values()
        )
    )


def sources() -> tuple[Recipe, Recipe]:
    return (
        dish("鸡肉玉米肠", "鸡胸肉200克；胡萝卜20克；玉米20克"),
        dish("清蒸草鱼", "草鱼200克；盐2克；生抽5毫升"),
    )


def test_default_planner_prefers_explicit_fish_over_incidental_carrot_gain() -> None:
    old, fish = sources()
    rules = RuleEngine()
    c = Constraints(
        dish_count=1,
        health_goals=["护心", "降压"],
        preferences=["清淡"],
        preferred_ingredients=["鱼"],
        no_spicy=True,
    )
    assert rules.soft_goal_scores(fish, c)[0] == rules.soft_goal_scores(old, c)[0]
    result = MenuPlanner(rules).plan([old, fish], c, current=[old])
    assert result.recipes == [fish]
    assert all(rules.evaluate(r, c).allowed for r in result.recipes)


def test_authorized_direct_repair_discloses_the_actual_source_tradeoff() -> None:
    old, fish = sources()
    rules = RuleEngine()
    c = Constraints(
        dish_count=1, health_goals=["护心", "降压"], preferred_ingredients=["鱼"]
    )
    result = repair_protein_food_preferences(
        [old],
        [old, fish],
        c,
        canonical_food=rules.canonical_food,
        food_matches=lambda r, t: bool(rules.food_matches(r, t)),
        goal_scores={r.recipe_id: rules.soft_goal_scores(r, c) for r in (old, fish)},
        order={old.recipe_id: 0, fish.recipe_id: 1},
        source_food_tradeoff=lambda a, b: named_food_tradeoff(
            a, b, c.health_goals, c.preferred_ingredients, rules.goal_evidence
        ),
    )
    assert result.recipes == [fish]
    # There is no longer a sodium-presence score regression to disclose as an
    # exception. Source sodium attention belongs in nutrition/API output.
    assert not any(w.startswith("为优先有完整原方主体依据") for w in result.warnings)
    assert rules.goal_evidence(fish, "降压").has_attention


@pytest.mark.parametrize(
    "goals,requested",
    [
        ([], ["鱼"]),
        (["控糖"], ["鱼"]),
        (["护心"], []),
        (["护心"], ["鸡肉"]),
        (["护心"], ["豆制品"]),
    ],
)
def test_source_exception_requires_explicit_supported_food_and_heart_or_bp_goal(
    goals: list[str], requested: list[str]
) -> None:
    old, fish = sources()
    assert not named_food_tradeoff(
        old, fish, goals, requested, RuleEngine().goal_evidence
    )


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("鱼豆腐汤", "草鱼200克；豆腐200克；水500克", "煮汤连汤盛出。"),
        ("鳕鱼泥", "鳕鱼200克", "蒸熟后打成泥。"),
        ("蒸草鱼", "草鱼200克；白糖1克", "蒸熟装盘。"),
        ("蒸草鱼", "草鱼200克；黄油1克", "蒸熟装盘。"),
        ("蒸草鱼", "草鱼200克；火腿10克", "蒸熟装盘。"),
        ("蒸草鱼", "草鱼200克", "蒸熟淋热油。"),
        ("蒸草鱼", "草鱼200克", "请选择开始烹饪，程序结束取出。"),
        ("蒸草鱼", "鱼露5毫升；白菜200克", "蒸熟装盘。"),
        ("烤三文鱼", "三文鱼200克；烧烤粉1克", "烤熟装盘。"),
    ],
)
def test_unknown_soup_puree_and_new_non_sodium_cautions_get_no_exception(
    name: str, foods: str, steps: str
) -> None:
    old, _ = sources()
    assert not named_food_tradeoff(
        old,
        dish(name, foods, steps),
        ["护心", "降压"],
        ["鱼"],
        RuleEngine().goal_evidence,
    )


def test_missing_source_evidence_cannot_authorize_exception() -> None:
    old, fish = sources()
    assert not named_food_tradeoff(old, fish, ["护心"], ["鱼"], lambda r, g: None)


@pytest.mark.parametrize("axis", ["other_goal", "appended", "short_old", "short_new"])
def test_exception_does_not_override_other_goal_or_appended_axes(axis: str) -> None:
    old, fish = sources()
    c = Constraints(
        dish_count=1,
        health_goals=["护心", "降压", "其他目标"],
        preferred_ingredients=["鱼"],
    )
    scores: dict[str, tuple[int, ...]] = {
        old.recipe_id: (2, 2, 2, 1),
        fish.recipe_id: (-3, -3, 2, 1),
    }
    if axis == "other_goal":
        scores[fish.recipe_id] = (-3, -3, 1, 1)
    elif axis == "appended":
        scores[fish.recipe_id] = (-3, -3, 2, 0)
    elif axis == "short_old":
        scores[old.recipe_id] = (2, 2)
    else:
        scores[fish.recipe_id] = (-3, -3)
    rules = RuleEngine()
    result = repair_protein_food_preferences(
        [old],
        [old, fish],
        c,
        canonical_food=rules.canonical_food,
        food_matches=lambda r, t: bool(rules.food_matches(r, t)),
        goal_scores=scores,
        order={old.recipe_id: 0, fish.recipe_id: 1},
        source_food_tradeoff=lambda a, b: named_food_tradeoff(
            a, b, c.health_goals, c.preferred_ingredients, rules.goal_evidence
        ),
    )
    assert result.recipes == [old]


@pytest.mark.parametrize(
    "restriction",
    [{"allergies": ["鱼"]}, {"diet_mode": "vegan"}, {"excluded_ingredients": ["鱼"]}],
)
def test_default_planner_never_waives_hard_rules(restriction: dict[str, Any]) -> None:
    old, fish = sources()
    tofu = dish("蒸豆腐", "豆腐200克")
    c = Constraints(
        dish_count=1,
        health_goals=["护心", "降压"],
        preferred_ingredients=["鱼"],
        **restriction,
    )
    result = MenuPlanner(RuleEngine()).plan([old, fish, tofu], c)
    assert fish not in result.recipes


def test_spicy_source_stays_out_even_if_fish_is_requested() -> None:
    old, _ = sources()
    spicy = dish("香辣蒸鱼", "草鱼200克；辣椒5克；盐2克")
    c = Constraints(
        dish_count=1,
        health_goals=["护心", "降压"],
        preferred_ingredients=["鱼"],
        no_spicy=True,
    )
    assert spicy not in MenuPlanner(RuleEngine()).plan([old, spicy], c).recipes


def test_continue_and_unrelated_local_slot_keep_protein() -> None:
    old, fish = sources()
    veg = dish("蒸白菜", "白菜200克")
    another = dish("蒸花菜", "花菜200克")
    c = Constraints(
        dish_count=2, health_goals=["护心", "降压"], preferred_ingredients=["鱼"]
    )
    for recheck, slot in ((False, None), (True, 2)):
        result = MenuPlanner(RuleEngine()).plan(
            [old, fish, veg, another],
            c,
            current=[old, veg],
            recheck_soft_preferences=recheck,
            replace_slot=slot,
        )
        assert result.recipes[0] == old


def test_explicit_scoped_method_remains_protected() -> None:
    old, _ = sources()
    fish = dish("煮草鱼", "草鱼200克；盐2克", "食材煮熟装盘。")
    c = Constraints(
        dish_count=1,
        health_goals=["护心", "降压"],
        preferred_ingredients=["鱼"],
        scoped_methods=[ScopedMethod(food="鸡肉", method="蒸", slot=1)],
    )
    assert MenuPlanner(RuleEngine()).plan([old, fish], c, current=[old]).recipes == [
        old
    ]


def test_tradeoff_fact_is_mandatory_in_response() -> None:
    assert "source_food_tradeoff" in required_fact_ids(
        Intent(), {"source_food_tradeoff": "新方含盐，不承诺低钠。"}
    )


def test_actual_default_api_n08_retains_other_source_slots_and_reports_tradeoff(
    tmp_path: Path,
) -> None:
    message = "4人晚餐，5道菜含1道汤，不辣，清淡一点，想兼顾护心控压，优先鱼和豆制品，没有其他忌口。"

    def handler(request: httpx.Request) -> httpx.Response:
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert not set(context) & {"profile", "facts", "diners", "existing_constraints"}
        assert context["message"] == message
        intent = {
            "action": "plan",
            "people": 4,
            "meal_type": "晚餐",
            "dish_count": 5,
            "soup_count": 1,
            "no_spicy": True,
            "preferences": ["清淡"],
            "health_goals": ["护心", "降压"],
            "preferred_ingredients": ["鱼", "豆制品"],
            "query_terms": ["鱼", "豆制品"],
            "restrictions_confirmed": True,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(intent, ensure_ascii=False)},
                    }
                ]
            },
        )

    provider = DeepSeekLLM(
        "not-live", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    settings = Settings(  # type: ignore[call-arg]  # BaseSettings runtime override.
        _env_file=None,
        local_profile_path=None,
        deepseek_api_key=SecretStr(""),
        session_db=tmp_path / "sessions.db",
    )
    application = create_app(settings=settings, llm=provider)
    with TestClient(application) as client:
        request = {"user_id": 900001, "message": message, "request_id": "fixed-n08"}
        response = client.post("/chat", json=request)
        assert response.status_code == 200
        value = response.json()
        assert value["status"] == "ok"
        agent = application.state.agent
        records = [agent.catalog.recipes[r["recipe_id"]] for r in value["menu"]]
        # This is initial planning, not a protected local edit: require real
        # vegetables instead of the old incidental-vegetable puree bonus.
        assert sum(r.categories == ["vegetable"] for r in records) >= 2
        assert any(r.name == "基础煮燕麦饭" for r in records)
        assert "鱼" in named_protein_foods(records[2])
        assert len(records) == 5 and sum("soup" in r.categories for r in records) == 1
        state = value["conversation_state"]
        constraints = Constraints.model_validate(state["constraints"])
        assert constraints.preferred_ingredients == ["鱼", "豆制品"]
        assert all(agent.rules.evaluate(r, constraints).allowed for r in records)
        assert "含钠来源待核" in value["reason"]
        assert "盐" in value["reason"] and "不能判定低钠" in value["reason"]
        assert not any(
            "尚未覆盖有实际食材依据的蛋白菜名称参考：鱼" in w for w in value["warnings"]
        )
        assert (
            client.post(
                "/chat", json={**request, "session_id": state["session_id"]}
            ).json()
            == value
        )


@pytest.mark.parametrize("stream", [False, True])
def test_actual_n08_compatible_wire_keeps_fish_and_mandatory_caveat(
    tmp_path: Path, stream: bool
) -> None:
    message = "4人晚餐，5道菜含1道汤，不辣，清淡一点，想兼顾护心控压，优先鱼和豆制品，没有其他忌口。"

    def handler(request: httpx.Request) -> httpx.Response:
        context = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert context["message"] == message
        assert not set(context) & {"profile", "facts", "diners", "existing_constraints"}
        intent = {
            "action": "plan",
            "people": 4,
            "meal_type": "晚餐",
            "dish_count": 5,
            "soup_count": 1,
            "no_spicy": True,
            "preferences": ["清淡"],
            "health_goals": ["护心", "降压"],
            "preferred_ingredients": ["鱼", "豆制品"],
            "query_terms": ["鱼", "豆制品"],
            "restrictions_confirmed": True,
        }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(intent, ensure_ascii=False)},
                    }
                ]
            },
        )

    provider = DeepSeekLLM(
        "not-live", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    settings = Settings(  # type: ignore[call-arg]  # BaseSettings runtime override.
        _env_file=None,
        local_profile_path=None,
        deepseek_api_key=SecretStr(""),
        session_db=tmp_path / "wire.db",
    )
    application = create_app(settings=settings, llm=provider)
    with TestClient(application) as client:
        body = {
            "model": "fangtai-meal-agent",
            "user": "900001",
            "stream": stream,
            "messages": [{"role": "user", "content": message}],
            "request_id": "n08-wire",
        }
        response = client.post("/v1/chat/completions", json=body)
        assert response.status_code == 200
        sid = response.headers["X-Session-ID"]
        if stream:
            chunks = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            text = "".join(
                chunk["choices"][0]["delta"].get("content", "") for chunk in chunks
            )
        else:
            text = response.json()["choices"][0]["message"]["content"]
        agent = application.state.agent
        state = agent.store.get(sid, 900001)
        records = [agent.catalog.recipes[key] for key in state.menu_ids]
        assert state.menu_valid and state.constraints.no_spicy
        assert len(records) == 5 and sum("soup" in r.categories for r in records) == 1
        assert "鱼" in named_protein_foods(records[2])
        assert all(r.name in text for r in records)
        assert "含钠来源待核" in text and "不能判定低钠" in text
        assert "盐" in text
        assert all(agent.rules.evaluate(r, state.constraints).allowed for r in records)
        ids = list(state.menu_ids)
        replay = client.post("/v1/chat/completions", json={**body, "session_id": sid})
        assert (
            replay.status_code == 200 and agent.store.get(sid, 900001).menu_ids == ids
        )
