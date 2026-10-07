"""Authored meal-role counterexamples; no source profiles or paid model calls."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.api.presentation import recipe_provenance
from app.domain.meal_roles import is_main_meal_recipe, non_meal_roles, preparation_only_steps
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine
from evaluation.main_meal_oracle import main_meal_findings
from evaluation.regression_suite import (
    CaseDefinition,
    SuiteDefinition,
    TurnDefinition,
    evaluate_turn,
    run_functional_cases,
)

NON_MEALS = [
    ("虾滑", "虾仁200克；蛋清20克；淀粉10克；盐1克", "搅打上劲，取出依需求使用。", "component"),
    (
        "妩媚妃子笑",
        "洛神花10克；冰水150克；荔枝果肉100克；冰块50克",
        "荔枝肉与冷泡茶打浆，倒入装有冰块的杯子中。",
        "drink",
    ),
    (
        "法式红酒炖梨&白梨挞同烹",
        "红酒100克；啤梨100克；肉桂5克；红糖20克；全蛋液20克；低筋面粉100克",
        "制作挞皮，倒入模具开始烹饪。",
        "dessert",
    ),
    ("果蔬汁", "苹果100克；胡萝卜50克；水200克", "打浆后倒入杯中饮用。", "drink"),
    ("黄瓜柠檬水", "黄瓜50克；柠檬30克；糖粉10克；水500克", "过滤取汁饮用。", "drink"),
    ("鲜榨西瓜汁", "西瓜200克；水100克", "榨汁倒入杯中。", "drink"),
    ("胡萝卜苹果汁", "胡萝卜100克；苹果100克；水200克", "打浆饮用。", "drink"),
    ("柠檬蜂蜜水", "柠檬20克；蜂蜜10克；水200克", "加水搅匀饮用。", "drink"),
    ("黄瓜气泡水", "黄瓜20克；气泡水200克", "放入杯中饮用。", "drink"),
    ("山楂糕", "山楂100克；白糖30克；水20克", "熬煮后冷却凝固切块。", "dessert"),
    ("蓝莓山药小丸子", "山药100克；蓝莓酱20克；黄油5克", "蒸熟压泥包入果酱搓圆。", "dessert"),
    ("草莓山药球", "山药100克；草莓酱20克", "压泥包馅搓球。", "dessert"),
    ("绿豆汤", "绿豆100克；冰糖20克；水500克", "加冰糖煮熟。", "dessert"),
    ("红豆汤", "红豆100克；白砂糖20克；水500克", "煮熟后放糖。", "dessert"),
    ("银耳莲子羹", "银耳20克；莲子20克；蜂蜜10克", "炖熟加入蜂蜜。", "dessert"),
    ("甜玉米羹", "玉米100克；冰糖20克；水500克", "煮成羹。", "dessert"),
    ("南瓜甜汤", "南瓜100克；白糖20克；水500克", "煮熟。", "dessert"),
    ("柠檬百香果蜜", "柠檬20克；百香果20克；糖粉30克", "装瓶，食用时取出泡水饮用。", "component"),
    ("万能凉拌汁", "生抽10克；醋10克", "拌匀。", "component"),
    ("蔬菜碎", "胡萝卜100克；青菜100克", "切碎。", "component"),
    ("红豆蛋糕", "鸡蛋100克；红豆50克；糖20克", "烤熟。", "dessert"),
    ("黑芝麻丸", "黑芝麻80克；黑豆20克；蜂蜜20克", "研磨混合成丸。", "dessert"),
    ("小吊梨汤", "雪梨100克；银耳10克；黄冰糖20克", "煮熟。", "dessert"),
    ("玫瑰山药", "山药100克；玫瑰花5克；蜂蜜10克", "蒸熟压泥，用模具压制成型，淋蜂蜜。", "dessert"),
    ("芒果牛油果沙冰", "芒果100克；牛油果100克；牛奶60克", "打浆冷冻后搅碎。", "dessert"),
    ("陈皮秋梨汤", "雪梨100克；陈皮10克；冰糖20克；盐2克", "蒸熟。", "dessert"),
    (
        "柠檬红茶冻撞奶",
        "红茶10克；柠檬20克；冰糖10克；白凉粉10克；牛奶100克",
        "冷藏凝固后加入牛奶。",
        "dessert",
    ),
    (
        "甘蔗马蹄水",
        "甘蔗200克；马蹄100克；茅根10克；冰糖10克；水500克",
        "食材放入碗，加水，开始烹饪。",
        "drink",
    ),
    (
        "黑芝麻糕",
        "马蹄粉100克；粘米粉100克；水200克；冰糖20克；黑芝麻粉30克",
        "烧煮后倒入蛋糕模具，蒸熟冷却。",
        "dessert",
    ),
    ("马蹄糕", "马蹄100克；马蹄粉100克；水200克；冰糖20克", "倒入模具蒸熟冷却。", "dessert"),
]

MAIN_MEALS = [
    ("水煮虾滑", "虾仁200克；蛋清20克；盐1克", "搅打上劲后下锅煮熟装盘。"),
    ("荔枝虾球", "荔枝果肉100克；虾仁200克；盐1克", "炒熟。"),
    ("肉桂炖排骨", "肉桂5克；排骨200克；盐1克", "炖熟。"),
    ("盐水鸭", "鸭肉200克；盐2克；水500克", "煮熟装盘。"),
    ("茄汁白玉菇", "白玉菇100克；番茄酱10克；盐1克", "炒熟。"),
    ("糖醋排骨", "排骨200克；白糖10克；醋10克", "炖熟。"),
    ("萝卜糕", "萝卜100克；粘米粉100克；盐1克", "蒸熟切片。"),
    ("蒸米糕", "大米100克；水100克", "蒸熟。"),
    ("玉米饼", "玉米面100克；鸡蛋50克", "煎熟。"),
    ("玉米排骨汤", "玉米100克；排骨200克；盐1克", "煮熟。"),
    ("排骨绿豆汤", "排骨200克；绿豆100克；白糖1克", "炖熟。"),
    ("南瓜肉丸", "南瓜100克；猪肉200克；盐1克", "蒸熟。"),
    ("鸡肉玉米肠", "鸡胸肉200克；玉米粒50克；胡萝卜30克", "蒸熟。"),
    ("香菇酿肉", "香菇100克；猪肉200克", "蒸熟。"),
    ("蒸白菜", "白菜100克", "蒸熟。"),
    ("马蹄排骨汤", "马蹄100克；排骨200克；盐1克；水500克", "炖熟装盘。"),
    ("马蹄炒木耳", "马蹄100克；木耳50克；盐1克", "炒熟装盘。"),
]


@pytest.mark.parametrize(
    "steps",
    [
        "食材准备。母鸡洗净切块。备好黄芪片，姜切片，葱切段。",
        "牛肉洗净切块，腌制备用。",
        "烤箱预热；白菜洗净切片，放入烤盘备用。",
    ],
)
def test_preparation_only_text_does_not_fill_meal_slot_even_with_stale_eligible(steps: str) -> None:
    recipe = make_recipe("家常鸡肉", "鸡肉200克", steps)
    assert preparation_only_steps(steps) is True
    assert not recipe.eligible
    assert "preparation_only_steps" in recipe.quality_flags
    recipe.eligible = True
    assert not is_main_meal_recipe(recipe)
    assert MenuPlanner(RuleEngine()).plan([recipe], Constraints(dish_count=1)).failure


@pytest.mark.parametrize(
    "steps",
    [
        "鸡肉洗净切块后蒸熟装盘。",
        "胡萝卜洗净切丝，凉拌后装盘。",
        "切好放蒸锅，按下开始烹饪。",
        "食材准备。用勺拌匀。",
        "这是无法进一步判断完成度的原文。",
    ],
)
def test_executed_steps_and_other_unknown_text_are_not_claimed_prep_only(steps: str) -> None:
    assert preparation_only_steps(steps) is False


def test_independent_oracle_rejects_reviewed_prep_only_but_not_completed_variant() -> None:
    steps = "将母鸡洗净，切块。备好黄芪片，姜切片，葱切段。"
    assert main_meal_findings("黄芪汽锅鸡", "母鸡200克；黄芪10克", steps=steps)
    assert not main_meal_findings("黄芪汽锅鸡", "母鸡200克；黄芪10克", steps=steps + "蒸熟装盘。")


def test_independent_oracle_distinguishes_raw_shrimp_paste_from_finished_variant() -> None:
    steps = "搅打上劲，取出依需求使用。"
    assert main_meal_findings("虾滑", "虾仁200克；蛋清20克", steps=steps)
    assert not main_meal_findings("虾滑", "虾仁200克；蛋清20克", steps=steps + "煮熟装盘。")


def make_recipe(name: str, foods: str, steps: str, *, labels: str = "晚餐") -> Recipe:
    """Normalize a handwritten recipe using the same entry point as the catalog."""
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": labels}]
            ).values()
        )
    )


@pytest.mark.parametrize("name,foods,steps,role", NON_MEALS, ids=[r[0] for r in NON_MEALS])
def test_non_meal_role_has_evidence_not_incidental_vegetable(
    name: str, foods: str, steps: str, role: str
) -> None:
    recipe = make_recipe(name, foods, steps)
    assert role in recipe.categories
    assert not {"vegetable", "protein", "staple"}.intersection(recipe.categories)


@pytest.mark.parametrize("name,foods,steps", MAIN_MEALS, ids=[r[0] for r in MAIN_MEALS])
def test_savory_dishes_are_not_rejected_by_water_juice_or_cake_substrings(
    name: str, foods: str, steps: str
) -> None:
    recipe = make_recipe(name, foods, steps)
    plan = MenuPlanner(RuleEngine()).plan(
        [recipe], Constraints(dish_count=1, soup_count=int("汤" in name))
    )
    assert plan.failure is None
    assert [r.recipe_id for r in plan.recipes] == [recipe.recipe_id]


@pytest.mark.parametrize("name,foods,steps,role", NON_MEALS, ids=[r[0] for r in NON_MEALS])
def test_stale_role_metadata_does_not_bypass_menu_or_suggestion_gate(
    name: str, foods: str, steps: str, role: str
) -> None:
    bad = make_recipe(name, foods, steps)
    bad.categories = ["vegetable"]
    bad.eligible = True
    kept = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
    replacement = make_recipe("炒青菜", "青菜100克", "炒熟。")
    plan = MenuPlanner(RuleEngine()).plan(
        [bad, kept, replacement], Constraints(dish_count=2), current=[bad, kept]
    )
    assert plan.failure is None
    assert bad.recipe_id not in {r.recipe_id for r in plan.recipes}
    assert plan.recipes[1].recipe_id == kept.recipe_id
    options = replacement_candidates([kept], [bad, replacement], "role-audit")
    assert [r.recipe_id for r in options] == [replacement.recipe_id]


def test_sweet_soup_cannot_fill_requested_savory_soup_slot() -> None:
    sweet = make_recipe("绿豆汤", "绿豆100克；冰糖20克", "煮熟。")
    entree = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
    plan = MenuPlanner(RuleEngine()).plan([sweet, entree], Constraints(dish_count=2, soup_count=1))
    assert plan.failure is not None
    assert plan.recipes == []


def test_explicit_dessert_style_label_is_not_overridden_by_dinner_label() -> None:
    bad = make_recipe("特色小丸子", "鸡蛋50克；面粉50克", "烤熟。", labels="晚餐、甜品风味")
    assert "dessert" in bad.categories
    assert MenuPlanner(RuleEngine()).plan([bad], Constraints(dish_count=1)).failure is not None


def test_non_meal_repair_does_not_relax_non_spicy_rules_or_change_valid_slot() -> None:
    bad = make_recipe("果蔬汁", "胡萝卜100克；水200克", "打浆。")
    bad.categories = ["vegetable"]
    spicy = make_recipe("拌青菜", "青菜100克；小米辣5克", "拌匀。")
    kept = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
    replacement = make_recipe("炒青菜", "青菜100克", "炒熟。")
    plan = MenuPlanner(RuleEngine()).plan(
        [bad, spicy, kept, replacement],
        Constraints(dish_count=2, no_spicy=True),
        current=[bad, kept],
        replace_slot=1,
    )
    assert plan.failure is None
    assert [r.recipe_id for r in plan.recipes] == [replacement.recipe_id, kept.recipe_id]
    retried = MenuPlanner(RuleEngine()).plan(
        [bad, spicy, kept, replacement],
        Constraints(dish_count=2, no_spicy=True),
        current=plan.recipes,
    )
    assert [r.recipe_id for r in retried.recipes] == [r.recipe_id for r in plan.recipes]
    assert retried.changes == []


@pytest.mark.parametrize("meal_type", ["午餐", "晚餐", "早餐", "下午茶"])
def test_dessert_menu_mode_is_not_silently_introduced(meal_type: str) -> None:
    bad = make_recipe("山楂糕", "山楂100克；白糖30克", "冷却凝固。")
    assert (
        MenuPlanner(RuleEngine())
        .plan([bad], Constraints(dish_count=1, meal_type=meal_type))
        .failure
        is not None
    )


def test_ineligible_and_explicit_non_meal_metadata_remain_rejected() -> None:
    good = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
    assert is_main_meal_recipe(good)
    good.eligible = False
    assert not is_main_meal_recipe(good)
    good.eligible = True
    good.categories = ["dessert", "vegetable"]
    assert not is_main_meal_recipe(good)
    good.categories = []
    assert not is_main_meal_recipe(good)
    assert non_meal_roles("未知菜", [], "", []) == []


@pytest.mark.parametrize(
    "name,foods,labels,expected",
    [
        ("绿豆汤", "绿豆；水", [], []),
        ("绿豆汤", "绿豆；水；冰糖；盐", [], []),
        ("肉皮冻", "肉皮；盐", [], []),
        ("鸡蛋羹", "鸡蛋；水", ["甜"], []),
        ("无名点心", "面粉", ["甜品风味"], ["dessert"]),
        ("水果冻", "苹果；糖", [], ["dessert"]),
        ("豆浆", "黄豆；水", [], ["drink"]),
        ("果饮", "苹果；水", [], ["drink"]),
        ("发酵面团", "面粉；水", [], ["component"]),
    ],
)
def test_role_boundaries_keep_unknown_or_savory_evidence_distinct(
    name: str, foods: str, labels: list[str], expected: list[str]
) -> None:
    assert non_meal_roles(name, foods.split("；"), "煮熟。", labels) == expected


@pytest.mark.parametrize("location", ["menu", "replacement_suggestions"])
@pytest.mark.parametrize("kind", ["drink", "sweet_soup", "dessert_label", "unknown"])
def test_independent_oracle_audits_source_even_when_response_and_metadata_lie(
    location: str, kind: str
) -> None:
    bad = make_recipe("果蔬汁", "苹果；水", "打浆。")
    if kind == "sweet_soup":
        bad = make_recipe("绿豆汤", "绿豆；冰糖；水", "煮熟。")
    elif kind == "dessert_label":
        bad = make_recipe("小点心", "鸡蛋；面粉", "烤熟。", labels="晚餐、甜品")
    bad.categories = ["vegetable"]
    good = make_recipe("蒸白菜", "白菜", "蒸熟。")

    def item(recipe: Recipe) -> dict[str, Any]:
        return {
            "recipe_id": recipe.recipe_id,
            "name": recipe.name,
            "provenance": recipe_provenance(recipe).model_dump(),
        }

    result: dict[str, Any] = {
        "menu": [item(bad)],
        "replacement_suggestions": [],
        "conversation_state": {"constraints": {"meal_type": "晚餐"}},
        "diner_suitability": [{"hard_constraints_satisfied": True}],
    }
    if location == "replacement_suggestions":
        result["menu"] = [item(good)]
        result["replacement_suggestions"] = [item(bad)]
    catalog = {r.recipe_id: r for r in [bad, good] if kind != "unknown" or r is good}
    checks = evaluate_turn(
        result, {"catalog_traceability": True}, previous_menu_ids=None, recipes=catalog
    )
    check = next(c for c in checks if c["check"] == "main_meal_eligibility")
    assert not check["passed"]
    assert check["actual"][0]["location"] == location


@pytest.mark.parametrize(
    "name,foods,expected",
    [
        ("红豆汤", "红豆；水", []),
        ("红豆汤", "红豆；白糖；盐", []),
        ("玉米排骨汤", "玉米；排骨", []),
        ("鲜果汁", "苹果", ["beverage_name:鲜果汁"]),
        ("柠檬百香果蜜", "柠檬；糖", ["known_component:柠檬百香果蜜"]),
    ],
)
def test_frozen_oracle_has_explicit_finite_boundaries(
    name: str, foods: str, expected: list[str]
) -> None:
    assert main_meal_findings(name, foods) == expected


@pytest.mark.parametrize("valid_meal", [False, True])
def test_regression_report_does_not_measure_quality_for_a_known_drink_menu(
    valid_meal: bool,
) -> None:
    recipe = (
        make_recipe("蒸白菜", "白菜100克", "蒸熟。")
        if valid_meal
        else make_recipe("果蔬汁", "胡萝卜100克；水200克", "打浆。")
    )
    result: dict[str, Any] = {
        "status": "ok",
        "reason": "已按当前确认的饮食要求安排本餐。",
        "menu": [
            {
                "recipe_id": recipe.recipe_id,
                "name": recipe.name,
                "provenance": recipe_provenance(recipe).model_dump(),
            }
        ],
        "replacement_suggestions": [],
        "conversation_state": {"session_id": "role-quality"},
    }
    suite = SuiteDefinition(
        schema_version="2.0",
        dataset_version="role-fixture-v1",
        data_scope="synthetic",
        description="annotated response, not a model evaluation",
        cases=(
            CaseDefinition(
                case_id="role-quality",
                rubric="basic",
                user_id=900001,
                tags=(),
                measure_performance=False,
                turns=(TurnDefinition(message="晚餐", expect={"catalog_traceability": True}),),
            ),
        ),
    )

    def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=result)

    cases, _ = run_functional_cases(
        suite,
        base_url="http://test",
        timeout_seconds=1,
        transport=httpx.MockTransport(response),
        recipes={recipe.recipe_id: recipe},
    )
    assert cases[0]["passed"] is valid_meal
    observation = cases[0]["turns"][0]["menu_quality"]
    assert observation["status"] == ("available" if valid_meal else "unavailable")
    if not valid_meal:
        assert observation["reason"] == "menu_validation_failed"


class DinnerLLM(BaseLLM):
    """Annotated intents isolate execution from real model comprehension."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if message == "解释旧菜单":
            return Intent(action="explain")
        return Intent(people=1, meal_type="晚餐", dish_count=1, restrictions_confirmed=True)

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return list(facts)

    async def aclose(self) -> None:
        pass


@pytest.mark.parametrize("has_entree", [False, True])
def test_http_excludes_stale_drink_categories_and_does_not_fabricate_meal(
    tmp_path: Path, has_entree: bool
) -> None:
    bad = make_recipe("黄瓜柠檬水", "黄瓜50克；柠檬20克；水200克", "过滤饮用。")
    bad.categories = ["vegetable"]
    recipes = {bad.recipe_id: bad}
    if has_entree:
        good = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
        recipes[good.recipe_id] = good
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    store = SessionStore(settings.database_path)
    app = create_app(
        settings, DinnerLLM(), DataCatalog({profile.user_id: profile}, recipes, {}), store
    )
    with TestClient(app) as client:
        result = client.post(
            "/chat", json={"user_id": profile.user_id, "message": "1人晚餐安排1道菜，无其他忌口。"}
        ).json()
    assert result["status"] == ("ok" if has_entree else "no_feasible_menu")
    assert bad.recipe_id not in {r["recipe_id"] for r in result["menu"]}
    assert result["replacement_suggestions"] == []


def test_explain_does_not_republish_a_legacy_drink_menu(tmp_path: Path) -> None:
    good = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
    bad = make_recipe("果蔬汁", "胡萝卜100克；水200克", "打浆。")
    bad.categories = ["vegetable"]
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    store = SessionStore(settings.database_path)
    app = create_app(
        settings,
        DinnerLLM(),
        DataCatalog({profile.user_id: profile}, {r.recipe_id: r for r in [good, bad]}, {}),
        store,
    )
    with TestClient(app) as client:
        first = client.post(
            "/chat", json={"user_id": profile.user_id, "message": "1人晚餐安排1道菜，无其他忌口。"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        state = store.get(sid, profile.user_id)
        assert state is not None
        state.menu_ids = [bad.recipe_id]
        store.save(state, state.revision)
        result = client.post(
            "/chat", json={"user_id": profile.user_id, "session_id": sid, "message": "解释旧菜单"}
        ).json()
    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert result["conversation_state"]["menu_valid"] is False


@pytest.mark.parametrize("has_entree", [False, True])
def test_sse_does_not_stream_drink_as_a_dinner_dish(tmp_path: Path, has_entree: bool) -> None:
    bad = make_recipe("果蔬汁", "胡萝卜100克；水200克", "打浆。")
    bad.categories = ["vegetable"]
    recipes = {bad.recipe_id: bad}
    if has_entree:
        good = make_recipe("蒸白菜", "白菜100克", "蒸熟。")
        recipes[good.recipe_id] = good
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
    )
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    app = create_app(
        settings,
        DinnerLLM(),
        DataCatalog({profile.user_id: profile}, recipes, {}),
        SessionStore(settings.database_path),
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": str(profile.user_id),
                "messages": [{"role": "user", "content": "1人晚餐1道菜，无其他忌口。"}],
                "stream": True,
            },
        )
    assert response.status_code == 200
    lines = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert lines[-1] == "[DONE]"
    content = "".join(
        json.loads(line)["choices"][0]["delta"].get("content", "") for line in lines[:-1]
    )
    assert "果蔬汁" not in content
    assert ("蒸白菜" in content) if has_entree else ("无法组成" in content)
