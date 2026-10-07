"""Latest literal local methods override only their witnessed current target."""

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import Intent, ScopedMethod
from app.domain.scoped_methods import amend_scoped_methods, explicit_scoped_methods
from app.infrastructure.settings import Settings
from tests.test_explicit_method_protection import dish
from tests.test_provider_recipe_generation import VariableProvider
from tests.test_recipe_generation import request_intent, sample_catalog


@pytest.mark.parametrize(
    "message,status",
    [
        ("不要修改菜单，但只把第2道换成蒸豆腐。", "ok"),
        ("不要修改菜单，但只换第2道。豆腐要蒸。", "clarification_required"),
    ],
)
def test_readonly_conflict_does_not_withdraw_a_previous_method_requirement(
    tmp_path, message, status
):
    provider = VariableProvider(
        [request_intent(), Intent(action="replace", replace_slot=2)]
    )
    settings = Settings(
        _env_file=None,
        deepseek_api_key="",
        allow_recipe_generation=False,
        session_db=tmp_path / "state.db",
    )
    with TestClient(
        create_app(settings=settings, catalog=sample_catalog(), llm=provider)
    ) as client:
        first = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                message="两人晚餐，3菜0汤，纯素不辣，豆腐要煎，没有其他忌口。",
            ),
        ).json()
        result = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                session_id=first["conversation_state"]["session_id"],
                message=message,
            ),
        ).json()
        assert result["status"] == status
        assert (
            result["conversation_state"]["constraints"]
            == first["conversation_state"]["constraints"]
        )
        assert (
            result["conversation_state"]["menu_ids"]
            == first["conversation_state"]["menu_ids"]
        )
        assert provider.requests == []


def test_actual_local_edit_keeps_an_untouched_global_witness(tmp_path):
    from app.domain.models import Constraints, SessionState
    from app.infrastructure.sessions import SessionStore
    from tests.test_agent_api import ScriptedLLM, client_for
    from tests.test_independent_meat_quota import public_catalog

    first_tofu = tofu("煎")
    second_tofu = tofu("煎").model_copy(
        update={"recipe_id": "second-fried-tofu", "name": "香煎豆腐"}
    )
    steam = tofu("蒸")
    soup = dish("冬瓜汤", "冬瓜煮熟后盛入碗中。", foods="冬瓜200克；水500克")
    constraints = Constraints(
        people=2,
        dish_count=3,
        soup_count=1,
        no_spicy=True,
        diet_mode="vegan",
        scoped_methods=[request("煎")],
    )
    sid = "e" * 32
    SessionStore(tmp_path / "state.db").save(
        SessionState(
            session_id=sid,
            user_id=3,
            menu_ids=[first_tofu.recipe_id, second_tofu.recipe_id, soup.recipe_id],
            menu_valid=True,
            constraints=constraints,
            meal_constraints=constraints.model_copy(deep=True),
            confirmed_fields=["people", "meal_type", "restrictions"],
            menu_structure_explicit=True,
        ),
        None,
    )
    with client_for(
        tmp_path,
        public_catalog([first_tofu, second_tofu, steam, soup]),
        ScriptedLLM(
            [Intent(action="explain"), Intent(action="replace", replace_slot=2)]
        ),
    ) as client:
        original = client.post(
            "/chat",
            json=dict(user_id=3, session_id=sid, message="解释当前菜单，不换菜"),
        ).json()
        assert original["status"] == "ok"
        changed = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="只把第2道换成蒸豆腐")
        ).json()
        assert changed["status"] == "ok"
        assert (
            changed["menu"][0] == original["menu"][0]
            and changed["menu"][2] == original["menu"][2]
        )
        assert changed["menu"][1]["name"] == steam.name
        assert changed["conversation_state"]["constraints"]["scoped_methods"] == [
            request("煎").model_dump(),
            request("蒸", 2).model_dump(),
        ]


def test_wrong_model_slot_cannot_amend_a_different_literal_target(tmp_path):
    provider = VariableProvider(
        [request_intent(), Intent(action="replace", replace_slot=1)]
    )
    settings = Settings(
        _env_file=None,
        deepseek_api_key="",
        allow_recipe_generation=False,
        session_db=tmp_path / "state.db",
    )
    with TestClient(
        create_app(settings=settings, catalog=sample_catalog(), llm=provider)
    ) as client:
        first = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                message="两人晚餐，3菜0汤，纯素不辣，豆腐要煎，没有其他忌口。",
            ),
        ).json()
        result = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                session_id=first["conversation_state"]["session_id"],
                message="只把第2道换成蒸豆腐",
            ),
        ).json()
        assert result["status"] == "clarification_required"
        assert (
            result["conversation_state"]["constraints"]
            == first["conversation_state"]["constraints"]
        )
        assert (
            result["conversation_state"]["menu_ids"]
            == first["conversation_state"]["menu_ids"]
        )
        assert result["tool_calls"] == []
        assert provider.requests == []


def request(method, slot=None, food="豆腐", required=True):
    return ScopedMethod(food=food, method=method, slot=slot, required=required)


def tofu(method, name="豆腐"):
    return dish(method + name, f"将{name}{method}熟后装盘。", foods=f"{name}200克")


def test_only_target_witness_can_be_replaced_without_global_replanning():
    old = request("煎")
    unrelated = request("蒸", 1, "南瓜")
    current = [dish("蒸南瓜", "南瓜蒸熟后装盘。", foods="南瓜200克"), tofu("煎")]
    incoming = explicit_scoped_methods("只把第2道换成蒸豆腐")
    assert amend_scoped_methods(
        [old, unrelated], incoming, menu=current, local=True, replace_slot=2
    ) == [unrelated, request("蒸", 2)]


def test_untouched_global_witness_and_unrelated_requirement_are_retained():
    old = request("煎")
    fish = request("蒸", food="鱼")
    assert amend_scoped_methods(
        [old, fish],
        [request("蒸", 2)],
        menu=[tofu("煎"), tofu("煎")],
        local=True,
        replace_slot=2,
    ) == [old, fish, request("蒸", 2)]


@pytest.mark.parametrize("slot", [None, 0, 3])
def test_unconfirmed_or_invalid_target_cannot_retract_any_requirement(slot):
    old = request("煎")
    assert amend_scoped_methods(
        [old], [request("蒸", 2)], menu=[tofu("煎")], local=True, replace_slot=slot
    ) == [old]


def test_wrong_food_missing_witness_and_soft_amendment_cannot_erase_old_hard_request():
    old = request("煎")
    for incoming, current in (
        (request("蒸", 1, "南瓜"), tofu("煎")),
        (request("蒸", 1), tofu("炒")),
        (request("蒸", None, required=False), tofu("煎")),
    ):
        amended = amend_scoped_methods(
            [old], [incoming], menu=[current], local=True, replace_slot=1
        )
        assert old in amended


def test_component_names_and_cached_methods_do_not_prove_a_superseded_witness():
    old = request("煎")
    current = tofu("炒").model_copy(update={"methods": ["煎"], "name": "煎豆腐"})
    assert old in amend_scoped_methods(
        [old], [request("蒸", 1)], menu=[current], local=True, replace_slot=1
    )


def test_unbound_clause_is_bound_to_the_confirmed_local_slot():
    incoming = explicit_scoped_methods("只换第2道，豆腐要蒸")
    assert amend_scoped_methods(
        [request("煎")],
        incoming,
        menu=[tofu("炒"), tofu("煎")],
        local=True,
        replace_slot=2,
    ) == [request("蒸", 2)]


def test_two_conflicting_new_clauses_are_not_silently_last_wins():
    incoming = explicit_scoped_methods("第2道换成蒸豆腐，第2道换成煎豆腐")
    assert amend_scoped_methods(
        [request("煎")],
        incoming,
        menu=[tofu("炒"), tofu("煎")],
        local=True,
        replace_slot=2,
    ) == [request("蒸", 2), request("煎", 2)]


def test_local_amendment_is_not_permission_for_a_global_food_substitution():
    old = request("煎", food="老豆腐")
    assert amend_scoped_methods(
        [old],
        [request("蒸", 1)],
        menu=[tofu("煎", "老豆腐")],
        local=True,
        replace_slot=1,
    ) == [request("蒸", 1)]
    # No relation between a tofu request and a chicken requirement.
    old = request("煎", food="鸡胸肉")
    assert old in amend_scoped_methods(
        [old], [request("蒸", 1)], menu=[tofu("煎")], local=True, replace_slot=1
    )


def test_actual_default_local_switch_preserves_other_slots_and_survives_restart(
    tmp_path,
):
    provider = VariableProvider(
        [
            request_intent(excluded_ingredients=["蒜"], allergies=["花生"]),
            Intent(action="replace", replace_slot=2),
        ]
    )
    settings = Settings(
        _env_file=None,
        deepseek_api_key="",
        allow_recipe_generation=False,
        session_db=tmp_path / "state.db",
    )
    with TestClient(
        create_app(settings=settings, catalog=sample_catalog(), llm=provider)
    ) as client:
        first = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                message="两人晚餐纯素不辣，3菜0汤，豆腐要煎，不放蒜，花生过敏，没有其他忌口。",
            ),
        ).json()
        assert first["status"] == "ok"
        sid = first["conversation_state"]["session_id"]
        body = dict(
            user_id=900001,
            session_id=sid,
            request_id="local-steam",
            message="只把第2道换成蒸豆腐",
        )
        changed = client.post("/chat", json=body).json()
        assert changed["status"] == "ok"
        assert changed["menu"][1]["name"] == "清蒸豆腐（新生成）"
        assert changed["menu"][0] == first["menu"][0]
        assert changed["menu"][2] == first["menu"][2]
        c = changed["conversation_state"]["constraints"]
        assert c["scoped_methods"] == [request("蒸", 2).model_dump()]
        assert c["allergies"] == ["花生"] and c["excluded_ingredients"] == ["蒜"]
        assert (
            c["no_spicy"]
            and c["diet_mode"] == "vegan"
            and c["dish_count"] == 3
            and c["soup_count"] == 0
        )
        assert client.post("/chat", json=body).json() == changed
        assert provider.requests == []
    provider = VariableProvider(
        [Intent(action="explain"), Intent(), Intent(action="replace", replace_slot=2)]
    )
    with TestClient(
        create_app(settings=settings, catalog=sample_catalog(), llm=provider)
    ) as client:
        for message in ("解释当前菜单，不换菜", "继续"):
            result = client.post(
                "/chat", json=dict(user_id=900001, session_id=sid, message=message)
            ).json()
            assert result["menu"] == changed["menu"]
        back = client.post(
            "/chat",
            json=dict(user_id=900001, session_id=sid, message="只把第2道换成煎豆腐"),
        ).json()
        assert back["status"] == "ok" and back["menu"] == first["menu"]
        assert provider.requests == []
