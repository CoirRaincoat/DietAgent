import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import DinerUpdate, Ingredient, Intent, Recipe, UserProfile
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
        assert "套餐搭配说明" in first["reason"]
        assert "蔬菜类菜" in first["reason"]
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
    assert "逐人适配" in first["reason"]
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
