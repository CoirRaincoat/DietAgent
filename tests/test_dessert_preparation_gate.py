"""Authored preparation contrasts, not copied private recipes or clinical advice."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.domain.meal_roles import is_main_meal_recipe
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.domain.source_preparation import grain_completion_issue, preparation_dessert_evidence
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def record(name: str, foods: str, steps: str, label: str = "午餐、晚餐") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": label}]
            ).values()
        )
    )


def bad_preparations() -> list[Recipe]:
    return [
        record(
            "椰香小圆片",
            "黄油90克；糖粉50克；鸡蛋50克；低筋面粉150克",
            "黄油与糖粉打匀，加入蛋液，拌入面粉，掐8克一个的小面团按扁，180度烤10分钟。",
        ),
        record(
            "山药酸奶杯",
            "山药100克；饼干80克；酸奶100毫升",
            "山药蒸熟碾碎，与酸奶搅打混合，饼干碾碎，将饼干末撒在表面。",
        ),
        record(
            "枣胶方块",
            "阿胶粉80克；核桃仁70克；大枣40克；冰糖粉50克",
            "阿胶粉与冰糖粉混匀，加入核桃大枣，开始烹饪，冷却切块。",
        ),
        record(
            "果香银耳",
            "木瓜300克；银耳20克；水600克；冰糖40克",
            "银耳加水煮制，加入木瓜冰糖，再煮制15分钟，倒出食用。",
        ),
        record(
            "红豆薏米粥",
            "红豆100克；薏米80克；水800克；红糖60克",
            "加入红糖，设置2分钟100℃2档混合。烹饪结束，取出食用。",
        ),
        record("蒸果小块", "南瓜200克；冰糖20克", "冰糖煮成糖浆，淋在蒸好的南瓜上。"),
        record("甜根茎碗", "山药200克；白糖20克；红枣10克", "山药与白糖搅打后加入红枣蒸制。"),
        record("花香烤块", "南瓜200克；蜂蜜10克；桂花酱10克", "蜂蜜与桂花酱混合，南瓜烹饪后刷酱。"),
        record(
            "金黄小丸",
            "南瓜200克；糯米粉100克；白糖20克；芝麻20克",
            "揉成面团搓圆裹芝麻，芝麻球慢炸。",
        ),
    ]


def ordinary_menu() -> list[Recipe]:
    return [
        record("蒸蛋", "鸡蛋100克；盐1克", "鸡蛋加盐打匀，蒸熟装盘。"),
        record("清炒白菜", "白菜300克；食用油3克；盐1克", "白菜炒熟装盘。"),
        record("米饭", "大米200克；水400克", "大米加水煮熟装盘。"),
    ]


@pytest.mark.parametrize("index", range(9))
def test_bad_preparation_never_fills_meal_role_even_with_stale_tags(index: int) -> None:
    recipe = bad_preparations()[index]
    assert not is_main_meal_recipe(recipe)
    stale = recipe.model_copy(update={"categories": ["protein"], "eligible": True})
    assert not is_main_meal_recipe(stale)
    result = MenuPlanner(RuleEngine()).plan([stale], Constraints(dish_count=1))
    assert not result.recipes and result.failure
    assert replacement_candidates([ordinary_menu()[0]], [stale], "stable") == []


def test_existing_bad_menu_is_repaired_with_scope_and_restart_stability() -> None:
    bad = bad_preparations()[0].model_copy(update={"categories": ["protein"], "eligible": True})
    foods = ordinary_menu()
    old = [bad, foods[1], foods[2]]
    result = MenuPlanner(RuleEngine()).plan([*foods, bad], Constraints(), current=old)
    assert [r.recipe_id for r in result.recipes] == [r.recipe_id for r in foods]
    retry = MenuPlanner(RuleEngine()).plan([*foods, bad], Constraints(), current=result.recipes)
    assert retry.recipes == result.recipes and not retry.changes


class PlanningLLM(BaseLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "解释" in message:
            return Intent(action="explain")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=3,
            restrictions_confirmed=True,
            no_spicy=True,
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def application(tmp_path: Path) -> Any:
    settings = Settings.model_construct(session_db=tmp_path / "preparations.db")
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    foods = [*bad_preparations(), *ordinary_menu()]
    return create_app(
        settings,
        PlanningLLM(),
        DataCatalog({900001: profile}, {r.recipe_id: r for r in foods}, {}),
        SessionStore(settings.database_path),
    )


def test_http_and_sse_exclude_preparation_counterexamples(tmp_path: Path) -> None:
    allowed = {r.recipe_id for r in ordinary_menu()}
    with TestClient(application(tmp_path)) as client:
        response = client.post(
            "/chat", json={"user_id": 900001, "message": "一人晚餐3道，不辣，没有其他忌口"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok" and {r["recipe_id"] for r in data["menu"]} == allowed
        assert all(r["recipe_id"] in allowed for r in data["replacement_suggestions"])
        stream = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [{"role": "user", "content": "一人晚餐3道，不辣，没有其他忌口"}],
            },
        )
        assert stream.status_code == 200 and "[DONE]" in stream.text
        assert not any(r.name in stream.text for r in bad_preparations())


def test_legacy_bad_menu_is_not_explained_as_valid(tmp_path: Path) -> None:
    bad = bad_preparations()[0]
    store = SessionStore(tmp_path / "preparations.db")
    state = SessionState(
        session_id="0123456789abcdef0123456789abcdef",
        user_id=900001,
        constraints=Constraints(dish_count=1),
        meal_constraints=Constraints(dish_count=1),
        menu_ids=[bad.recipe_id],
        menu_valid=True,
        menu_structure_explicit=True,
        confirmed_fields=["people", "meal_type", "restrictions"],
    )
    store.save(state, None)
    with TestClient(application(tmp_path)) as client:
        response = client.post(
            "/chat",
            json={"user_id": 900001, "message": "解释这份菜单", "session_id": state.session_id},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "clarification_required" and not response.json()["menu"]


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("银耳木瓜鸡肉汤", "银耳20克；木瓜100克；鸡胸肉200克；水500克；白糖1克", "炖熟装盘。"),
        ("咸味银耳羹", "银耳20克；木瓜100克；盐1克；水500克；冰糖1克", "煮熟装盘。"),
        ("肉皮冻", "猪肉皮200克；明胶2克；白糖1克；盐1克", "猪肉皮炖熟，冷却切块。"),
        ("木瓜小炒", "木瓜100克；银耳20克；白糖1克；盐1克", "炒熟装盘。"),
        ("早餐黄油面包", "面粉200克；黄油30克；糖粉10克；酵母2克", "面团发酵后按扁，烤熟。"),
        ("香菇豆腐", "豆腐200克；香菇50克", "蒸熟装盘。"),
        (
            "饼干碎裹鸡肉",
            "鸡胸肉200克；饼干20克；酸奶10毫升；盐1克",
            "鸡肉酸奶混合后裹饼干碎煎熟。",
        ),
        ("熟豆粥", "熟红豆100克；熟薏米80克；红糖10克", "加入红糖混合2分钟，装盘。"),
        ("红豆薏米粥", "红豆100克；薏米80克；水800克", "红豆薏米加入水，熬煮后取出。"),
        ("红豆薏米粥", "红豆100克；薏米80克；水800克", "食材入锅，开始烹饪，取出。"),
    ],
)
def test_ordinary_and_completed_contrasts_keep_recall(name: str, foods: str, steps: str) -> None:
    recipe = record(name, foods, steps)
    assert is_main_meal_recipe(recipe)


@pytest.mark.parametrize(
    "steps",
    [
        "加入红糖，混合后取出。",
        "红豆薏米与红糖搅拌混合2分钟后取出食用。",
        "设置2分钟100℃2档混合。烹饪结束。",
    ],
)
def test_raw_grain_mention_or_finishing_words_do_not_prove_completion(steps: str) -> None:
    recipe = record("红豆薏米粥", "红豆100克；薏米80克；水800克；红糖20克", steps)
    assert not recipe.eligible and "unverified_grain_completion" in recipe.quality_flags
    stale = recipe.model_copy(update={"eligible": True})
    assert not RuleEngine().evaluate(stale, Constraints()).allowed
    assert not is_main_meal_recipe(stale)


def test_readiness_is_finite_not_general_unknown_text_certification() -> None:
    assert grain_completion_issue("红豆粥", ["红豆"], "无法进一步判断的原文") is None
    assert grain_completion_issue("家常盘", ["红豆"], "混合取出") is None
    assert grain_completion_issue("熟豆粥", ["熟红豆"], "混合取出") is None
    assert preparation_dessert_evidence([], "混合") == []


def test_cookie_oven_temperature_and_salt_do_not_create_ordinary_protein() -> None:
    recipe = record(
        "椰香小圆片",
        "黄油90克；糖粉50克；鸡蛋50克；低筋面粉150克；盐1克",
        "拌匀后掐8克一个小面团按扁，180度8分钟，冷却食用。",
    )
    assert recipe.categories == ["dessert"] and not is_main_meal_recipe(recipe)


def test_dessert_missing_evidence_is_not_guessed_from_name() -> None:
    recipe = record("特色玛格丽特", "白菜200克；盐1克", "白菜蒸熟装盘。")
    assert is_main_meal_recipe(recipe)


@pytest.mark.parametrize(
    "steps",
    [
        "主锅中加入大米和水，盖上锅盖，设置30分钟95℃2档，取出食用。",
        "锅中加入大米和水，盖上锅盖，设置8分钟9档，再设置45分钟2档。加入虾肉搅拌均匀，设置5分钟5档。",
    ],
)
def test_explicit_grain_loading_and_equipment_execution_does_not_require_cook_word(
    steps: str,
) -> None:
    recipe = record("白米粥", "大米100克；水600克", steps)
    assert recipe.eligible and is_main_meal_recipe(recipe)
