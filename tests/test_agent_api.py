import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.openai_compat import ChatCompletionsRequest, OpenAIRequestError
from app.domain.models import (
    ChatResult,
    DinerUpdate,
    Ingredient,
    Intent,
    MenuItem,
    Recipe,
    SessionState,
    UserProfile,
)
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM, LLMOutputError, LLMUnavailable
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings


def complete_intent(**changes):
    """Explicit test meal context; separate tests cover missing-context behavior."""
    fields = {"people": 1, "meal_type": "晚餐", "restrictions_confirmed": True}
    fields.update(changes)
    return Intent(**fields)


class ScriptedLLM(BaseLLM):
    """Test-only adapter. Production always uses the DeepSeek adapter."""

    def __init__(self, intents=None):
        self.intents = list(intents or [complete_intent()])
        self.parse_calls = 0
        self.parse_error = None
        self.explain_error = None

    async def parse(self, message, state, profile):
        self.parse_calls += 1
        if self.parse_error:
            raise self.parse_error
        return self.intents.pop(0) if self.intents else Intent()

    async def explain(self, facts):
        if self.explain_error:
            raise self.explain_error
        return list(facts)

    async def aclose(self):
        pass


@pytest.fixture
def catalog():
    profile = UserProfile(
        data_scope="synthetic", user_id=3, age=30, sex="女", height_cm=165, weight_kg=55, bmi=20.2,
        health_goals=["增肌"],
    )
    raw = [
        ("蒸鸡蛋", ["鸡蛋", "水", "盐"], ["protein"]),
        ("蒸鸡肉", ["鸡胸肉", "水", "盐"], ["protein"]),
        ("蒸鱼", ["鲈鱼", "水", "盐"], ["protein"]),
        ("清炒西兰花", ["西兰花", "油", "盐"], ["vegetable"]),
        ("蒸南瓜", ["南瓜", "水"], ["vegetable"]),
        ("炒青菜", ["青菜", "油"], ["vegetable"]),
        ("米饭", ["大米", "水"], ["staple"]),
        ("小米饭", ["小米", "水"], ["staple"]),
        ("花生粥", ["花生", "大米", "水"], ["staple"]),
        ("虾仁豆腐", ["虾仁", "豆腐", "水"], ["protein"]),
        ("萝卜汤", ["萝卜", "水", "盐"], ["soup", "vegetable"]),
    ]
    recipes = {}
    for index, (name, ingredients, categories) in enumerate(raw):
        key = f"test_{index}"
        recipes[key] = Recipe(
            recipe_id=key, name=name, raw_ingredients="；".join(ingredients),
            ingredients=[Ingredient(raw=x, name=x) for x in ingredients],
            steps="将食材蒸熟后装盘。", categories=categories, methods=["蒸"],
            meal_types=["晚餐"], source_row=index + 2, fingerprint=key,
        )
    return DataCatalog(profiles={3: profile, 4: profile.model_copy(update={"user_id": 4})},
                       recipes=recipes, quality_report={})


def client_for(tmp_path, catalog, llm):
    settings = Settings(_env_file=None, deepseek_api_key="", session_db=tmp_path / "state.db")
    return TestClient(create_app(settings, llm, catalog, SessionStore(settings.database_path)))


def test_full_menu_tool_calls_and_replacement_preserves_other_slots(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(), Intent(action="replace", replace_slot=2)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        assert first["status"] == "ok"
        assert len(first["menu"]) == 3
        assert "menu_balance" not in first
        assert "搭配上包含" in first["reason"]
        assert "蔬菜类菜" in first["reason"]
        assert "recipe_id" not in first["reason"]
        # A configured goal now has mandatory evidence and limitations, even
        # when the model selects only an opening. No intake success is claimed.
        assert "未计算蛋白质克数或增肌效果" in first["reason"]
        assert "营养达标评分" in first["reason"]
        assert "工程评分" not in first["reason"]
        assert "/100" not in first["reason"]
        assert all(item["recipe_id"] in catalog.recipes for item in first["menu"])
        assert {tool["name"] for tool in first["tool_calls"]} >= {
            "recipe_search", "health_check", "menu_modify", "nutrition_analysis",
        }
        second = client.post("/chat", json={
            "user_id": 3, "message": "只换第二道", "session_id": first["conversation_state"]["session_id"]
        }).json()
        assert second["status"] == "ok"
        old, new = [x["recipe_id"] for x in first["menu"]], [x["recipe_id"] for x in second["menu"]]
        assert old[0] == new[0] and old[2] == new[2] and old[1] != new[1]
        assert "只将第 2 道" in second["reason"]
        assert "其他菜保持不变" in second["reason"]
        assert second["conversation_state"]["revision"] == 2


def test_added_restrictions_survive_multiple_rounds_and_restart(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(allergies=["海鲜"], excluded_ingredients=["花生"], no_spicy=True),
        Intent(action="replace", replace_slot=1),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，海鲜过敏，不要花生和辣"}).json()
        assert first["status"] == "ok"
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={"user_id": 3, "message": "换第一道", "session_id": sid}).json()
        assert second["status"] == "ok"
        constraints = second["conversation_state"]["constraints"]
        assert constraints["allergies"] == ["海鲜"]
        assert constraints["excluded_ingredients"] == ["花生"]
        assert constraints["no_spicy"] is True
        assert not any("虾" in ingredient or "花生" in ingredient or "鲈鱼" in ingredient
                       for dish in second["menu"] for ingredient in dish["ingredients"])
    reloaded = SessionStore(tmp_path / "state.db").get(sid, 3)
    assert reloaded.constraints.allergies == ["海鲜"]
    assert reloaded.menu_valid


def test_profile_allergies_cannot_be_removed_by_later_empty_intent(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋"]
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        result = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
    assert result["status"] == "ok"
    assert "鸡蛋" in result["conversation_state"]["constraints"]["allergies"]
    assert all("鸡蛋" not in item["ingredients"] for item in result["menu"])


def test_completed_request_is_idempotent_and_old_replay_is_rejected(tmp_path, catalog):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口", "request_id": "r1"}).json()
        sid = first["conversation_state"]["session_id"]
        request = {"user_id": 3, "message": "1人晚餐，没有其他忌口", "request_id": "r1", "session_id": sid}
        assert client.post("/chat", json=request).json() == first
        assert llm.parse_calls == 1
        assert client.post("/chat", json={**request, "message": "不同内容"}).status_code == 409
        assert client.post("/chat", json={
            "user_id": 3, "message": "再看看", "session_id": sid
        }).status_code == 200
        assert client.post("/chat", json=request).status_code == 409


def test_user_and_session_isolation_and_input_validation(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        sid = first["conversation_state"]["session_id"]
        assert client.post("/chat", json={"user_id": 4, "message": "查看", "session_id": sid}).status_code == 409
        second = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        assert second["conversation_state"]["session_id"] != sid
        assert client.post("/chat", json={"user_id": 999, "message": "1人晚餐，没有其他忌口"}).status_code == 404
        assert client.post("/chat", json={"user_id": 3, "message": " "}).status_code == 422
        assert client.post("/chat", json={"user_id": 3, "message": "查看", "session_id": "a"*32}).status_code == 404


def test_time_limit_clarification_and_explicit_relaxation(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(max_minutes=30), Intent(clear_time_limit=True)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口，30分钟内"}).json()
        assert first["status"] == "clarification_required"
        assert first["menu"] == [] and not first["conversation_state"]["menu_valid"]
        second = client.post("/chat", json={
            "user_id": 3, "message": "取消时间限制", "session_id": first["conversation_state"]["session_id"]
        }).json()
        assert second["status"] == "ok"
        assert second["conversation_state"]["constraints"]["max_minutes"] is None


def test_no_safe_candidates_never_fabricates_a_menu(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(inventory=[])])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口，没有食材"}).json()
        assert result["status"] == "no_feasible_menu"
        assert result["menu"] == []


def test_explanation_failure_uses_verified_facts(tmp_path, catalog):
    llm = ScriptedLLM()
    llm.explain_error = LLMUnavailable("timeout")
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        assert result["status"] == "ok"
        assert result["explanation_source"] == "verified_template"
        assert "未计算" in result["reason"]
        assert "搭配上包含" in result["reason"]
        assert "recipe_id" not in result["reason"]


@pytest.mark.parametrize("error,status", [(LLMUnavailable("timeout"), 503), (LLMOutputError(), 502),
                                          (LLMUnavailable("original_profile_blocked"), 403)])
def test_parser_failure_returns_safe_error_and_no_fake_success(tmp_path, catalog, error, status):
    llm = ScriptedLLM()
    llm.parse_error = error
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"})
        assert response.status_code == status
        assert "menu" not in response.json()
        assert client.get("/health").status_code == 200

def test_first_request_retry_without_session_does_not_create_another_session(tmp_path, catalog):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        payload = {"user_id": 3, "message": "1人晚餐，没有其他忌口", "request_id": "first-retry"}
        first = client.post("/chat", json=payload).json()
        second = client.post("/chat", json=payload).json()
        assert first == second
        assert llm.parse_calls == 1


def test_unresolved_allergy_blocks_later_vague_requests(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(action="clarify", clarification="请明确过敏食材"),
        Intent(),
        Intent(allergies=["海鲜"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，对一些东西过敏"}).json()
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={"user_id": 3, "message": "随便推荐", "session_id": sid}).json()
        assert second["status"] == "clarification_required"
        assert second["menu"] == []
        assert second["conversation_state"]["pending_allergy"]
        assert not second["tool_calls"]
        third = client.post("/chat", json={"user_id": 3, "message": "海鲜过敏", "session_id": sid}).json()
        assert third["status"] == "ok"
        assert not third["conversation_state"]["pending_allergy"]


def test_explanation_never_calls_replanning_tools(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        second = client.post("/chat", json={
            "user_id": 3, "message": "解释这份菜单", "session_id": first["conversation_state"]["session_id"]
        }).json()
        assert second["status"] == "ok"
        assert [x["recipe_id"] for x in first["menu"]] == [x["recipe_id"] for x in second["menu"]]
        assert "menu_modify" not in [x["name"] for x in second["tool_calls"]]


def test_proactive_clarification_collects_only_missing_context(tmp_path, catalog):
    llm = ScriptedLLM([
        Intent(), Intent(people=2), Intent(meal_type="午餐"),
        Intent(restrictions_confirmed=True), Intent(restrictions_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "推荐一餐"}).json()
        assert first["status"] == "clarification_required"
        assert first["menu"] == [] and first["tool_calls"] == []
        assert [q["field"] for q in first["clarification_questions"]] == [
            "people", "meal_type", "restrictions",
        ]
        assert "人数待确认" in first["constraints"][0]
        sid = first["conversation_state"]["session_id"]
        def ask(message):
            return client.post("/chat", json={"user_id": 3, "message": message, "session_id": sid}).json()
        second = ask("2个人")
        assert [q["field"] for q in second["clarification_questions"]] == ["meal_type", "restrictions"]
        third = ask("午餐")
        assert [q["field"] for q in third["clarification_questions"]] == ["restrictions"]
        vague = ask("随便")
        assert vague["status"] == "clarification_required" and vague["tool_calls"] == []
        final = ask("没有")
        assert final["status"] == "ok"
        assert final["conversation_state"]["constraints"]["people"] == 2
        assert final["conversation_state"]["constraints"]["meal_type"] == "午餐"
        assert final["conversation_state"]["pending_fields"] == []
        assert final["clarification_questions"] == []
    restored = SessionStore(tmp_path / "state.db").get(sid, 3)
    assert restored.confirmed_fields == ["people", "meal_type", "restrictions"]


def test_existing_profile_allergy_counts_as_known_restriction(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋"]
    llm = ScriptedLLM([Intent(), Intent(people=1, meal_type="晚餐")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "推荐一餐"}).json()
        assert [q["field"] for q in first["clarification_questions"]] == ["people", "meal_type"]
        final = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐", "session_id": first["conversation_state"]["session_id"],
        }).json()
        assert final["status"] == "ok"
        assert all("鸡蛋" not in item["ingredients"] for item in final["menu"])


def test_unknown_allergen_can_be_clarified_without_erasing_known_allergy(tmp_path, catalog):
    catalog.profiles[3].allergies = ["鸡蛋"]
    llm = ScriptedLLM([
        complete_intent(allergies=["某种调料"]),
        Intent(allergies=["芝麻"], allergy_clarifications={"某种调料": ["芝麻"]}),
        Intent(restrictions_confirmed=True),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，对某种调料过敏"}).json()
        assert first["status"] == "clarification_required"
        assert first["conversation_state"]["pending_allergy_terms"] == ["某种调料"]
        sid = first["conversation_state"]["session_id"]
        second = client.post("/chat", json={"user_id": 3, "message": "具体是芝麻", "session_id": sid}).json()
        assert second["status"] == "ok"
        assert second["conversation_state"]["pending_allergy_terms"] == []
        assert set(second["conversation_state"]["constraints"]["allergies"]) == {"鸡蛋", "芝麻"}
        final = client.post("/chat", json={"user_id": 3, "message": "没有其他过敏", "session_id": sid}).json()
        assert final["status"] == "ok"
        assert not final["conversation_state"]["pending_allergy"]
        assert set(final["conversation_state"]["constraints"]["allergies"]) == {"鸡蛋", "芝麻"}
        assert {event["name"] for event in final["tool_calls"]}.issubset({
            "recipe_search", "health_check", "menu_modify", "nutrition_analysis",
        })


def test_demo_response_cards_and_nutrition_are_source_traceable(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        result = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，没有其他忌口",
        }).json()
        assert result["status"] == "ok" and result["schema_version"] == "2.0"
        analysis = result["nutrition_analysis"]
        assert analysis["recipe_ids"] == [item["recipe_id"] for item in result["menu"]]
        for item in result["menu"] + result["replacement_suggestions"]:
            source = catalog.recipes[item["recipe_id"]]
            assert item["card"]["title"] == source.name
            assert all(item["card"][field] is None for field in ("image_url", "cooking_minutes", "servings"))
            assert item["ingredient_details"] == [x.model_dump() for x in source.ingredients]
            assert [step["description"] for step in item["cooking_steps"]] == source.steps.splitlines()
            assert item["provenance"]["fingerprint"] == source.fingerprint
            assert item["nutrition"]["recipe_id"] == source.recipe_id
            assert all(contribution["ingredient_name"] in item["ingredients"]
                       for contribution in item["nutrition"]["ingredient_contributions"])
        for suggestion in result["replacement_suggestions"]:
            assert suggestion["slot"] == 1 and suggestion["replacement_reason"]
            assert suggestion["name"] not in {item["name"] for item in result["menu"]}
        assert {x["user_id"] for x in client.get("/demo/profiles").json()["profiles"]} == {3, 4}


def test_demo_profile_listing_never_exposes_original_health_fields(tmp_path, catalog):
    catalog.profiles[3].data_scope = "original"
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        response = client.get("/demo/profiles").json()
        assert [item["user_id"] for item in response["profiles"]] == [4]


def test_additional_allergies_never_clear_unresolved_terms_by_count(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(allergies=["不明调料", "不明水果"]),
        Intent(allergies=["花生", "鸡蛋"]),
        Intent(allergies=["芝麻"], allergy_clarifications={"不明调料": ["芝麻"]}),
        Intent(allergies=["桃"], allergy_clarifications={"不明水果": ["桃"]}),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，对不明调料和不明水果过敏"}).json()
        sid = first["conversation_state"]["session_id"]
        def ask(message):
            return client.post("/chat", json={"user_id": 3, "message": message, "session_id": sid}).json()
        extra = ask("另外花生和鸡蛋也过敏")
        assert extra["status"] == "clarification_required" and extra["tool_calls"] == []
        assert extra["conversation_state"]["pending_allergy_terms"] == ["不明调料", "不明水果"]
        partial = ask("原来说的不明调料具体是芝麻")
        assert partial["status"] == "clarification_required"
        assert partial["conversation_state"]["pending_allergy_terms"] == ["不明水果"]
        final = ask("不明水果具体是桃")
        assert final["status"] == "ok"
        assert final["conversation_state"]["pending_allergy_terms"] == []
        assert set(final["conversation_state"]["constraints"]["allergies"]) == {"花生", "鸡蛋", "芝麻", "桃"}


def test_multi_diner_shared_constraints_and_attendance_changes(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(
            people=3,
            diner_updates=[
                DinerUpdate(
                    diner="爸爸", aliases=["我爸"], attendance=True, allergies=["花生"]
                ),
                DinerUpdate(
                    diner="妈妈", aliases=["我妈"], attendance=True, no_spicy=True
                ),
            ],
        ),
        Intent(diner_updates=[DinerUpdate(diner="我爸", attendance=False)]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post(
            "/chat",
            json={"user_id": 3, "message": "我和爸妈三个人晚餐，我爸花生过敏，我妈不吃辣"},
        ).json()
        session_id = first["conversation_state"]["session_id"]
        second = client.post(
            "/chat",
            json={"user_id": 3, "message": "爸爸今晚不参加", "session_id": session_id},
        ).json()

    assert first["status"] == "ok"
    assert len(first["menu"]) == 4
    assert first["conversation_state"]["constraints"]["soup_count"] == 1
    assert first["conversation_state"]["constraints"]["allergies"] == ["花生"]
    assert first["conversation_state"]["constraints"]["no_spicy"] is True
    assert len(first["diner_suitability"]) == 3
    assert all(item["hard_constraints_satisfied"] for item in first["diner_suitability"])
    assert "多人要求方面" in first["reason"]
    assert not any(
        "花生" in ingredient
        for item in first["menu"]
        for ingredient in item["ingredients"]
    )

    assert second["status"] == "ok"
    assert second["conversation_state"]["constraints"]["people"] == 2
    assert second["conversation_state"]["constraints"]["dish_count"] == 3
    assert second["conversation_state"]["constraints"]["soup_count"] == 0
    assert second["conversation_state"]["constraints"]["allergies"] == []
    assert second["conversation_state"]["constraints"]["no_spicy"] is True
    dad = next(
        diner
        for diner in second["conversation_state"]["diners"]
        if diner["display_name"] == "爸爸"
    )
    assert dad["attendance"] is False
    assert dad["allergies"] == ["花生"]
    assert {item["display_name"] for item in second["diner_suitability"]} == {"用户", "妈妈"}


def test_named_diners_exceeding_confirmed_people_require_clarification(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(
                people=2,
                diner_updates=[
                    DinerUpdate(diner="爸爸", attendance=True, allergies=["花生"]),
                    DinerUpdate(diner="妈妈", attendance=True),
                ],
        )
    ])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post(
            "/chat",
            json={"user_id": 3, "message": "我们两个人吃，我爸爸和妈妈都参加"},
        ).json()

    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert result["tool_calls"] == []
    assert "3 位参餐者" in result["reason"]
    assert result["conversation_state"]["constraints"]["allergies"] == ["花生"]


def test_explicit_menu_structure_is_not_overridden_by_party_defaults(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(
            people=3,
            dish_count=3,
            soup_count=0,
            diner_updates=[
                DinerUpdate(diner="爸爸", attendance=True),
                DinerUpdate(diner="妈妈", attendance=True),
            ],
        )
    ])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 3,
                "message": "我和爸妈三个人，没有其他忌口，明确只要三道菜，不要汤",
            },
        ).json()

    assert result["status"] == "ok"
    assert len(result["menu"]) == 3
    assert result["conversation_state"]["constraints"]["dish_count"] == 3
    assert result["conversation_state"]["constraints"]["soup_count"] == 0
    assert result["conversation_state"]["menu_structure_explicit"] is True


@pytest.mark.parametrize("message,total,soups", [
    ("3人晚餐，没有其他忌口，安排4道菜，其中1道汤。", 4, 1),
    ("3人晚餐，没有其他忌口，共四道菜，包含一道汤。", 4, 1),
    ("3人晚餐，没有其他忌口，四菜一汤。", 5, 1),
    ("3人晚餐，没有其他忌口，安排3道菜，不要汤。", 3, 0),
])
def test_explicit_menu_numbers_correct_valid_but_wrong_model_counts(
    tmp_path, catalog, message, total, soups
):
    llm = ScriptedLLM([complete_intent(people=3, dish_count=6, soup_count=1)])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json={"user_id": 3, "message": message}).json()
    assert result["status"] == "ok"
    assert len(result["menu"]) == total
    assert result["conversation_state"]["constraints"]["dish_count"] == total
    assert result["conversation_state"]["constraints"]["soup_count"] == soups
    assert sum("soup" in catalog.recipes[item["recipe_id"]].categories
               for item in result["menu"]) == soups


@pytest.mark.parametrize("restart", [False, True])
def test_answering_pending_allergy_resumes_requested_menu_without_extra_confirmation(
    tmp_path, catalog, restart
):
    catalog.profiles[3].allergies = ["鸡蛋"]
    first_llm = ScriptedLLM([complete_intent(allergies=["神秘酱料"])])
    answer_intent = Intent(
        action="clarify", allergies=["芝麻"],
        allergy_clarifications={"神秘酱料": ["芝麻"]},
        clarification="已记录芝麻过敏，是否现在推荐晚餐？",
    )
    if not restart:
        first_llm.intents.append(answer_intent)
    with client_for(tmp_path, catalog, first_llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，神秘酱料过敏，安排3道菜，不要汤。",
        }).json()
        assert first["status"] == "clarification_required"
        sid = first["conversation_state"]["session_id"]
        if not restart:
            final = client.post("/chat", json={
                "user_id": 3, "message": "原来说的神秘酱料具体是芝麻。", "session_id": sid,
            }).json()
    if restart:
        with client_for(tmp_path, catalog, ScriptedLLM([answer_intent])) as client:
            final = client.post("/chat", json={
                "user_id": 3, "message": "原来说的神秘酱料具体是芝麻。", "session_id": sid,
            }).json()
    assert final["status"] == "ok"
    assert len(final["menu"]) == 3
    assert final["clarification_questions"] == []
    assert set(final["conversation_state"]["constraints"]["allergies"]) == {"鸡蛋", "芝麻"}
    assert not final["conversation_state"]["pending_allergy_terms"]
    assert not final["conversation_state"]["pending_plan"]
    assert all("鸡蛋" not in item["ingredients"] and "芝麻" not in item["ingredients"]
               for item in final["menu"])


@pytest.mark.parametrize("answer", [
    "原来说的神秘酱料具体是芝麻，先别推荐。",
    "原来说的神秘酱料具体是芝麻，计算精确蛋白质。",
    "原来说的神秘酱料具体是芝麻吗？",
])
def test_allergy_answer_with_another_request_keeps_model_clarification(
    tmp_path, catalog, answer
):
    llm = ScriptedLLM([
        complete_intent(allergies=["神秘酱料"]),
        Intent(action="clarify", allergies=["芝麻"],
               allergy_clarifications={"神秘酱料": ["芝麻"]}, clarification="请确认本轮要求。"),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "1人晚餐，神秘酱料过敏，安排3道菜。",
        }).json()
        final = client.post("/chat", json={
            "user_id": 3, "message": answer,
            "session_id": first["conversation_state"]["session_id"],
        }).json()
    assert final["status"] == "clarification_required"
    assert final["menu"] == []


def test_allergy_answer_without_an_existing_plan_request_does_not_start_planning(
    tmp_path, catalog
):
    llm = ScriptedLLM([
        complete_intent(action="clarify", allergies=["神秘酱料"], clarification="具体是什么？"),
        Intent(action="clarify", allergies=["芝麻"],
               allergy_clarifications={"神秘酱料": ["芝麻"]}, clarification="你希望安排什么餐？"),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={
            "user_id": 3, "message": "我对神秘酱料过敏。",
        }).json()
        final = client.post("/chat", json={
            "user_id": 3, "message": "原来说的神秘酱料具体是芝麻。",
            "session_id": first["conversation_state"]["session_id"],
        }).json()
    assert final["status"] == "clarification_required"
    assert final["menu"] == []
    assert not final["conversation_state"]["pending_plan"]


@pytest.mark.parametrize("message", [
    "1人晚餐，没有其他忌口，安排4道菜还是5道菜？",
    "1人晚餐，没有其他忌口，安排2道菜，其中3道汤。",
    "1人晚餐，没有其他忌口，安排9道菜。",
])
def test_conflicting_or_out_of_range_literal_counts_do_not_produce_a_menu(
    tmp_path, catalog, message
):
    llm = ScriptedLLM([complete_intent(dish_count=3, soup_count=0)])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json={"user_id": 3, "message": message}).json()
    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert not result["conversation_state"]["menu_valid"]


def test_unknown_attributed_allergen_stops_planning(tmp_path, catalog):
    llm = ScriptedLLM([
        complete_intent(
            people=2,
            diner_updates=[
                DinerUpdate(diner="爸爸", attendance=True, allergies=["神秘酱料"])
            ],
        )
    ])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post(
            "/chat",
            json={"user_id": 3, "message": "我和爸爸吃，他对神秘酱料过敏"},
        ).json()

    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert result["tool_calls"] == []
    assert "爸爸的过敏原缺少可靠映射" in result["reason"]


def openai_payload(**changes):
    payload = {
        "model": "fangtai-meal-agent",
        "messages": [{"role": "user", "content": "1人晚餐，没有其他忌口"}],
        "user": "3",
        "stream": False,
    }
    payload.update(changes)
    return payload


def parse_sse(body):
    records = [record for record in body.split("\n\n") if record]
    assert records[-1] == "data: [DONE]"
    return [json.loads(record.removeprefix("data: ")) for record in records[:-1]]


def compatibility_content(response):
    if response.headers["content-type"].startswith("text/event-stream"):
        return "".join(chunk["choices"][0]["delta"].get("content", "")
                       for chunk in parse_sse(response.text))
    return response.json()["choices"][0]["message"]["content"]


def final_recipe_json(content):
    _, marker, suffix = content.rpartition("\n\n【菜谱JSON】\n```json\n")
    assert marker and suffix.endswith("\n```")
    summary = json.loads(suffix[:-4])
    assert isinstance(summary, list)
    assert all(list(item) == ["recipe_id", "name"] for item in summary)
    return summary


def capture_chat_results(client, monkeypatch):
    """Observe actual current results without another HTTP request or planning pass."""
    original = client.app.state.agent.chat
    results = []

    async def capture(*args, **kwargs):
        result = await original(*args, **kwargs)
        results.append(result.model_copy(deep=True))
        return result

    monkeypatch.setattr(client.app.state.agent, "chat", capture)
    return results


def test_openai_nonstreaming_response_and_session_headers(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        response = client.post("/v1/chat/completions", json=openai_payload())
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"id", "object", "created", "model", "choices"}
    assert body["id"].startswith("chatcmpl-")
    assert body["object"] == "chat.completion"
    assert body["model"] == "fangtai-meal-agent"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert body["choices"][0]["message"]["content"]
    assert body["choices"][0]["finish_reason"] == "stop"
    assert response.headers["x-session-id"]
    assert response.headers["x-request-id"].startswith("req_")


def test_openai_sse_chunks_reconstruct_verified_answer(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        with client.stream(
            "POST", "/v1/chat/completions", json=openai_payload(stream=True)
        ) as response:
            body = "".join(response.iter_text())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    timing = response.headers["server-timing"]
    assert "agent_total;dur=" in timing
    assert "agent_parse;dur=" in timing
    assert "ttft" not in timing
    chunks = parse_sse(body)
    assert len({chunk["id"] for chunk in chunks}) == 1
    assert len({chunk["created"] for chunk in chunks}) == 1
    assert chunks[0]["object"] == "chat.completion.chunk"
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert chunks[-1]["choices"][0]["delta"] == {}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    content = "".join(
        chunk["choices"][0]["delta"].get("content", "") for chunk in chunks
    )
    assert "本餐菜品均来自方太菜谱库" in content
    assert "已根据你确认的人数、餐次和饮食要求安排好这餐" in content
    assert "可通过 recipe_id" not in content


def test_openai_followup_uses_response_session_header(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(), Intent(action="replace", replace_slot=2)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/v1/chat/completions", json=openai_payload())
        session_id = first.headers["x-session-id"]
        second = client.post(
            "/v1/chat/completions",
            headers={"X-Session-ID": session_id},
            json=openai_payload(messages=[{"role": "user", "content": "只换第二道菜"}]),
        )
    assert second.status_code == 200
    assert second.headers["x-session-id"] == session_id
    assert llm.parse_calls == 2


def test_openai_conflicting_identity_sources_fail_before_agent(tmp_path, catalog):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post(
            "/v1/chat/completions",
            json=openai_payload(context={"user_id": 4}),
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "identity_conflict"
    assert llm.parse_calls == 0


@pytest.mark.parametrize(
    "payload",
    [
        openai_payload(user=None),
        openai_payload(
            messages=[
                {"role": "user", "content": "第一轮"},
                {"role": "user", "content": "第二轮"},
            ]
        ),
        openai_payload(stream=True, stream_options={"include_usage": True}),
        openai_payload(model="unknown-model"),
        openai_payload(temperature=0.3),
    ],
)
def test_openai_unsupported_request_subset_has_error_envelope(tmp_path, catalog, payload):
    with client_for(tmp_path, catalog, ScriptedLLM()) as client:
        response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 422
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_openai_provider_error_happens_before_sse_headers(tmp_path, catalog):
    llm = ScriptedLLM()
    llm.parse_error = LLMUnavailable("timeout")
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post(
            "/v1/chat/completions", json=openai_payload(stream=True)
        )
    assert response.status_code == 503
    assert not response.headers["content-type"].startswith("text/event-stream")
    assert response.json()["error"]["code"] == "llm_unavailable"


def test_openai_original_profile_block_has_specific_permission_error(tmp_path, catalog):
    llm = ScriptedLLM()
    llm.parse_error = LLMUnavailable("original_profile_blocked")
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/v1/chat/completions", json=openai_payload())
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "permission_error"
    assert response.json()["error"]["code"] == "original_profile_blocked"


def test_openai_client_request_id_replays_business_result(tmp_path, catalog):
    llm = ScriptedLLM()
    headers = {"X-Client-Request-Id": "openai-retry-1"}
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/v1/chat/completions", headers=headers, json=openai_payload())
        second = client.post("/v1/chat/completions", headers=headers, json=openai_payload())
    assert first.status_code == second.status_code == 200
    assert first.headers["x-session-id"] == second.headers["x-session-id"]
    assert first.json()["choices"][0]["message"] == second.json()["choices"][0]["message"]
    assert llm.parse_calls == 1


@pytest.mark.parametrize("stream", [False, True])
def test_openai_renders_every_verified_dish_with_minimal_selected_facts(tmp_path, catalog, stream):
    class OpeningOnlyLLM(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    llm = OpeningOnlyLLM([complete_intent(), Intent(action="replace", replace_slot=2)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        sid = first["conversation_state"]["session_id"]
        # Replay the same business result through both wire formats, with no new parsing.
        payload = {
            "user_id": 3, "session_id": sid, "message": "换第二道菜", "request_id": "menu-render",
        }
        expected = client.post("/chat", json=payload).json()
        response = client.post("/v1/chat/completions", json=openai_payload(
            stream=stream, session_id=sid, request_id="menu-render",
            messages=[{"role": "user", "content": "换第二道菜"}],
        ))
    assert expected["status"] == "ok"
    assert response.status_code == 200
    if stream:
        content = "".join(
            chunk["choices"][0]["delta"].get("content", "")
            for chunk in parse_sse(response.text)
        )
    else:
        content = response.json()["choices"][0]["message"]["content"]
    assert "只将第 2 道" in expected["reason"]
    assert "其他菜保持不变" in expected["reason"]
    assert "未计算蛋白质克数或增肌效果" in expected["reason"]
    assert "营养达标评分" in expected["reason"]
    for dish in expected["menu"]:
        assert f'{dish["slot"]}. {dish["name"]}' in content
        assert dish["recipe_id"] in content
    assert content.rpartition("\n\n【菜谱JSON】\n```json\n")[0].endswith(expected["reason"])
    assert final_recipe_json(content) == [
        {"recipe_id": dish["recipe_id"], "name": dish["name"]} for dish in expected["menu"]
    ]
    assert llm.parse_calls == 2


@pytest.mark.parametrize("stream", [False, True])
def test_openai_clarification_does_not_render_previous_menu(tmp_path, catalog, stream):
    llm = ScriptedLLM([complete_intent(), Intent(max_minutes=1)])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json={"user_id": 3, "message": "1人晚餐，没有其他忌口"}).json()
        response = client.post("/v1/chat/completions", json=openai_payload(
            stream=stream, session_id=first["conversation_state"]["session_id"],
            messages=[{"role": "user", "content": "要求一分钟内做好"}],
        ))
    assert response.status_code == 200
    content = (
        "".join(chunk["choices"][0]["delta"].get("content", "")
                for chunk in parse_sse(response.text))
        if stream else response.json()["choices"][0]["message"]["content"]
    )
    assert "本餐菜单：" not in content
    assert all(dish["name"] not in content for dish in first["menu"])
    assert "本次未返回菜单。" in content
    assert final_recipe_json(content) == []


@pytest.mark.parametrize("stream", [False, True])
def test_openai_json_follows_a_real_second_turn_exclusion(
    tmp_path, catalog, monkeypatch, stream,
):
    llm = ScriptedLLM([
        complete_intent(preferred_ingredients=["鸡胸肉"]),
        Intent(excluded_ingredients=["鸡胸肉"]),
    ])
    with client_for(tmp_path, catalog, llm) as client:
        results = capture_chat_results(client, monkeypatch)
        first = client.post("/v1/chat/completions", json=openai_payload(
            stream=stream,
            messages=[{"role": "user", "content": "1人晚餐，没有其他忌口，想吃鸡胸肉"}],
        ))
        sid = first.headers["x-session-id"]
        second_payload = openai_payload(
            stream=stream, messages=[{"role": "user", "content": "不要鸡胸肉，其他要求不变"}],
        )
        second = client.post("/v1/chat/completions", headers={"X-Session-ID": sid},
                             json=second_payload)
    assert first.status_code == second.status_code == 200
    assert len(second_payload["messages"]) == 1 and second.headers["x-session-id"] == sid
    assert results[0].status == results[1].status == "ok"
    assert any("鸡胸肉" in item.ingredients for item in results[0].menu)
    assert all("鸡胸肉" not in item.ingredients for item in results[1].menu)
    assert "鸡胸肉" in results[1].conversation_state.constraints.excluded_ingredients
    old_ids = {item.recipe_id for item in results[0].menu if "鸡胸肉" in item.ingredients}
    assert old_ids.isdisjoint(item.recipe_id for item in results[1].menu)
    for response, current in zip((first, second), results, strict=True):
        assert final_recipe_json(compatibility_content(response)) == [
            {"recipe_id": item.recipe_id, "name": item.name} for item in current.menu
        ]
    assert results[1].conversation_state.revision == results[0].conversation_state.revision + 1
    assert llm.parse_calls == 2


@pytest.mark.parametrize("stream", [False, True])
def test_openai_json_retains_a_current_reverified_menu(tmp_path, catalog, monkeypatch, stream):
    llm = ScriptedLLM([complete_intent(), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        results = capture_chat_results(client, monkeypatch)
        first = client.post("/v1/chat/completions", json=openai_payload(stream=stream))
        second = client.post("/v1/chat/completions", headers={
            "X-Session-ID": first.headers["x-session-id"],
        }, json=openai_payload(stream=stream, messages=[{"role": "user", "content": "继续"}]))
    assert first.status_code == second.status_code == 200
    assert results[1].status == "ok" and results[1].conversation_state.menu_valid
    expected = [{"recipe_id": item.recipe_id, "name": item.name} for item in results[1].menu]
    assert expected and final_recipe_json(compatibility_content(second)) == expected
    assert expected == final_recipe_json(compatibility_content(first))
    assert "本轮保留原菜单" in compatibility_content(second)
    assert "本次未返回菜单。" not in compatibility_content(second)
    assert llm.parse_calls == 2


@pytest.mark.parametrize("stream", [False, True])
def test_openai_json_no_feasible_response_preserves_reason(tmp_path, catalog, stream):
    llm = ScriptedLLM([complete_intent(inventory=[])])
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/v1/chat/completions", json=openai_payload(
            stream=stream, messages=[{"role": "user", "content": "1人晚餐，无其他忌口，没有食材"}],
        ))
    assert response.status_code == 200
    content = compatibility_content(response)
    assert "本次未返回菜单。" in content and final_recipe_json(content) == []
    assert content.rpartition("\n\n【菜谱JSON】\n```json\n")[0] != "本次未返回菜单。"


@pytest.mark.parametrize("stream", [False, True])
def test_openai_json_history_is_not_a_current_menu(tmp_path, catalog, monkeypatch, stream):
    llm = ScriptedLLM([complete_intent(), Intent(max_minutes=1)])
    with client_for(tmp_path, catalog, llm) as client:
        results = capture_chat_results(client, monkeypatch)
        first = client.post("/v1/chat/completions", json=openai_payload(stream=stream))
        second = client.post("/v1/chat/completions", headers={
            "X-Session-ID": first.headers["x-session-id"],
        }, json=openai_payload(stream=stream, messages=[{"role": "user", "content": "要求一分钟内做好"}]))
    assert first.status_code == second.status_code == 200
    assert results[1].status == "clarification_required" and results[1].menu == []
    assert results[1].conversation_state.menu_ids == results[0].conversation_state.menu_ids
    assert results[1].conversation_state.menu_history
    content = compatibility_content(second)
    assert results[1].reason in content
    assert "本次未返回菜单。" in content and final_recipe_json(content) == []
    assert llm.parse_calls == 2


@pytest.mark.parametrize("first_stream", [False, True])
def test_openai_json_replay_across_formats_and_conflict_errors(tmp_path, catalog, first_stream):
    llm = ScriptedLLM([complete_intent(), Intent(action="explain")])
    payload = openai_payload(stream=first_stream, request_id="json-replay")
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/v1/chat/completions", json=payload)
        sid = first.headers["x-session-id"]
        replay_payload = {**payload, "stream": not first_stream, "session_id": sid}
        replay = client.post("/v1/chat/completions", json=replay_payload)
        assert first.status_code == replay.status_code == 200
        assert compatibility_content(first) == compatibility_content(replay)
        assert compatibility_content(replay).count("\n\n【菜谱JSON】\n```json\n") == 1
        assert final_recipe_json(compatibility_content(replay))
        assert llm.parse_calls == 1
        conflict = client.post("/v1/chat/completions", json={
            **replay_payload, "messages": [{"role": "user", "content": "不同消息"}],
        })
        advanced = client.post("/v1/chat/completions", json={
            **replay_payload, "request_id": "json-new-turn",
            "messages": [{"role": "user", "content": "继续"}],
        })
        assert advanced.status_code == 200
        expired = client.post("/v1/chat/completions", json=replay_payload)
    for error in (conflict, expired):
        assert error.status_code == 409 and error.json()["error"]["code"] == "session_conflict"
        assert set(error.json()) == {"error"} and "【菜谱JSON】" not in error.text
        assert not error.headers["content-type"].startswith("text/event-stream")
    assert llm.parse_calls == 2


def test_openai_json_does_not_change_native_chat_or_repeat_tools(tmp_path, catalog, monkeypatch):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        native_request = {"user_id": 3, "message": "1人晚餐，没有其他忌口", "request_id": "native-json"}
        native = client.post("/chat", json=native_request).json()
        sid = native["conversation_state"]["session_id"]

        def unexpected_work(*args, **kwargs):
            raise AssertionError("Rendering/replaying a completed result must not invoke tools")

        monkeypatch.setattr(client.app.state.agent.tools, "call", unexpected_work)
        rendered = client.post("/v1/chat/completions", json=openai_payload(
            session_id=sid, request_id="native-json",
        ))
        native_replay = client.post("/chat", json={**native_request, "session_id": sid}).json()
    assert native["schema_version"] == "2.0" and native["status"] == "ok"
    assert native == native_replay
    assert "【菜谱JSON】" not in native["reason"]
    assert final_recipe_json(compatibility_content(rendered)) == [
        {"recipe_id": item["recipe_id"], "name": item["name"]} for item in native["menu"]
    ]
    assert llm.parse_calls == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("field,bad_value", [
    ("recipe_id", None), ("recipe_id", ""), ("name", 123), ("name", "\ud800"),
])
def test_openai_invalid_final_data_uses_json_error_before_sse(
    tmp_path, catalog, monkeypatch, stream, field, bad_value,
):
    result = ChatResult(
        status="ok", reason="合成说明。",
        menu=[MenuItem(slot=1, recipe_id="synthetic_http", name="合成菜", ingredients=[], steps="")],
        conversation_state=SessionState(
            session_id="b" * 32, user_id=3, menu_ids=["synthetic_http"], menu_valid=True,
        ),
    )
    result.menu[0] = result.menu[0].model_copy(update={field: bad_value})
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        async def invalid_result(**kwargs):
            return result

        monkeypatch.setattr(client.app.state.agent, "chat", invalid_result)
        response = client.post("/v1/chat/completions", json=openai_payload(stream=stream))
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": {
        "message": "本轮菜单的输出数据不完整或不一致，无法生成回答。",
        "type": "server_error", "param": None, "code": "invalid_menu_result",
    }}
    assert "【菜谱JSON】" not in response.text and "[DONE]" not in response.text
    assert llm.parse_calls == 0


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("payload_changes,headers,status,code", [
    ({"user": None}, {}, 422, "missing_user"),
    ({"user": 3}, {}, 422, "invalid_request"),
    ({"context": {"user_id": "3"}}, {}, 422, "invalid_request"),
    ({"context": {"user_id": 4}}, {}, 409, "identity_conflict"),
    ({}, {"X-User-ID": "4"}, 409, "identity_conflict"),
    ({"session_id": "a" * 32}, {"X-Session-ID": "b" * 32}, 409, "identity_conflict"),
    ({"request_id": "first"}, {"X-Client-Request-Id": "second"}, 409, "identity_conflict"),
    ({"user": "999999"}, {}, 404, "not_found"),
    ({"session_id": "f" * 32}, {}, 404, "not_found"),
])
def test_openai_request_errors_never_get_a_success_tail(
    tmp_path, catalog, stream, payload_changes, headers, status, code,
):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/v1/chat/completions", headers=headers,
                               json=openai_payload(stream=stream, **payload_changes))
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert set(response.json()["error"]) == {"message", "type", "param", "code"}
    assert "【菜谱JSON】" not in response.text and "[DONE]" not in response.text
    assert llm.parse_calls == 0


@pytest.mark.parametrize("body_changes,headers", [
    ({"user": None, "context": {"user_id": 3}}, {}),
    ({"user": None}, {"X-User-ID": "3"}),
    ({"context": {"user_id": 3}}, {"X-User-ID": "03"}),
])
def test_openai_supported_user_sources_keep_existing_types_and_matching_rules(
    tmp_path, catalog, body_changes, headers,
):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/v1/chat/completions", headers=headers,
                               json=openai_payload(**body_changes))
    assert response.status_code == 200 and final_recipe_json(compatibility_content(response))
    assert llm.parse_calls == 1


@pytest.mark.parametrize("session_source", ["session_id", "context", "header", "all"])
def test_openai_supported_session_sources_do_not_require_resending_history(
    tmp_path, catalog, session_source,
):
    llm = ScriptedLLM([complete_intent(), Intent(action="explain")])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/v1/chat/completions", json=openai_payload())
        sid = first.headers["x-session-id"]
        body, headers = {}, {}
        if session_source in {"session_id", "all"}:
            body["session_id"] = sid
        if session_source in {"context", "all"}:
            body["context"] = {"session_id": sid}
        if session_source in {"header", "all"}:
            headers["X-Session-ID"] = sid
        second = client.post("/v1/chat/completions", headers=headers, json=openai_payload(
            **body, messages=[{"role": "user", "content": "继续"}],
        ))
        wrong_user = client.post("/v1/chat/completions", headers={"X-Session-ID": sid},
                                 json=openai_payload(user="4", stream=True))
    assert second.status_code == 200 and second.headers["x-session-id"] == sid
    assert final_recipe_json(compatibility_content(second)) == final_recipe_json(
        compatibility_content(first)
    )
    assert wrong_user.status_code == 409 and wrong_user.json()["error"]["code"] == "session_conflict"
    assert "【菜谱JSON】" not in wrong_user.text and llm.parse_calls == 2


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status,code,error_type", [
    (401, "unauthorized", "invalid_request_error"),
    (403, "original_profile_blocked", "permission_error"),
    (429, "rate_limit_exceeded", "rate_limit_error"),
])
def test_openai_existing_error_handler_preserves_status_without_a_success_tail(
    tmp_path, catalog, monkeypatch, stream, status, code, error_type,
):
    # Synthetic raised errors exercise the backend boundary. This is not a live
    # gateway-auth test; actual Bearer verification remains solely in the gateway.
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        async def rejected(**kwargs):
            raise OpenAIRequestError(status, "合成错误说明", code, error_type=error_type)

        monkeypatch.setattr(client.app.state.agent, "chat", rejected)
        response = client.post("/v1/chat/completions", json=openai_payload(stream=stream))
    assert response.status_code == status
    assert response.json() == {"error": {
        "message": "合成错误说明", "type": error_type, "param": None, "code": code,
    }}
    assert response.headers["content-type"].startswith("application/json")
    assert "【菜谱JSON】" not in response.text and "[DONE]" not in response.text
    assert llm.parse_calls == 0


@pytest.mark.parametrize("stream", [None, False, True], ids=["omitted", "false", "true"])
def test_submitted_minimal_request_and_actual_second_turn(tmp_path, catalog, stream):
    llm = ScriptedLLM([complete_intent(), Intent(action="replace", replace_slot=2)])
    payload = {
        "model": "fangtai-meal-agent", "user": "3",
        "messages": [{"role": "user", "content": "1人晚餐，没有其他忌口"}],
    }
    if stream is not None:
        payload["stream"] = stream
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/v1/chat/completions", json=payload)
        assert first.status_code == 200
        sid = first.headers["x-session-id"]
        second = client.post("/v1/chat/completions", headers={"X-Session-ID": sid}, json={
            **payload, "messages": [{"role": "user", "content": "只换第二道菜"}],
        })
        assert second.status_code == 200
    assert second.headers["x-session-id"] == sid and llm.parse_calls == 2
    old = final_recipe_json(compatibility_content(first))
    new = final_recipe_json(compatibility_content(second))
    assert old[0] == new[0] and old[2] == new[2] and old[1] != new[1]
    assert "/api-guide" not in compatibility_content(second)
    expected_type = "text/event-stream" if stream else "application/json"
    assert first.headers["content-type"].startswith(expected_type)
    if stream:
        assert second.text.count("data: [DONE]") == 1
        assert parse_sse(second.text)[-1]["choices"][0]["finish_reason"] == "stop"


def test_required_schema_and_nullable_extensions_keep_identity_contract(tmp_path, catalog):
    schema = ChatCompletionsRequest.model_json_schema()
    assert schema["required"] == ["model", "messages"]
    assert schema["$defs"]["OpenAIMessage"]["required"] == ["role", "content"]
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/v1/chat/completions", headers={"X-User-ID": "3"}, json={
            "model": "fangtai-meal-agent",
            "messages": [{"role": "user", "content": "1人晚餐，没有其他忌口"}],
            "user": None, "context": None, "session_id": None, "request_id": None,
            "stream_options": None,
        })
    assert response.status_code == 200 and final_recipe_json(compatibility_content(response))
    assert llm.parse_calls == 1


@pytest.mark.parametrize("changes,removed,parameter,code,hint", [
    ({}, "model", "model", "invalid_request", "fangtai-meal-agent"),
    ({}, "messages", "messages", "invalid_request", "恰好一条"),
    ({}, "user", "user", "missing_user", "已获授权"),
    ({"model": "synthetic-private-sentinel"}, None, "model", "invalid_request", "fangtai"),
    ({"user": 3}, None, "user", "invalid_request", "字符串"),
    ({"stream": None}, None, "stream", "invalid_request", "不能为null"),
    ({"stream": False, "stream_options": {}}, None, "stream_options", "invalid_request", "删除"),
    ({"stream_options": {"include_usage": True}}, None,
     "stream_options.include_usage", "invalid_request", "include_usage=false"),
    ({"context": {"user_id": "3"}}, None, "context.user_id", "invalid_request", "JSON正整数"),
    ({"session_id": "bad"}, None, "session_id", "invalid_request", "首轮可省略"),
    ({"request_id": ""}, None, "request_id", "invalid_request", "1–128"),
    ({"messages": [{"role": "assistant", "content": "synthetic-private-sentinel"}]}, None,
     "messages.0.role", "invalid_request", "role 必须为 user"),
    ({"messages": [{"role": "user"}]}, None,
     "messages.0.content", "invalid_request", "纯文本"),
    ({"tools": [{"synthetic": "synthetic-private-sentinel"}]}, None,
     "tools", "invalid_request", "删除不支持"),
    ({"temperature": 0.3}, None, "temperature", "invalid_request", "删除不支持"),
])
def test_invalid_submitted_fields_get_safe_correction_without_success(
    tmp_path, catalog, changes, removed, parameter, code, hint,
):
    llm = ScriptedLLM()
    payload = openai_payload(stream=True, **changes) if "stream" not in changes else openai_payload(
        **changes,
    )
    if removed:
        payload.pop(removed)
    with client_for(tmp_path, catalog, llm) as client:
        response = client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    error = response.json()["error"]
    assert set(error) == {"message", "type", "param", "code"}
    assert error["type"] == "invalid_request_error"
    assert error["param"] == parameter and error["code"] == code
    assert hint in error["message"] and "/api-guide" in error["message"]
    assert "synthetic-private-sentinel" not in response.text
    assert "[DONE]" not in response.text and "【菜谱JSON】" not in response.text
    assert llm.parse_calls == 0


def test_guide_does_not_touch_existing_session_or_invoke_business(tmp_path, catalog, monkeypatch):
    llm = ScriptedLLM()
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/v1/chat/completions", json=openai_payload())
        assert first.status_code == 200
        database = tmp_path / "state.db"
        before = database.read_bytes()

        def unexpected_work(*args, **kwargs):
            raise AssertionError("Public guide must not access existing business/session state")

        monkeypatch.setattr(client.app.state.agent, "chat", unexpected_work)
        monkeypatch.setattr(client.app.state.agent.tools, "call", unexpected_work)
        monkeypatch.setattr(SessionStore, "get", unexpected_work)
        monkeypatch.setattr(llm, "parse", unexpected_work)
        response = client.get("/api-guide", headers={"X-Session-ID": first.headers["x-session-id"]})
        head = client.head("/api-guide")
        assert response.status_code == head.status_code == 200
        assert "x-session-id" not in response.headers
        assert head.content == b"" and database.read_bytes() == before
    assert llm.parse_calls == 1
