"""Explicit exclusions use source metadata, never missing tags as proof."""

import pytest
from fastapi.testclient import TestClient

from app.agent.planner import MenuPlanner
from app.agent.scene_preferences import scene_warnings
from app.agent.scene_retraction import apply_scene_withdrawals, scene_withdrawals
from app.agent.suggestions import replacement_candidates
from app.domain.context_exclusions import context_exclusion_hits
from app.domain.meal_context import negative_meal_request_clauses
from app.domain.models import Constraints, Intent, SessionState, UserProfile
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine
from tests.test_component_slots import catalog
from tests.test_flavor_matching_integration import FlavorLLM, dish, make_app
from tests.test_scene_retraction import state as owned_state


@pytest.mark.parametrize(
    "name,label,preference",
    [
        ("便当炒白菜", "晚餐", "不要便当"),
        ("炒白菜", "晚餐、便当", "不要便当"),
        ("家常炒白菜", "晚餐", "不要家常"),
        ("炒白菜", "晚餐、夜宵", "不要夜宵"),
    ],
)
def test_known_source_exclusion_reaches_rules_retrieval_and_menu(name, label, preference):
    rejected = dish(name, "白菜200克；盐1克", label)
    neutral = dish("清炒菠菜", "菠菜200克；盐1克", "晚餐")
    constraints = Constraints(dish_count=1, preferences=[preference])
    rules = RuleEngine()
    assert not rules.evaluate(rejected, constraints).allowed
    assert rules.evaluate(neutral, constraints).allowed
    assert KeywordRetriever([rejected, neutral]).search([], constraints) == [neutral]
    result = MenuPlanner(rules).plan([rejected, neutral], constraints, current=[rejected])
    assert result.failure is None and result.recipes == [neutral]
    assert any("排除" in warning and "不" in warning for warning in result.warnings)


@pytest.mark.parametrize("preference", ["不要便当", "不要夜宵"])
def test_optional_suggestions_cannot_restore_excluded_source_without_rules(preference):
    current = dish("清炒菠菜", "菠菜200克；盐1克", "晚餐")
    rejected = dish("便当炒白菜", "白菜200克；盐1克", "晚餐、夜宵")
    constraints = Constraints(dish_count=1, preferences=[preference])
    assert replacement_candidates([current], [rejected], "bounded", constraints=constraints) == []


def test_local_exclusion_cannot_authorize_changes_outside_target_slot():
    first = dish("便当炒白菜", "白菜200克；盐1克", "晚餐")
    second = dish("炒菠菜", "菠菜200克；盐1克", "晚餐")
    third = dish("炒青菜", "青菜200克；盐1克", "晚餐")
    fourth = dish("炒西兰花", "西兰花200克；盐1克", "晚餐")
    result = MenuPlanner(RuleEngine()).plan(
        [first, second, third, fourth],
        Constraints(dish_count=2, preferences=["不要便当"]),
        current=[first, second],
        replace_slot=2,
    )
    assert result.failure and "其他菜位" in result.failure and not result.recipes


def test_polarity_conflict_is_not_a_successful_menu_with_only_a_warning():
    item = dish("便当炒白菜", "白菜200克；盐1克", "晚餐")
    result = MenuPlanner(RuleEngine()).plan(
        [item],
        Constraints(dish_count=1, preferences=["便当", "不要便当"]),
    )
    assert result.failure and "冲突" in result.failure and not result.recipes


class ContextLLM(FlavorLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=1,
            soup_count=0,
            restrictions_confirmed=True,
            query_terms=["便当"],
        )


def test_http_literal_negative_reaches_menu_and_survives_restart(tmp_path):
    old = dish("便当炒白菜", "白菜200克；盐1克", "晚餐、夜宵")
    neutral = dish("清炒菠菜", "菠菜200克；盐1克", "晚餐")
    with TestClient(make_app(tmp_path, (old, neutral), ContextLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道菜，无其他忌口。"}
        ).json()
        assert first["menu"][0]["recipe_id"] == old.recipe_id
        sid = first["conversation_state"]["session_id"]
        changed = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": sid,
                "message": "这餐不要便当，也不要夜宵风格，可以调整。",
            },
        ).json()
    assert changed["status"] == "ok" and changed["menu"][0]["recipe_id"] == neutral.recipe_id
    preferences = changed["conversation_state"]["constraints"]["preferences"]
    assert "不要便当" in preferences and "不要夜宵" in preferences
    assert changed["conversation_state"]["constraints"]["meal_type"] == "晚餐"
    assert "未知" in changed["reason"] and "排除" in changed["reason"]
    with TestClient(make_app(tmp_path, (old, neutral), ContextLLM())) as client:
        continued = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
        ).json()
    assert continued["menu"][0]["recipe_id"] == neutral.recipe_id


@pytest.mark.parametrize(
    "message",
    [
        "夜宵",
        "妈妈不要夜宵",
        "如果不要夜宵",
        "解释不要夜宵",
        "不要夜宵是什么意思？",
        "不是不要夜宵",
        "取消不要夜宵要求",
        "他说‘不要夜宵’",
        "夜宵之后不吃辣",
    ],
)
def test_meal_mentions_and_owned_statements_are_not_unowned_exclusions(message):
    assert negative_meal_request_clauses(message) == ()


@pytest.mark.parametrize(
    "message,expected",
    [
        ("这餐不要夜宵风格", ("不要夜宵",)),
        ("不要早餐，但是不要下午茶", ("不要早餐", "不要下午茶")),
        ("也不要夜宵", ("不要夜宵",)),
    ],
)
def test_negative_meals_are_distinct_from_positive_meal_type(message, expected):
    assert negative_meal_request_clauses(message) == expected


@pytest.mark.parametrize(
    "name,label",
    [
        ("便当盒炒白菜", "晚餐"),
        ("非宴客炒白菜", "晚餐"),
        ("清炒菠菜", "晚餐、非便当"),
        ("炒白菜", "晚餐、非夜宵"),
    ],
)
def test_negated_and_container_source_is_not_positive_exclusion_evidence(name, label):
    item = dish(name, "白菜200克；盐1克", label)
    assert not context_exclusion_hits(
        item, Constraints(preferences=["不要便当", "不要宴客", "不要夜宵"])
    )


@pytest.mark.parametrize("raw_label", ["", "晚餐"])
def test_cached_scene_and_meal_tags_cannot_create_negative_source_proof(raw_label):
    item = dish("炒白菜", "白菜200克；盐1克", raw_label).model_copy(
        update={"labels": ["便当"], "meal_types": ["夜宵"]},
    )
    assert not context_exclusion_hits(item, Constraints(preferences=["不要便当", "不要夜宵"]))


def test_assistant_bento_compatibility_is_not_a_forbidden_source_identity():
    item = catalog()[356]
    before = item.model_dump_json()
    constraints = Constraints(preferences=["不要便当"], dish_count=1)
    assert not context_exclusion_hits(item, constraints)
    assert RuleEngine().evaluate(item, constraints).allowed
    assert KeywordRetriever([item]).search([], constraints) == [item]
    warnings = "\n".join(scene_warnings([item], constraints))
    assert "未知" in warnings and "助手" in warnings and "不保证" in warnings
    assert item.model_dump_json() == before


@pytest.mark.parametrize(
    "constraints", [Constraints(allergies=["花生"]), Constraints(no_spicy=True)]
)
def test_scene_replacement_does_not_waive_hard_food_rejections(constraints):
    old = dish("便当炒白菜", "白菜200克；盐1克", "晚餐")
    bad = dish("清炒青菜", "青菜200克；花生油5克；辣椒3克；盐1克", "晚餐")
    constraints = constraints.model_copy(update={"dish_count": 1, "preferences": ["不要便当"]})
    result = MenuPlanner(RuleEngine()).plan([old, bad], constraints, current=[old])
    assert result.failure and not result.recipes


def test_night_exclusion_withdrawal_is_source_scoped_not_meal_switch_or_all_people():
    current = owned_state(["不要夜宵，清淡，不吃辣"], ["不要夜宵"])
    before_diners = [d.model_dump() for d in current.diners]
    _, issue = apply_scene_withdrawals(current, Intent(), "取消本餐的不要夜宵要求")
    assert issue is None and current.meal_constraints is not None
    assert current.meal_constraints.preferences == ["清淡，不吃辣"]
    assert [d.model_dump() for d in current.diners] == before_diners
    assert current.constraints.meal_type == "晚餐"
    assert "不要夜宵" in current.constraints.preferences
    assert current.constraints.no_spicy and "虾" in current.constraints.allergies
    assert not scene_withdrawals("取消晚餐要求")


def test_http_conflict_needs_explicit_source_withdrawal(tmp_path):
    class PositiveLLM(ContextLLM):
        async def parse(self, message, state, profile):
            intent = await super().parse(message, state, profile)
            return (
                intent.model_copy(update={"preferences": ["便当"]})
                if not state.menu_ids
                else intent
            )

    old = dish("便当炒白菜", "白菜200克；盐1克", "晚餐")
    neutral = dish("清炒菠菜", "菠菜200克；盐1克", "晚餐")
    with TestClient(make_app(tmp_path, (old, neutral), PositiveLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道便当菜，无其他忌口。"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        conflict = client.post(
            "/chat",
            json={"user_id": 900001, "session_id": sid, "message": "这餐不要便当，可以调整。"},
        ).json()
        assert conflict["status"] == "clarification_required" and conflict["menu"] == []
        assert conflict["conversation_state"]["constraints"]["preferences"] == ["便当", "不要便当"]
        retry = client.post(
            "/chat", json={"user_id": 900001, "session_id": sid, "message": "继续"}
        ).json()
        assert retry["status"] == "clarification_required" and retry["menu"] == []
        resolved = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": sid,
                "message": "取消本餐的便当要求，可以调整。",
            },
        ).json()
    assert resolved["status"] == "ok" and resolved["menu"][0]["recipe_id"] == neutral.recipe_id
    assert resolved["conversation_state"]["constraints"]["preferences"] == ["不要便当"]


def test_http_model_negative_meal_mention_cannot_change_current_meal(tmp_path):
    class MistakenMealLLM(ContextLLM):
        async def parse(self, message, state, profile):
            intent = await super().parse(message, state, profile)
            return (
                intent.model_copy(update={"meal_type": "夜宵"}) if "不要夜宵" in message else intent
            )

    night = dish("炒白菜", "白菜200克；盐1克", "晚餐、夜宵")
    dinner = dish("清炒菠菜", "菠菜200克；盐1克", "晚餐")
    with TestClient(make_app(tmp_path, (night, dinner), MistakenMealLLM())) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": "1人晚餐1道菜，无其他忌口。"}
        ).json()
        sid = first["conversation_state"]["session_id"]
        after = client.post(
            "/chat",
            json={"user_id": 900001, "session_id": sid, "message": "这餐不要夜宵，可以调整。"},
        ).json()
        assert (
            after["status"] == "ok"
            and after["conversation_state"]["constraints"]["meal_type"] == "晚餐"
        )
        assert after["menu"][0]["recipe_id"] == dinner.recipe_id
        withdrawn = client.post(
            "/chat",
            json={"user_id": 900001, "session_id": sid, "message": "取消本餐的不要夜宵要求。"},
        ).json()
        assert (
            withdrawn["status"] == "ok"
            and not withdrawn["conversation_state"]["constraints"]["preferences"]
        )


def test_explicit_current_meal_and_excluded_meal_conflict_is_not_silent_default():
    item = dish("炒白菜", "白菜200克；盐1克", "夜宵")
    result = MenuPlanner(RuleEngine()).plan(
        [item], Constraints(dish_count=1, meal_type="夜宵", preferences=["不要夜宵"])
    )
    assert result.failure and "冲突" in result.failure and not result.recipes


def test_failed_local_negative_context_remains_local_across_continue_restart(tmp_path):
    class LocalLLM(ContextLLM):
        async def parse(self, message, state, profile):
            if "只换第2道" in message:
                return Intent(action="replace", replace_slot=2)
            return await super().parse(message, state, profile)

    bento = dish("便当炒白菜", "白菜200克；盐1克", "晚餐")
    spinach = dish("炒菠菜", "菠菜200克；盐1克", "晚餐")
    greens = dish("炒青菜", "青菜200克；盐1克", "晚餐")
    broccoli = dish("炒西兰花", "西兰花200克；盐1克", "晚餐")
    from app.domain.models import ContextReplacementScope
    from app.infrastructure.sessions import SessionStore

    app = make_app(tmp_path, (bento, spinach, greens, broccoli), LocalLLM())
    store = SessionStore(tmp_path / "flavor.db")
    seed = owned_state([])
    seed.user_id = 900001
    seed.diners = seed.diners[:1]
    seed.constraints = Constraints(people=1, dish_count=2)
    seed.meal_constraints = seed.constraints.model_copy(deep=True)
    seed.menu_ids = [bento.recipe_id, spinach.recipe_id]
    seed.menu_valid = True
    store.save(seed, None)
    request = {"user_id": 900001, "session_id": seed.session_id}
    with TestClient(app) as client:
        failed = client.post(
            "/chat", json={**request, "message": "只换第2道，这餐不要便当，其他不变。"}
        ).json()
        assert not failed["menu"] and "其他菜位" in failed["reason"]
        assert failed["conversation_state"]["pending_context_replacement"]["replace_slot"] == 2
        continued = client.post("/chat", json={**request, "message": "继续"}).json()
        assert not continued["menu"] and "其他菜位" in continued["reason"]
    with TestClient(make_app(tmp_path, (bento, spinach, greens, broccoli), LocalLLM())) as client:
        restarted = client.post("/chat", json={**request, "message": "继续"}).json()
        assert not restarted["menu"] and "其他菜位" in restarted["reason"]
        allowed = client.post("/chat", json={**request, "message": "允许整餐调整。"}).json()
    assert (
        allowed["status"] == "ok"
        and allowed["conversation_state"]["pending_context_replacement"] is None
    )
    assert bento.recipe_id not in [item["recipe_id"] for item in allowed["menu"]]
    assert ContextReplacementScope(replace_slot=2, menu_ids=seed.menu_ids).replace_slot == 2
