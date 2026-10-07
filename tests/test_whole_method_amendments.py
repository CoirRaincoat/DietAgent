"""Explicitly retract an old food-method, without general last-mention wins."""

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.domain.models import Intent, ScopedMethod
from app.domain.scoped_methods import amend_scoped_methods, explicit_scoped_methods
from app.infrastructure.settings import Settings
from tests.test_provider_recipe_generation import VariableProvider
from tests.test_recipe_generation import request_intent, sample_catalog


def req(method, food="豆腐", slot=None, required=True):
    return ScopedMethod(food=food, method=method, slot=slot, required=required)


def amend(existing, message, **kwargs):
    return amend_scoped_methods(
        existing, explicit_scoped_methods(message), message=message, **kwargs
    )


def test_explicit_global_contrast_retracts_only_the_named_old_method():
    unrelated = req("煎", "鸡胸肉")
    another_tofu_method = req("炒")
    assert amend(
        [req("煎"), unrelated, another_tofu_method],
        "豆腐不要煎改成蒸，重新安排整餐。",
    ) == [unrelated, another_tofu_method, req("蒸")]


def test_previous_slot_binding_is_preserved_when_changing_its_food_method():
    assert amend([req("煎", slot=2)], "豆腐不要煎改成蒸，重新安排整餐。") == [
        req("蒸", slot=2),
        req("蒸"),
    ]


def test_literal_completed_particle_does_not_change_contrast_scope():
    assert amend([req("煎")], "豆腐不要煎了改成蒸，重新安排整餐。") == [req("蒸")]


def test_conflicting_contrasts_keep_both_new_slot_requests():
    assert amend([req("煎", slot=2)], "豆腐不要煎改成蒸，豆腐不要煎改成炒") == [
        req("蒸", slot=2),
        req("炒", slot=2),
        req("蒸"),
        req("炒"),
    ]


@pytest.mark.parametrize(
    "message",
    [
        "豆腐要蒸",
        "偏好蒸豆腐",
        "豆腐不要炒改成蒸",
        "老豆腐不要煎改成蒸",
        "解释：豆腐不要煎改成蒸",
        "如果豆腐不要煎改成蒸",
        "是否豆腐不要煎改成蒸？",
        "“豆腐不要煎改成蒸”",
    ],
)
def test_other_methods_subtypes_soft_mentions_and_quoted_text_do_not_retract(message):
    old = req("煎")
    assert old in amend([old], message)


def test_model_incoming_request_without_literal_contrast_cannot_retract():
    old = req("煎")
    assert amend_scoped_methods([old], [req("蒸")], message="重新安排整餐") == [
        old,
        req("蒸"),
    ]


def test_two_new_contradictory_requests_are_both_retained():
    assert amend([req("煎")], "豆腐不要煎改成蒸，豆腐要煎") == [req("蒸"), req("煎")]


def test_local_request_cannot_use_contrast_to_withdraw_untouched_global_witness():
    from tests.test_local_method_amendments import tofu

    old = req("煎")
    assert amend(
        [old],
        "只换第2道，豆腐不要煎改成蒸",
        local=True,
        replace_slot=2,
        menu=[tofu("煎"), tofu("煎")],
    ) == [old, req("蒸", slot=2)]


def settings(tmp_path):
    return Settings(
        _env_file=None,
        deepseek_api_key="",
        allow_recipe_generation=False,
        local_profile_path=None,
        session_db=tmp_path / "state.db",
    )


def test_actual_default_whole_method_change_is_feasible_and_survives_restart(tmp_path):
    provider = VariableProvider(
        [
            request_intent(excluded_ingredients=["蒜"], allergies=["花生"]),
            Intent(action="plan"),
        ]
    )
    with TestClient(create_app(settings=settings(tmp_path), llm=provider)) as client:
        initial = client.post(
            "/chat",
            json=dict(
                user_id=900003,
                message="两人晚餐，3菜0汤，纯素不辣，不放蒜，花生过敏，豆腐要煎，没有其他忌口。",
            ),
        ).json()
        assert initial["status"] == "ok"
        sid = initial["conversation_state"]["session_id"]
        body = dict(
            user_id=900003,
            session_id=sid,
            message="豆腐不要煎改成蒸，重新安排整餐。",
            request_id="method-update",
        )
        changed = client.post("/chat", json=body).json()
        assert changed["status"] == "ok"
        assert changed["menu"][1]["name"] == "清蒸豆腐（新生成）"
        before = initial["conversation_state"]["constraints"]
        after = changed["conversation_state"]["constraints"]
        assert after["scoped_methods"] == [req("蒸").model_dump()]
        assert {k: v for k, v in before.items() if k != "scoped_methods"} == {
            k: v for k, v in after.items() if k != "scoped_methods"
        }
        assert client.post("/chat", json=body).json() == changed
        assert (
            client.post(
                "/chat", json=dict(user_id=900001, session_id=sid, message="继续")
            ).status_code
            == 409
        )
    provider = VariableProvider([Intent(action="explain"), Intent()])
    with TestClient(create_app(settings=settings(tmp_path), llm=provider)) as client:
        for message in ("解释当前菜单，不换菜", "继续"):
            result = client.post(
                "/chat", json=dict(user_id=900003, session_id=sid, message=message)
            ).json()
            assert result["status"] == "ok" and result["menu"] == changed["menu"]
    assert provider.requests == []


@pytest.mark.parametrize(
    "message",
    [
        "解释：豆腐不要煎改成蒸，不换菜。",
        "不要修改菜单，豆腐不要煎改成蒸。",
    ],
)
def test_readonly_request_does_not_withdraw_food_method(tmp_path, message):
    provider = VariableProvider([request_intent(), Intent(action="plan")])
    with TestClient(
        create_app(settings=settings(tmp_path), catalog=sample_catalog(), llm=provider)
    ) as client:
        initial = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                message="两人晚餐，3菜0汤，纯素不辣，豆腐要煎，没有其他忌口。",
            ),
        ).json()
        changed = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                session_id=initial["conversation_state"]["session_id"],
                message=message,
            ),
        ).json()
        assert (
            changed["conversation_state"]["constraints"]
            == initial["conversation_state"]["constraints"]
        )
        assert (
            changed["conversation_state"]["menu_ids"]
            == initial["conversation_state"]["menu_ids"]
        )
        assert provider.requests == []


def test_whole_method_change_prefers_compatible_original_recipe(tmp_path):
    provider = VariableProvider([request_intent(), Intent(action="plan")])
    catalog = sample_catalog(include_tofu=True)
    with TestClient(
        create_app(settings=settings(tmp_path), catalog=catalog, llm=provider)
    ) as client:
        initial = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                message="两人晚餐，3菜0汤，纯素不辣，豆腐要煎，没有其他忌口。",
            ),
        ).json()
        assert initial["status"] == "ok" and any(
            item["provenance"]["origin"] == "generated" for item in initial["menu"]
        )
        changed = client.post(
            "/chat",
            json=dict(
                user_id=900001,
                session_id=initial["conversation_state"]["session_id"],
                message="豆腐不要煎改成蒸，重新安排整餐。",
            ),
        ).json()
        assert changed["status"] == "ok"
        assert any(item["name"] == "原库清蒸豆腐" for item in changed["menu"])
        assert all(
            item["provenance"]["origin"] != "generated" for item in changed["menu"]
        )
        assert not any(
            event["name"] == "recipe_generate" for event in changed["tool_calls"]
        )
        assert provider.requests == []
