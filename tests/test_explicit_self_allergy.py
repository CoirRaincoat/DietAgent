"""Local native workflows for a parsed meal allergen omitted from the owner."""
import pytest

from app.domain.models import DinerUpdate, Intent
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_agent_api import catalog as catalog


def owner(response):
    return next(d for d in response["conversation_state"]["diners"] if d["profile_owner"])


@pytest.mark.parametrize("message", [
    "我们3个人吃晚餐：我对海鲜过敏，小王不吃花生，小李不吃辣。没有其他忌口，安排4道菜，不要汤。",
    "3人晚餐，我对海鲜过敏，小王不吃花生，小李不吃辣，没有其他忌口，4菜0汤。",
    "3人晚餐；本人对海鲜过敏；小王不吃花生；小李不吃辣；4菜0汤，没有其他忌口。",
])
def test_known_meal_allergen_is_also_recorded_for_explicit_self(tmp_path,catalog,message):
    llm=ScriptedLLM([complete_intent(people=3,dish_count=4,soup_count=0,allergies=["海鲜"],
        diner_updates=[DinerUpdate(diner="小王",excluded_ingredients=["花生"]),
                       DinerUpdate(diner="小李",no_spicy=True)])])
    with client_for(tmp_path,catalog,llm) as client:
        response=client.post("/chat",json=dict(user_id=3,message=message)).json()
    assert response["status"] == "ok"
    assert owner(response)["allergies"] == ["海鲜"]
    assert response["conversation_state"]["meal_constraints"]["allergies"] == ["海鲜"]
    assert response["conversation_state"]["constraints"]["allergies"] == ["海鲜"]
    others=[d for d in response["conversation_state"]["diners"] if not d["profile_owner"]]
    assert all(not d["allergies"] for d in others)
    assert any(d["display_name"] == "小王" and d["excluded_ingredients"] == ["花生"] for d in others)
    assert any(d["display_name"] == "小李" and d["no_spicy"] for d in others)


@pytest.mark.parametrize("clause", [
    "小王对海鲜过敏", "我爸对海鲜过敏", "我妈妈对海鲜过敏", "我的朋友对海鲜过敏",
    "我没有海鲜过敏", "我对海鲜不过敏", "我对海鲜过敏吗", "是否我对海鲜过敏",
    "如果我对海鲜过敏", "假设我对海鲜过敏", "例如我对海鲜过敏", "讨论我对海鲜过敏",
    "他说我对海鲜过敏", "参考‘我对海鲜过敏’这句话", "我对海鲜过敏和小王的情况不一样",
])
def test_global_allergen_cannot_be_borrowed_to_guess_self(tmp_path,catalog,clause):
    # Intentionally imperfect extraction still retains meal-wide screening.
    llm=ScriptedLLM([complete_intent(allergies=["海鲜"])])
    with client_for(tmp_path,catalog,llm) as client:
        response=client.post("/chat",json=dict(user_id=3,message="1人晚餐，"+clause+"，3菜0汤，没有其他忌口。")).json()
    assert owner(response)["allergies"] == []
    assert "海鲜" in response["conversation_state"]["constraints"]["allergies"]


def test_owner_facts_survive_exit_return_retry_restart_and_cross_id(tmp_path,catalog):
    llm=ScriptedLLM([
        complete_intent(people=3,dish_count=4,soup_count=0,allergies=["海鲜"],
            diner_updates=[DinerUpdate(diner="小王"),DinerUpdate(diner="小李",no_spicy=True)]),
        Intent(diner_updates=[DinerUpdate(diner="我",attendance=False)]),
        Intent(diner_updates=[DinerUpdate(diner="本人",attendance=True)]),
    ])
    with client_for(tmp_path,catalog,llm) as client:
        first=client.post("/chat",json=dict(user_id=3,message="我们3人吃晚餐：我对海鲜过敏，小王和小李都参加，小李不辣，4菜0汤，没有其他忌口。")).json()
        sid=first["conversation_state"]["session_id"]
        exit_response=client.post("/chat",json=dict(user_id=3,session_id=sid,message="我不参加，其他人照旧。")).json()
        body=dict(user_id=3,session_id=sid,message="我重新参加，其他要求不变。",request_id="self-return")
        returned=client.post("/chat",json=body).json()
        assert client.post("/chat",json=body).json() == returned
        other_id=4
        catalog.profiles[other_id]=catalog.profiles[3].model_copy(update={"user_id":other_id})
        assert client.post("/chat",json=dict(user_id=other_id,session_id=sid,message="继续")).status_code == 409
    for result in (first,exit_response,returned):
        assert owner(result)["diner_id"] == owner(first)["diner_id"]
        assert owner(result)["allergies"] == ["海鲜"]
        # No migration/deletion of the global safety field is authorized here.
        assert "海鲜" in result["conversation_state"]["meal_constraints"]["allergies"]
        assert result["conversation_state"]["constraints"]["no_spicy"]
    assert not owner(exit_response)["attendance"] and owner(returned)["attendance"]
    with client_for(tmp_path,catalog,ScriptedLLM([Intent(action="explain")])) as client:
        restarted=client.post("/chat",json=dict(user_id=3,session_id=sid,message="解释当前菜单，不换菜。")).json()
    assert owner(restarted)["allergies"] == ["海鲜"]
    assert restarted["menu"] == returned["menu"]


def test_owner_binding_does_not_infer_attendance_or_remove_other_allergens(tmp_path,catalog):
    catalog.profiles[3].allergies=["鸡蛋"]
    llm=ScriptedLLM([complete_intent(allergies=["海鲜","花生"])])
    with client_for(tmp_path,catalog,llm) as client:
        response=client.post("/chat",json=dict(user_id=3,message=
            "1人晚餐，我对海鲜过敏，整桌也避开花生，3菜0汤，没有其他忌口。")).json()
    assert owner(response)["allergies"] == ["鸡蛋","海鲜"]
    assert owner(response)["participation_basis"] == "profile_unlinked"
    assert set(response["conversation_state"]["constraints"]["allergies"]) == {"鸡蛋","海鲜","花生"}


def test_read_only_request_does_not_gain_fact_update_authority(tmp_path,catalog):
    llm=ScriptedLLM([
        complete_intent(allergies=["海鲜"]),
        Intent(action="explain",allergies=["海鲜"]),
    ])
    with client_for(tmp_path,catalog,llm) as client:
        initial=client.post("/chat",json=dict(user_id=3,message="1人晚餐，整桌避开海鲜过敏原，3菜0汤，没有其他忌口。")).json()
        later=client.post("/chat",json=dict(user_id=3,session_id=initial["conversation_state"]["session_id"],
            message="只解释菜单：我对海鲜过敏，不换菜，不修改要求。")).json()
    assert owner(initial)["allergies"] == owner(later)["allergies"] == []
    assert initial["menu"] == later["menu"]
