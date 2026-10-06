"""Public source-pattern contracts, not nutrition/clinical or human gold."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.health_preferences import repair_health_preferences
from app.agent.planner import MenuPlanner
from app.agent.response_copy import required_fact_ids
from app.api.main import create_app
from app.domain.heart_protein_reference import (
    preferred_protein_body,
    protein_body_tradeoff,
)
from app.domain.models import (
    Constraints,
    Intent,
    Recipe,
    ScopedMethod,
    SessionState,
    UserProfile,
)
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def dish(
    name: str,
    foods: str,
    steps: str = "食材蒸熟装盘。",
    label: str = "午餐、晚餐、清淡",
) -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": label}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods",
    [
        ("清蒸龙利鱼", "龙利鱼200克；盐2克；生抽5毫升"),
        ("清蒸草鱼", "草鱼200克；姜5克"),
        ("蒸豆腐", "豆腐200克；葱5克"),
        ("蒸鸡胸肉", "鸡胸肉200克；盐1小勺"),
        ("去皮鸡肉蒸豆腐", "去皮鸡肉200克；豆腐100克"),
        ("蒸鸡脯肉", "鸡脯肉200克；姜5克"),
    ],
)
def test_named_declared_supported_body_not_a_sodium_dose(name: str, foods: str) -> None:
    assert preferred_protein_body(dish(name, foods))


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("清蒸鲈鱼", "鱼露5毫升；白菜200克", "白菜蒸熟。"),
        ("鸡汤蒸蛋", "鸡汤100克；鸡蛋2个", "蒸熟装盘。"),
        ("蒸鸡肉", "鸡肉200克", "蒸熟装盘。"),
        ("蒸鸡胸肉", "带皮鸡胸肉200克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克；猪肉末30克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克；神秘肉末30克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克；火腿20克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克；鸡皮20克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克；糖1克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克；黄油1克", "蒸熟装盘。"),
        ("蒸豆腐", "豆腐200克", "加入猪肉末蒸熟装盘。"),
        ("烤三文鱼", "三文鱼200克；烧烤汁5克", "烤熟装盘。"),
        ("烤三文鱼", "三文鱼200克", "加入烧烤粉烤熟装盘。"),
        ("鳕鱼泥", "鳕鱼200克", "蒸熟后打成泥。"),
        ("薯泥鳕鱼糕", "鳕鱼200克；土豆200克", "煮熟后压碎成泥。"),
        ("蒸豆腐", "豆腐200克", "煎熟后蒸熟。"),
        ("蒸豆腐", "豆腐200克", "蒸熟淋上热油。"),
        ("蒸豆腐", "豆腐200克", "选择开始烹饪，程序结束装盘。"),
    ],
)
def test_incomplete_incidental_processed_or_unsupported_body_gets_no_credit(
    name: str,
    foods: str,
    steps: str,
) -> None:
    assert not preferred_protein_body(dish(name, foods, steps))


@pytest.mark.parametrize(
    "steps",
    [
        "不打成泥，蒸熟后切片。",
        "有人说打成泥，食材蒸熟装盘。",
        "“打成泥”只是示例，食材蒸熟装盘。",
        "不加糖，不加入猪肉末，食材蒸熟装盘。",
        "鸡腿菇和豆腐蒸熟装盘。",
    ],
)
def test_negation_and_discussion_are_not_added_food_or_puree(steps: str) -> None:
    assert preferred_protein_body(dish("蒸豆腐", "豆腐200克", steps))


@pytest.mark.parametrize(
    "update",
    [
        {"eligible": False},
        {"quality_flags": ["unparsed_ingredients"]},
        {"categories": ["vegetable"]},
    ],
)
def test_source_qualification_cannot_be_overridden_by_labels(
    update: dict[str, Any],
) -> None:
    record = dish("蒸豆腐", "豆腐200克").model_copy(update=update)
    assert not preferred_protein_body(record)


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("鱼豆腐汤", "鱼肉200克；豆腐200克；水500克", "煮汤连汤盛出。"),
        ("鸡肉米饭", "鸡胸肉100克；大米200克", "米饭煮熟后与鸡胸肉装盘。"),
        ("白菜配豆腐", "白菜200克；豆腐20克", "白菜与豆腐蒸熟。"),
    ],
)
def test_forged_role_or_incidental_food_does_not_fill_body(
    name: str, foods: str, steps: str
) -> None:
    record = dish(name, foods, steps).model_copy(update={"categories": ["protein"]})
    assert not preferred_protein_body(record)


def sources() -> tuple[Recipe, Recipe, Recipe, Recipe]:
    return (
        dish("蒸猪肉", "猪肉200克"),
        dish("蒸鱼", "鱼肉200克；盐2克；生抽5毫升"),
        dish("蒸鸡胸肉", "鸡胸肉200克"),
        dish("米饭", "大米200克；水300克", "煮熟食用。"),
    )


def test_red_meat_is_not_forbidden_or_declared_high_fat() -> None:
    red, fish, _, _ = sources()
    rules = RuleEngine()
    assert rules.evaluate(red, Constraints(health_goals=["护心"])).allowed
    assert protein_body_tradeoff(red, fish, ["降压", "护心"], rules.goal_evidence)
    assert not protein_body_tradeoff(red, fish, ["降压"], rules.goal_evidence)
    assert not protein_body_tradeoff(fish, red, ["护心"], rules.goal_evidence)


def test_ordinary_sodium_presence_can_trade_off_only_in_source_pass() -> None:
    red, fish, _, _ = sources()
    rules = RuleEngine()
    constraints = Constraints(dish_count=1, health_goals=["护心", "降压"])
    kwargs: dict[str, Any] = dict(
        scores={
            r.recipe_id: rules.soft_goal_scores(r, constraints) for r in [red, fish]
        },
        order={red.recipe_id: 0, fish.recipe_id: 1},
        food_matches=lambda r, t: False,
    )
    legacy = repair_health_preferences([red], [red, fish], constraints, **kwargs)
    assert legacy.recipes == [red]
    source = repair_health_preferences(
        [red],
        [red, fish],
        constraints,
        **kwargs,
        source_food_reference=preferred_protein_body,
        source_food_tradeoff=lambda a, b: protein_body_tradeoff(
            a, b, constraints.health_goals, rules.goal_evidence
        ),
    )
    assert source.recipes == [fish] and source.source_food_tradeoff_indices == {0}
    retry = repair_health_preferences(
        source.recipes,
        [red, fish],
        constraints,
        **kwargs,
        source_food_reference=preferred_protein_body,
        source_food_tradeoff=lambda a, b: protein_body_tradeoff(
            a, b, constraints.health_goals, rules.goal_evidence
        ),
    )
    assert retry.recipes == [fish] and not retry.changed_indices


def test_last_source_pass_does_not_churn_staple_or_other_body() -> None:
    red, fish, chicken, rice = sources()
    other_chicken = dish("另一鸡胸肉", "鸡胸肉200克")
    sweet_rice = dish("甜糯米饭", "糯米200克；糖20克", "煮熟食用。")
    constraints = Constraints(
        dish_count=3, preferences=["清淡", "做法多样"], health_goals=["降压", "护心"]
    )
    pool = [red, chicken, rice, other_chicken, fish, sweet_rice]
    result = MenuPlanner(RuleEngine()).plan(
        pool, constraints, current=[chicken, red, rice]
    )
    assert result.recipes == [chicken, fish, rice]
    assert any("普通盐/含钠调味来源可能增加" in text for text in result.warnings)


@pytest.mark.parametrize(
    "constraints",
    [
        Constraints(
            dish_count=1, preferred_ingredients=["猪肉"], health_goals=["护心"]
        ),
        Constraints(dish_count=1, preferences=["炖"], health_goals=["护心"]),
        Constraints(
            dish_count=1,
            scoped_methods=[
                ScopedMethod(food="猪肉", slot=1, method="炖", required=True)
            ],
            health_goals=["护心"],
        ),
        Constraints(dish_count=1, preferences=["便当"], health_goals=["护心"]),
    ],
)
def test_named_food_method_or_scene_is_not_traded_away(
    constraints: Constraints,
) -> None:
    red = dish("炖猪肉", "猪肉200克", "炖熟装盘。", "晚餐、便当")
    _, fish, _, _ = sources()
    result = MenuPlanner(RuleEngine()).plan([red, fish], constraints, current=[red])
    assert result.recipes == [red]


def test_other_goal_proxy_decline_does_not_get_sodium_exception() -> None:
    red, fish, _, _ = sources()
    constraints = Constraints(dish_count=1, health_goals=["护心", "自定义保护目标"])
    rules = RuleEngine()
    result = repair_health_preferences(
        [red],
        [red, fish],
        constraints,
        scores={red.recipe_id: (0, 1), fish.recipe_id: (-6, 0)},
        order={red.recipe_id: 0, fish.recipe_id: 1},
        food_matches=lambda r, t: False,
        source_food_reference=preferred_protein_body,
        source_food_tradeoff=lambda a, b: protein_body_tradeoff(
            a, b, constraints.health_goals, rules.goal_evidence
        ),
    )
    assert result.recipes == [red]


@pytest.mark.parametrize(
    "constraint",
    [
        {"allergies": ["鱼"]},
        {"allergies": ["大豆"]},
        {"no_spicy": True},
        {"diet_mode": "vegan"},
        {"diet_mode": "ovo_lacto_vegetarian"},
    ],
)
def test_default_source_pass_never_bypasses_hard_rules(
    constraint: dict[str, Any],
) -> None:
    red, fish, _, _ = sources()
    tofu = dish("蒸豆腐", "豆腐200克；辣椒3克")
    constraints = Constraints(dish_count=1, health_goals=["护心"], **constraint)
    rules = RuleEngine()
    result = MenuPlanner(rules).plan([red, fish, tofu], constraints)
    assert all(rules.evaluate(r, constraints).allowed for r in result.recipes)


def test_local_and_readonly_boundaries_survive_last_source_pass() -> None:
    red, fish, _, rice = sources()
    planner = MenuPlanner(RuleEngine())
    constraints = Constraints(dish_count=2, health_goals=["护心"])
    assert (
        planner.plan(
            [red, fish, rice], constraints, current=[red, rice], replace_slot=2
        ).recipes[0]
        == red
    )
    assert planner.plan(
        [red, fish, rice],
        constraints,
        current=[red, rice],
        recheck_soft_preferences=False,
    ).recipes == [red, rice]


class PatternLLM(BaseLLM):
    async def parse(
        self, message: str, state: SessionState, profile: UserProfile
    ) -> Intent:
        if message == "一人晚餐1道菜护心，不辣，无忌口":
            return Intent(
                action="plan",
                people=1,
                meal_type="晚餐",
                dish_count=1,
                soup_count=0,
                health_goals=["护心"],
                no_spicy=True,
                restrictions_confirmed=True,
            )
        return Intent(action="plan")

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return [
            "opening"
        ]  # Mandatory limitations cannot be omitted by model selection.

    async def aclose(self) -> None:
        pass


def test_native_default_discloses_source_tradeoff_and_preserves_id_retry(
    tmp_path: Path,
) -> None:
    red, fish, _, _ = sources()
    profile = UserProfile(data_scope="synthetic", user_id=900001, age=30, sex="男")
    setting_values: dict[str, Any] = dict(
        _env_file=None,
        local_profile_path=None,
        deepseek_api_key="",
        session_db=tmp_path / "sessions.db",
    )
    settings = Settings(**setting_values)
    application = create_app(
        settings,
        PatternLLM(),
        DataCatalog(
            {900001: profile, 900002: profile.model_copy(update={"user_id": 900002})},
            {r.recipe_id: r for r in [red, fish]},
            {},
        ),
    )
    with TestClient(application) as client:
        request = {
            "user_id": 900001,
            "message": "一人晚餐1道菜护心，不辣，无忌口",
            "request_id": "source-body-1",
        }
        result = client.post("/chat", json=request)
        assert result.status_code == 200
        value = result.json()
        assert (
            value["status"] == "ok" and value["menu"][0]["recipe_id"] == fish.recipe_id
        )
        assert "普通盐/含钠调味来源可能增加" in value["reason"]
        assert "不宣称低钠" in value["reason"]
        sid = value["conversation_state"]["session_id"]
        assert client.post("/chat", json={**request, "session_id": sid}).json() == value
        assert (
            client.post(
                "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
            ).json()["menu"]
            == value["menu"]
        )
        assert (
            client.post(
                "/chat", json={"user_id": 900002, "session_id": sid, "message": "继续"}
            ).status_code
            == 409
        )
        for stream in (False, True):
            compatible = client.post(
                "/v1/chat/completions",
                json={
                    "model": "fangtai-meal-agent",
                    "user": "900001",
                    "stream": stream,
                    "messages": [{"role": "user", "content": request["message"]}],
                },
            )
            assert compatible.status_code == 200
            if stream:
                chunks = [
                    json.loads(line[6:])
                    for line in compatible.text.splitlines()
                    if line.startswith("data: ") and line != "data: [DONE]"
                ]
                text = "".join(
                    chunk["choices"][0]["delta"].get("content", "") for chunk in chunks
                )
            else:
                text = compatible.json()["choices"][0]["message"]["content"]
            assert "蒸鱼" in text and "普通盐/含钠调味来源可能增加" in text
            assert "不宣称低钠" in text


def test_source_tradeoff_is_a_mandatory_fact_not_model_rating() -> None:
    assert "source_food_tradeoff" in required_fact_ids(
        Intent(action="plan"), {"source_food_tradeoff": "原方依据与边界"}
    )
