"""Known-food negative assertions supplement an otherwise valid parser output."""
import pytest

from app.domain.models import DinerUpdate, Intent
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as catalog
from tests.test_component_slots import dish


@pytest.mark.parametrize("clause, foods", [
    ("不放蒜", ["蒜"]), ("不要加大蒜", ["大蒜"]), ("整餐不放蒜和葱", ["蒜", "葱"]),
    ("我不吃鸡蛋", ["鸡蛋"]), ("请避开花生", ["花生"]),
    ("本餐不加香菜即可", ["香菜"]), ("不吃海鲜", ["海鲜"]),
])
def test_omitted_known_food_exclusion_enters_hard_field(tmp_path, catalog, clause, foods):
    catalog.profiles[3].health_goals = []
    llm = ScriptedLLM([complete_intent(dish_count=3, soup_count=0, preferences=[clause])])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat", json=dict(user_id=3,
            message="1人晚餐，3道菜不要汤，"+clause+"，没有其他忌口。")).json()
    assert result["status"] == "ok"
    assert result["conversation_state"]["meal_constraints"]["excluded_ingredients"] == foods
    assert result["conversation_state"]["constraints"]["excluded_ingredients"] == foods


@pytest.mark.parametrize("clause", [
    "少放蒜", "蒜可以少放", "可以不放蒜", "不放蒜的做法是什么", "为什么不放蒜",
    "不放蒜吗", "如果不放蒜", "不是不放蒜", "取消不放蒜", "我爸不吃蒜",
    "小王说，不放蒜", "例如不放蒜", "参考‘不放蒜’", "不要蒜香口味",
    "第二道不放蒜", "第2道不放蒜", "只换第2道，不放蒜", "不放未收录酱汁",
])
def test_literal_recovery_does_not_guess_scope_or_food_from_non_assertions(tmp_path, catalog, clause):
    llm = ScriptedLLM([complete_intent()])
    with client_for(tmp_path, catalog, llm) as client:
        result = client.post("/chat",json=dict(user_id=3,
            message="1人晚餐，3菜0汤，"+clause+"，没有其他忌口。")).json()
    assert result["conversation_state"]["meal_constraints"]["excluded_ingredients"] == []


def test_alias_already_parsed_is_not_duplicated_or_changed(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(excluded_ingredients=["大蒜"])])
    with client_for(tmp_path,catalog,llm) as client:
        result=client.post("/chat",json=dict(user_id=3,message="1人晚餐，不放蒜，没有其他忌口。")).json()
    assert result["conversation_state"]["constraints"]["excluded_ingredients"] == ["大蒜"]


def test_alias_restatement_does_not_duplicate_a_persisted_meal_exclusion(tmp_path,catalog):
    llm=ScriptedLLM([complete_intent(excluded_ingredients=["大蒜"]),Intent()])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="1人晚餐，不放大蒜，没有其他忌口。")).json()
        again=client.post("/chat",json=dict(user_id=3,session_id=first["conversation_state"]["session_id"],
            message="整餐不放蒜，其他要求不变。")).json()
    assert again["conversation_state"]["meal_constraints"]["excluded_ingredients"] == ["大蒜"]


def test_local_parser_action_cannot_promote_unscoped_ingredient_to_whole_meal(tmp_path,catalog):
    llm=ScriptedLLM([complete_intent(),Intent(action="replace",replace_slot=2)])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="1人晚餐，没有其他忌口。")).json()
        local=client.post("/chat",json=dict(user_id=3,session_id=first["conversation_state"]["session_id"],
            message="换一下第二个菜，不放蒜。")).json()
    assert local["conversation_state"]["meal_constraints"]["excluded_ingredients"] == []


def test_already_attributed_self_exclusion_is_not_promoted_to_persistent_whole_meal(tmp_path,catalog):
    catalog.profiles[3].health_goals=[]
    llm=ScriptedLLM([
        complete_intent(people=2,diner_updates=[DinerUpdate(diner="用户",aliases=["我"],
            attendance=True,excluded_ingredients=["牛肉"]),DinerUpdate(diner="小王",attendance=True)]),
        Intent(diner_updates=[DinerUpdate(diner="我",attendance=False)]),
    ])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,
            message="我和小王2人晚餐，我不吃牛肉，3菜0汤，没有其他忌口。")).json()
        left=client.post("/chat",json=dict(user_id=3,session_id=first["conversation_state"]["session_id"],
            message="我不参加，其他人照旧。")).json()
    assert first["conversation_state"]["meal_constraints"]["excluded_ingredients"]==[]
    assert first["conversation_state"]["constraints"]["excluded_ingredients"]==["牛肉"]
    assert left["conversation_state"]["meal_constraints"]["excluded_ingredients"]==[]
    assert left["conversation_state"]["constraints"]["excluded_ingredients"]==[]


def test_explicit_whole_scope_is_not_skipped_just_because_owner_also_has_exclusion(tmp_path,catalog):
    llm=ScriptedLLM([complete_intent(diner_updates=[DinerUpdate(diner="我",excluded_ingredients=["牛肉"])])])
    with client_for(tmp_path,catalog,llm) as client:
        result=client.post("/chat",json=dict(user_id=3,message="1人晚餐，整餐不吃牛肉，没有其他忌口。")).json()
    assert result["conversation_state"]["meal_constraints"]["excluded_ingredients"]==["牛肉"]


def test_ambiguous_diner_lookup_remains_a_clarification_not_an_uncaught_error(tmp_path,catalog):
    llm=ScriptedLLM([
        complete_intent(people=2,diner_updates=[DinerUpdate(diner="爸爸",attendance=True)]),
        Intent(diner_updates=[DinerUpdate(diner="爸爸",aliases=["我"],excluded_ingredients=["蒜"])]),
    ])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="我和爸爸2人晚餐，没有其他忌口。")).json()
        response=client.post("/chat",json=dict(user_id=3,session_id=first["conversation_state"]["session_id"],
            message="爸爸不吃蒜。")).json()
    assert response["status"]=="clarification_required"
    assert "无法确定" in response["reason"]


def test_added_exclusion_is_retained_on_retry_restart_and_id_isolation(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(), Intent()])
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat",json=dict(user_id=3,message="1人晚餐，3菜0汤，没有其他忌口。")).json()
        body = dict(user_id=3,session_id=first["conversation_state"]["session_id"],
                    message="整餐不放蒜，其他要求不变。",request_id="garlic")
        added = client.post("/chat",json=body).json()
        assert added["status"] == "ok"
        assert client.post("/chat",json=body).json() == added
        assert added["conversation_state"]["constraints"]["excluded_ingredients"] == ["蒜"]
        assert client.post("/chat",json=dict(user_id=4,session_id=body["session_id"],message="继续")).status_code == 409
    with client_for(tmp_path,catalog,ScriptedLLM([Intent(action="explain")])) as client:
        restarted=client.post("/chat",json=dict(user_id=3,session_id=body["session_id"],message="解释，不换菜。")).json()
    assert restarted["conversation_state"]["constraints"]["excluded_ingredients"] == ["蒜"]
    assert restarted["menu"] == added["menu"]


def test_readonly_request_cannot_add_literal_food_exclusion(tmp_path, catalog):
    llm = ScriptedLLM([complete_intent(),Intent()])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="1人晚餐，没有其他忌口。")).json()
        after=client.post("/chat",json=dict(user_id=3,session_id=first["conversation_state"]["session_id"],
            message="只解释不放蒜，不换菜。")).json()
    assert after["conversation_state"]["constraints"] == first["conversation_state"]["constraints"]
    assert after["menu"] == first["menu"]


def test_new_whole_exclusion_does_not_authorize_replacing_a_protected_slot(tmp_path, catalog):
    catalog.profiles[3].health_goals = []
    garlic = dish("蒜蒸鸡肉", "鸡胸肉200克；蒜10克；水50克", "鸡胸肉和蒜蒸熟装盘。")
    vegetable = dish("炒青菜", "青菜200克；油5克", "青菜炒熟装盘。")
    other = dish("炒西兰花", "西兰花200克；油5克", "西兰花炒熟装盘。")
    rice = dish("蒸米饭", "大米200克；水300克", "大米加水蒸熟。")
    catalog.recipes = {r.recipe_id: r for r in (garlic, vegetable, other, rice)}
    local_intent = Intent(action="replace")
    llm = ScriptedLLM([complete_intent(), local_intent])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="1人晚餐，3菜0汤，没有其他忌口。")).json()
        assert first["status"] == "ok" and any(r["recipe_id"] == garlic.recipe_id for r in first["menu"])
        target = next(r["slot"] for r in first["menu"] if r["recipe_id"] in {vegetable.recipe_id,other.recipe_id})
        local_intent.replace_slot = target
        result=client.post("/chat",json=dict(user_id=3,session_id=first["conversation_state"]["session_id"],
            message=f"只换第{target}道，整餐不放蒜，其他要求不变。")).json()
    assert result["status"] != "ok"
    assert result["conversation_state"]["constraints"]["excluded_ingredients"] == ["蒜"]
    assert result["conversation_state"]["menu_ids"] == first["conversation_state"]["menu_ids"]


def test_recovered_exclusion_still_requires_confirmed_revocation(tmp_path,catalog):
    llm=ScriptedLLM([complete_intent(),Intent(revoke_exclusions=["蒜"]),Intent(revoke_confirmed=True)])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="1人晚餐，整餐不放蒜，没有其他忌口。")).json()
        sid=first["conversation_state"]["session_id"]
        pending=client.post("/chat",json=dict(user_id=3,session_id=sid,message="取消刚才不放蒜这一条。")).json()
        assert pending["conversation_state"]["constraints"]["excluded_ingredients"] == ["蒜"]
        assert pending["conversation_state"]["pending_revoke_exclusion"]
        confirmed=client.post("/chat",json=dict(user_id=3,session_id=sid,message="确认")).json()
    assert confirmed["conversation_state"]["constraints"]["excluded_ingredients"] == []
