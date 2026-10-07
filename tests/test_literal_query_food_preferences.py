"""Literal query-only food requests must not silently disappear from a meal."""

import pytest

from app.domain.models import Intent
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as catalog


@pytest.mark.parametrize("clause", ["想吃凉粉", "这餐想吃凉粉", "整餐希望吃凉粉", "想吃凉粉即可"])
def test_exact_query_only_food_request_is_saved_and_bounded_gap_is_filled(tmp_path, catalog, clause):
    catalog.profiles[3].health_goals = []
    llm = ScriptedLLM([complete_intent(dish_count=3, soup_count=0, no_spicy=True,
                                     query_terms=["凉粉"])])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，3菜0汤，不辣，" + clause + "，没有其他忌口。")).json()
    assert result["status"] == "ok"
    assert result["conversation_state"]["meal_constraints"]["preferred_ingredients"] == ["凉粉"]
    assert result["conversation_state"]["constraints"]["preferred_ingredients"] == ["凉粉"]
    assert "当前菜单未覆盖食材偏好：凉粉" not in result["reason"]
    assert any(item["name"] == "黄瓜清拌凉粉（新生成）" for item in result["menu"])
    assert "待试做" in result["reason"]
    assert len(result["menu"]) == 3 and result["conversation_state"]["constraints"]["no_spicy"]


def test_saved_liangfen_preference_is_still_disclosed_when_pea_allergy_blocks_draft(tmp_path, catalog):
    catalog.profiles[3].health_goals = []
    llm = ScriptedLLM([complete_intent(dish_count=3, soup_count=0, no_spicy=True,
                                     allergies=["豌豆"], query_terms=["凉粉"])])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，3菜0汤，不辣，豌豆过敏，想吃凉粉，没有其他忌口。")).json()
    assert result["status"] == "ok"
    assert result["conversation_state"]["constraints"]["preferred_ingredients"] == ["凉粉"]
    assert "当前菜单未覆盖食材偏好：凉粉" in result["reason"]
    assert all(item["provenance"]["origin"] == "catalog" for item in result["menu"])


@pytest.mark.parametrize("clause", [
    "不想吃凉粉", "不想吃凉粉但想吃鸡肉", "想吃凉粉吗", "为什么想吃凉粉",
    "如果想吃凉粉", "不是想吃凉粉", "例如想吃凉粉", "小王想吃凉粉",
    "参考想吃凉粉", "想吃凉粉和鸡肉", "想吃凉粉酱", "想吃凉粉味的菜",
    "第2道想吃凉粉", "只换第2道，想吃凉粉", "想吃凉粉，不确定是否合适",
])
def test_query_word_is_not_authority_without_a_complete_unscoped_assertion(tmp_path, catalog, clause):
    llm = ScriptedLLM([complete_intent(query_terms=["凉粉"])])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，" + clause + "，没有其他忌口。")).json()
    assert result["conversation_state"]["meal_constraints"]["preferred_ingredients"] == []


def test_no_query_word_does_not_create_new_food_extraction_authority(tmp_path, catalog):
    with client_for(tmp_path, catalog, ScriptedLLM([complete_intent()])) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，想吃凉粉，没有其他忌口。")).json()
    assert result["conversation_state"]["constraints"]["preferred_ingredients"] == []


def test_existing_parsed_food_alias_is_not_duplicated(tmp_path, catalog):
    # 大蒜/蒜 is an existing canonical alias; 鸡/鸡肉 are not, so do not
    # expand the food namespace just to manufacture a passing assertion.
    llm = ScriptedLLM([complete_intent(query_terms=["蒜"], preferred_ingredients=["大蒜"])])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，想吃蒜，没有其他忌口。")).json()
    assert result["conversation_state"]["constraints"]["preferred_ingredients"] == ["大蒜"]


@pytest.mark.parametrize("word", ["清淡", "便当", "早餐", "汤", "蛋白质"])
def test_non_food_axes_are_not_promoted_from_query_words(tmp_path, catalog, word):
    with client_for(tmp_path, catalog, ScriptedLLM([complete_intent(query_terms=[word])])) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，想吃" + word + "，没有其他忌口。")).json()
    assert result["conversation_state"]["meal_constraints"]["preferred_ingredients"] == []


def test_recovered_preference_survives_retry_restart_and_readonly_with_id_isolation(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(query_terms=["凉粉"]), Intent()])
    with client_for(tmp_path, catalog, llm) as client:
        body = dict(user_id=3, message="1人晚餐，想吃凉粉，没有其他忌口。", request_id="liangfen")
        first = client.post("/chat", json=body).json()
        sid = first["conversation_state"]["session_id"]
        assert client.post("/chat", json={**body, "session_id": sid}).json() == first
        assert client.post("/chat", json=dict(user_id=4, session_id=sid, message="继续")).status_code == 409
    with client_for(tmp_path, catalog, ScriptedLLM([Intent(action="explain")])) as client:
        same = client.post("/chat", json=dict(user_id=3, session_id=sid, message="解释当前菜单，不换菜。")).json()
    assert same["conversation_state"]["constraints"]["preferred_ingredients"] == ["凉粉"]
    assert same["menu"] == first["menu"]


def test_readonly_and_local_actions_cannot_promote_query_terms_to_whole_meal(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(), Intent(query_terms=["凉粉"]),
                      Intent(action="replace", replace_slot=2, query_terms=["凉粉"])])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，没有其他忌口。")).json()
        sid = first["conversation_state"]["session_id"]
        readonly = client.post("/chat", json=dict(user_id=3, session_id=sid,
            message="只解释想吃凉粉，不换菜。")).json()
        local = client.post("/chat", json=dict(user_id=3, session_id=sid,
            message="只换第2道，想吃凉粉。")).json()
    assert readonly["menu"] == first["menu"]
    assert readonly["conversation_state"]["constraints"] == first["conversation_state"]["constraints"]
    assert local["conversation_state"]["constraints"]["preferred_ingredients"] == []
    assert local["menu"][0] == first["menu"][0] and local["menu"][2] == first["menu"][2]
