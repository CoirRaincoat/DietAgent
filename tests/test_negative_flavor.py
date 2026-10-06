"""Source exclusion is not sensory certification; no live model calls."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.domain.matching_tags import (
    flavor_conflicts,
    negative_flavor_request_clauses,
    supported_flavor_exclusions,
)
from app.domain.models import Constraints, Intent, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def recipe(name, label="晚餐", foods="白菜200克；盐1克", steps="白菜蒸熟装盘。"):
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": label,
                    }
                ]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "label,name,negative",
    [
        ("晚餐、酸", "醋溜白菜", "不要酸"),
        ("晚餐、酸甜", "双味白菜", "不要酸"),
        ("晚餐", "糖醋白菜", "不要甜"),
        ("晚餐、酸辣", "凉拌白菜", "不喜欢酸"),
        ("晚餐、蒜香", "蒸白菜", "蒜香不要"),
    ],
)
def test_known_source_negative_is_excluded(label, name, negative):
    source = recipe(name, label)
    decision = RuleEngine().evaluate(source, Constraints(preferences=[negative]))
    assert not decision.allowed
    assert any("口味排除" in reason for reason in decision.reasons)


def test_initial_menu_and_new_requirement_do_not_keep_known_sour_source():
    sour, neutral = recipe("醋溜白菜", "晚餐、酸"), recipe("蒸白菜", "晚餐、原味")
    result = MenuPlanner(RuleEngine()).plan(
        [sour, neutral],
        Constraints(dish_count=1, soup_count=0, preferences=["不要酸"]),
        current=[sour],
    )
    assert result.failure is None and result.recipes == [neutral]


def test_only_conflicting_sources_are_not_relaxed_to_fill_menu():
    source = recipe("糖醋白菜")
    result = MenuPlanner(RuleEngine()).plan(
        [source],
        Constraints(dish_count=1, soup_count=0, preferences=["不要酸"]),
    )
    assert result.failure and not result.recipes


def test_negative_source_conflict_outside_local_slot_requires_whole_menu_permission():
    sour, chicken, alternative = (
        recipe("醋溜白菜", "晚餐、酸"),
        recipe("煮鸡肉", foods="鸡肉200克；盐1克", steps="鸡肉煮熟装盘。"),
        recipe("蒸鸡肉", foods="鸡肉200克；盐1克", steps="鸡肉蒸熟装盘。"),
    )
    result = MenuPlanner(RuleEngine()).plan(
        [sour, chicken, alternative, recipe("蒸白菜")],
        Constraints(dish_count=2, soup_count=0, preferences=["不要酸"]),
        current=[sour, chicken],
        replace_slot=2,
    )
    assert result.failure and "其他菜位" in result.failure
    assert not result.recipes


class IgnoringNegativeLLM(BaseLLM):
    async def parse(self, message, state, profile):
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
        )

    async def explain(self, facts):
        return ["opening", "constraints"]

    async def aclose(self):
        pass


def make_app(tmp_path: Path, records, llm=None):
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    settings = Settings.model_construct(session_db=tmp_path / "negative-flavor.db")
    return create_app(
        settings,
        llm or IgnoringNegativeLLM(),
        DataCatalog({900001: profile}, {r.recipe_id: r for r in records}, {}),
        SessionStore(settings.database_path),
    )


def test_http_ground_negative_when_model_omits_it_and_preserve_after_restart(tmp_path):
    sour, unknown = recipe("糖醋白菜"), recipe("蒸白菜")
    with TestClient(make_app(tmp_path, [sour, unknown])) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道菜，无其他忌口。"}
        ).json()
        assert [r["recipe_id"] for r in first["menu"]] == [sour.recipe_id]
        session = first["conversation_state"]["session_id"]
        body = {
            "user_id": 900001,
            "session_id": session,
            "message": "现在不要酸。",
            "request_id": "negative-1",
        }
        updated = client.post("/chat", json=body).json()
        assert updated == client.post("/chat", json=body).json()
    assert updated["status"] == "ok"
    assert [r["recipe_id"] for r in updated["menu"]] == [unknown.recipe_id]
    assert "现在不要酸" in updated["conversation_state"]["constraints"]["preferences"]
    assert "无法核验" in updated["reason"] and "蒸白菜" in updated["reason"]
    assert not any(r["recipe_id"] == sour.recipe_id for r in updated["replacement_suggestions"])
    with TestClient(make_app(tmp_path, [sour, unknown])) as client:
        continued = client.post(
            "/chat", json={"user_id": 900001, "session_id": session, "message": "继续"}
        ).json()
    assert [r["recipe_id"] for r in continued["menu"]] == [unknown.recipe_id]
    assert "无法核验" in continued["reason"]


@pytest.mark.parametrize(
    "message",
    [
        "不要酸？",
        "如果不要酸",
        "例如不要酸",
        "解释不要酸是什么意思",
        "取消不要酸",
        "妈妈不要酸",
        "他不要酸",
        "“不要酸”",
        "不是不喜欢酸",
        "没有不喜欢酸",
        "酸菜不要",
        "不要五香粉",
    ],
)
def test_question_attribution_double_negation_and_food_names_not_shared_requests(message):
    assert negative_flavor_request_clauses(message) == ()


@pytest.mark.parametrize(
    "reference,excluded,blocked",
    [
        ("酸甜", "酸", True),
        ("酸甜", "酸甜", True),
        ("酸", "酸甜", False),
        ("甜", "酸甜", False),
        ("奶香", "甜", False),
        ("清淡", "咸", False),
        ("咸香", "咸", True),
        ("酸辣", "辣", True),
    ],
)
def test_compound_exclusion_is_directional_not_arbitrary_taste_inference(
    reference, excluded, blocked
):
    source = recipe("来源白菜", "晚餐、" + reference)
    assert (
        RuleEngine().evaluate(source, Constraints(preferences=["不要" + excluded])).allowed
        != blocked
    )


def test_compound_positive_conflict_detected_but_acid_only_not_sour_sweet():
    assert flavor_conflicts(["酸甜", "不要酸"]) == ("酸甜",)
    assert flavor_conflicts(["酸", "不要酸甜"]) == ()
    assert supported_flavor_exclusions(["不要甜但喜欢酸"]) == ("甜",)


def test_cached_flavor_and_garlic_cue_not_negative_sensory_proof():
    unknown = recipe("蒸白菜", foods="白菜200克；大蒜2克；盐1克")
    cached = unknown.model_copy(update={"labels": ["酸", "蒜香"]})
    assert RuleEngine().evaluate(cached, Constraints(preferences=["不要酸", "不要蒜香"])).allowed


def test_negative_plain_does_not_earn_positive_health_or_method_credit():
    steam = recipe("蒸白菜")
    constraints = Constraints(preferences=["不要清淡"])
    assert RuleEngine().soft_goal_scores(steam, constraints) == ()
    assert RuleEngine().evaluate(steam, constraints).score == 0


def test_negative_source_does_not_reappear_as_optional_suggestion():
    neutral, sour = recipe("蒸白菜"), recipe("糖醋白菜")
    assert (
        replacement_candidates(
            [neutral], [sour], "synthetic", constraints=Constraints(preferences=["不要酸"])
        )
        == []
    )


def test_filter_keeps_allergy_and_spicy_hard_gates():
    source = recipe("原味白菜", "晚餐、原味", foods="白菜200克；花生油2克；辣椒2克")
    decision = RuleEngine().evaluate(
        source, Constraints(preferences=["不要酸"], allergies=["花生"], no_spicy=True)
    )
    assert not decision.allowed
    assert any("过敏" in reason for reason in decision.reasons)
    assert any("辣" in reason for reason in decision.reasons)


def test_strict_non_spicy_request_uses_ingredient_gate_even_without_spicy_label(tmp_path):
    spicy = recipe("川北凉粉", foods="豌豆粉200克；辣椒2克；盐1克", steps="凉粉装盘，加入辣椒。")
    neutral = recipe("蒸白菜")
    with TestClient(make_app(tmp_path, [spicy, neutral])) as client:
        result = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道菜，不辣，无其他忌口。"}
        ).json()
    assert result["status"] == "ok" and result["conversation_state"]["constraints"]["no_spicy"]
    assert all(r["recipe_id"] != spicy.recipe_id for r in result["menu"])


def test_accumulated_conflict_stays_unresolved_on_continue_and_restart(tmp_path):
    class ConflictLLM(IgnoringNegativeLLM):
        async def parse(self, message, state, profile):
            intent = await super().parse(message, state, profile)
            if not state.menu_ids:
                return intent.model_copy(update={"preferences": ["酸甜"]})
            return intent

    records = [recipe("糖醋白菜"), recipe("蒸白菜")]
    with TestClient(make_app(tmp_path, records, ConflictLLM())) as client:
        initial = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道菜，想吃酸甜，无其他忌口。"}
        ).json()
        session = initial["conversation_state"]["session_id"]
        failed = client.post(
            "/chat", json={"user_id": 900001, "session_id": session, "message": "现在不要酸。"}
        ).json()
    with TestClient(make_app(tmp_path, records, ConflictLLM())) as client:
        continued = client.post(
            "/chat", json={"user_id": 900001, "session_id": session, "message": "继续"}
        ).json()
    for result in (failed, continued):
        assert result["status"] == "clarification_required" and result["menu"] == []
        assert "酸甜" in result["conversation_state"]["constraints"]["preferences"]
        assert "现在不要酸" in result["conversation_state"]["constraints"]["preferences"]
        assert "冲突" in result["reason"]
