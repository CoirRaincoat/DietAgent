"""Public development contracts: ownership, transaction and recommendation lifecycle."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.agent.service import MealAgent
from app.domain.models import ChatResult, Intent, MenuItem, RecommendedMeal, SessionState
from app.infrastructure.sessions import SessionConflict, SessionStore
from tests.test_agent_api import ScriptedLLM, client_for
from tests.test_cross_meal_rotation import catalog, first_intent, names


def receipt(name="蒸白菜", key="r1"):
    return RecommendedMeal(meal_type="晚餐", recipe_ids=[key], recipe_names=[name])


def prepared(store, user=3, sequence=0, recommendation=None):
    value = recommendation or receipt()
    state = SessionState(
        session_id=uuid4().hex,
        user_id=user,
        revision=1,
        meal_sequence=sequence,
        menu_ids=value.recipe_ids,
        menu_valid=True,
    )
    store.save(state, None)
    return ChatResult(
        status="ok",
        reason="source",
        conversation_state=state,
        menu=[
            MenuItem(slot=i + 1, recipe_id=key, name=name, ingredients=[], steps="蒸熟装盘。")
            for i, (key, name) in enumerate(zip(value.recipe_ids, value.recipe_names))
        ],
    )


def test_user_history_is_empty_without_a_completed_recommendation(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    result = prepared(store)
    assert store.recommendation_history(3).recommendations == []
    store.complete(result, 1, None, "h")
    assert store.recommendation_history(3).recommendations == []


def test_user_history_crosses_sessions_not_user_ids_and_survives_restart(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    a = prepared(store, recommendation=receipt())
    b = prepared(store, recommendation=receipt("蒸菠菜", "r2"))
    other = prepared(store, user=4, recommendation=receipt("蒸鸡蛋", "r3"))
    for result in (a, b, other):
        value = receipt(result.menu[0].name, result.menu[0].recipe_id)
        store.complete(result, 1, None, "h", recommendation=value)
    history = SessionStore(tmp_path / "state.db").recommendation_history(3)
    assert [r.recipe_names for r in history.recommendations] == [["蒸白菜"], ["蒸菠菜"]]
    assert history.revision == 2
    assert store.recommendation_history(4).recommendations[0].recipe_names == ["蒸鸡蛋"]
    assert store.recommendation_history(5).recommendations == []
    excluded = store.recommendation_history(3, exclude=(b.conversation_state.session_id, 0))
    assert [r.recipe_names for r in excluded.recommendations] == [["蒸白菜"]]


def test_identical_completion_is_one_meal_and_local_change_updates_without_reordering(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    a = prepared(store)
    store.complete(a, 1, None, "h", recommendation=receipt())
    store.complete(a, 1, None, "h", recommendation=receipt())
    assert store.recommendation_history(3).revision == 1
    b = prepared(store, recommendation=receipt("蒸鸡蛋", "r2"))
    store.complete(b, 1, None, "h", recommendation=receipt("蒸鸡蛋", "r2"))
    a.menu[0].name = "蒸菠菜"
    a.menu[0].recipe_id = "r3"
    a.conversation_state.menu_ids = ["r3"]
    store.complete(a, 1, None, "h", recommendation=receipt("蒸菠菜", "r3"))
    assert [r.recipe_names for r in store.recommendation_history(3).recommendations] == [
        ["蒸菠菜"],
        ["蒸鸡蛋"],
    ]
    assert store.recommendation_history(3).revision == 3


def test_stale_cross_session_history_rolls_back_result_and_receipt(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    a, b = prepared(store), prepared(store, recommendation=receipt("蒸鸡蛋", "r2"))
    store.complete(a, 1, "a", "ha", recommendation=receipt(), expected_history_revision=0)
    with pytest.raises(SessionConflict):
        store.complete(
            b, 1, "b", "hb", recommendation=receipt("蒸鸡蛋", "r2"), expected_history_revision=0
        )
    assert store.replay(b.conversation_state.session_id, "b", "hb", 1) is None
    assert len(store.recommendation_history(3).recommendations) == 1
    assert store.recommendation_history(3).revision == 1


def test_failed_request_insert_rolls_back_menu_history_and_session(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    result = prepared(store)
    store.complete(result, 1, "same", "h", recommendation=receipt())
    result.menu[0].name = "蒸菠菜"
    result.menu[0].recipe_id = "r2"
    result.conversation_state.menu_ids = ["r2"]
    with pytest.raises(sqlite3.IntegrityError):
        store.complete(result, 1, "same", "h", recommendation=receipt("蒸菠菜", "r2"))
    assert store.recommendation_history(3).recommendations[0].recipe_names == ["蒸白菜"]
    assert store.recommendation_history(3).revision == 1
    assert store.get(result.conversation_state.session_id, 3).menu_ids == ["r1"]


def test_completion_cannot_reassign_another_users_session(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    result = prepared(store)
    result.conversation_state.user_id = 4
    with pytest.raises(SessionConflict):
        store.complete(result, 1, None, "h", recommendation=receipt())
    assert store.get(result.conversation_state.session_id, 3).user_id == 3
    assert store.recommendation_history(4).recommendations == []


@pytest.mark.parametrize("invalid", ["failure", "wrong_name", "wrong_id", "invalid_menu"])
def test_history_receipt_must_match_a_completed_valid_menu(tmp_path, invalid):
    store = SessionStore(tmp_path / "state.db")
    result = prepared(store)
    r = receipt()
    if invalid == "failure":
        result.status = "no_feasible_menu"
    if invalid == "wrong_name":
        r.recipe_names = ["虚构菜单"]
    if invalid == "wrong_id":
        r.recipe_ids = ["fake"]
    if invalid == "invalid_menu":
        result.conversation_state.menu_valid = False
    with pytest.raises(ValueError):
        store.complete(result, 1, None, "h", recommendation=r)
    assert store.recommendation_history(3).recommendations == []


def test_history_read_is_bounded_and_exclusion_does_not_change_version(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    for i in range(12):
        r = receipt("菜" + str(i), str(i))
        result = prepared(store, recommendation=r)
        store.complete(result, 1, None, "h", recommendation=r)
    before = store.recommendation_history(3)
    assert [r.recipe_names for r in before.recommendations] == [
        ["菜" + str(i)] for i in range(4, 12)
    ]
    assert (
        store.recommendation_history(3, exclude=(result.conversation_state.session_id, 0)).revision
        == before.revision
        == 12
    )


def test_actual_new_sessions_use_user_history_and_replay_without_extra_receipts(tmp_path):
    data = catalog()
    llm = ScriptedLLM([first_intent(), first_intent(), first_intent()])
    with client_for(tmp_path, data, llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        request = dict(user_id=3, request_id="one", message="1人晚餐，不辣，花生过敏")
        first = client.post("/chat", json=request).json()
        replay = client.post("/chat", json=request).json()
        assert replay == first and llm.parse_calls == 1
        second = client.post("/chat", json=dict(request, request_id="two")).json()
        assert second["status"] == "ok" and names(second) != names(first)
        other = client.post("/chat", json=dict(request, user_id=4, request_id="other")).json()
        assert names(other) == names(first)
    store = SessionStore(tmp_path / "state.db")
    assert len(store.recommendation_history(3).recommendations) == 2
    assert len(store.recommendation_history(4).recommendations) == 1


def test_rejected_failed_meal_retains_last_recommendation_for_next_meal(tmp_path):
    llm = ScriptedLLM([first_intent(), Intent(action="reject", dish_count=8), Intent(dish_count=3)])
    with client_for(tmp_path, catalog(), llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，不辣，花生过敏")).json()
        sid = first["conversation_state"]["session_id"]
        failed = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="这些都不要，换成8道菜")
        ).json()
        assert failed["status"] == "no_feasible_menu" and failed["menu"] == []
        assert failed["conversation_state"]["last_recommendation"]["recipe_names"] == names(first)
        next_meal = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="安排下一餐，3道菜，沿用原要求")
        ).json()
        assert next_meal["status"] == "ok"
        assert next_meal["conversation_state"]["recent_recommendations"][0][
            "recipe_names"
        ] == names(first)
        assert next_meal["conversation_state"]["rejected_recipe_ids"] == []
        assert next_meal["conversation_state"]["constraints"]["allergies"] == ["花生"]


def test_concurrent_store_writers_cannot_both_publish_the_same_history_revision(tmp_path):
    store = SessionStore(tmp_path / "state.db")
    results = [prepared(store), prepared(store, recommendation=receipt("蒸菠菜", "r2"))]
    barrier = Barrier(2)

    def publish(result):
        local = SessionStore(tmp_path / "state.db")
        r = receipt(result.menu[0].name, result.menu[0].recipe_id)
        barrier.wait(timeout=10)
        try:
            local.complete(
                result,
                1,
                result.conversation_state.session_id,
                "h",
                recommendation=r,
                expected_history_revision=0,
            )
            return "published"
        except SessionConflict:
            return "history_conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(publish, results))
    assert sorted(statuses) == ["history_conflict", "published"]
    assert store.recommendation_history(3).revision == 1
    assert len(store.recommendation_history(3).recommendations) == 1
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_same_process_user_lock_serializes_new_conversations_without_cross_user_pollution(tmp_path):
    class YieldingLLM(ScriptedLLM):
        async def parse(self, message, state, profile):
            await asyncio.sleep(0)
            return await super().parse(message, state, profile)

    store = SessionStore(tmp_path / "state.db")
    agent = MealAgent(
        catalog(),
        store,
        YieldingLLM([first_intent() for _ in range(3)]),
        experiment_cross_meal_rotation=True,
    )

    async def run():
        return await asyncio.gather(
            agent.chat(3, "1人晚餐，不辣，花生过敏", request_id="a"),
            agent.chat(3, "1人晚餐，不辣，花生过敏", request_id="b"),
            agent.chat(4, "1人晚餐，不辣，花生过敏", request_id="c"),
        )

    a, b, c = asyncio.run(run())
    assert a.status == b.status == c.status == "ok"
    assert [v.name for v in a.menu] != [v.name for v in b.menu]
    assert [v.name for v in a.menu] == [v.name for v in c.menu]
    assert len(store.recommendation_history(3).recommendations) == 2
    assert len(store.recommendation_history(4).recommendations) == 1


def test_history_version_private_attr_cannot_be_chosen_by_model_json():
    with pytest.raises(ValueError):
        SessionState.model_validate(
            dict(session_id="a" * 32, user_id=3, _recommendation_history_revision=999)
        )
    state = SessionState(session_id="a" * 32, user_id=3)
    state._recommendation_history_revision = 2
    assert "_recommendation_history_revision" not in state.model_dump()
    assert "_recommendation_history_revision" not in SessionState.model_json_schema()["properties"]


def test_replay_after_another_session_updates_history_does_not_publish_again(tmp_path):
    llm = ScriptedLLM([first_intent(), first_intent()])
    with client_for(tmp_path, catalog(), llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        request = dict(user_id=3, request_id="a", message="1人晚餐，不辣，花生过敏")
        first = client.post("/chat", json=request).json()
        second = client.post("/chat", json=dict(request, request_id="b")).json()
        assert second["status"] == "ok"
        assert client.post("/chat", json=request).json() == first
        assert llm.parse_calls == 2
    history = SessionStore(tmp_path / "state.db").recommendation_history(3)
    assert history.revision == 2 and len(history.recommendations) == 2


def test_new_session_unknown_allergy_cannot_use_history_to_skip_clarification(tmp_path):
    unknown = first_intent().model_copy(update={"allergies": ["神秘配料"]})
    with client_for(tmp_path, catalog(), ScriptedLLM([first_intent(), unknown])) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，不辣，花生过敏")).json()
        failed = client.post("/chat", json=dict(user_id=3, message="1人晚餐，神秘配料过敏")).json()
        assert failed["status"] == "clarification_required" and failed["menu"] == []
        assert failed["conversation_state"]["pending_allergy"]
    history = SessionStore(tmp_path / "state.db").recommendation_history(3)
    assert len(history.recommendations) == 1
    assert history.recommendations[0].recipe_names == names(first)


def test_explicit_compatibility_opt_out_does_not_use_or_write_existing_history(tmp_path):
    data = catalog()
    with client_for(tmp_path, data, ScriptedLLM([first_intent()])) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，不辣，花生过敏")).json()
    with client_for(tmp_path, data, ScriptedLLM([first_intent()])) as client:
        client.app.state.agent.experiment_cross_meal_rotation = False
        second = client.post(
            "/chat", json=dict(user_id=3, message="1人晚餐，不辣，花生过敏")
        ).json()
        assert names(first) == names(second)
        assert second["conversation_state"]["recent_recommendations"] == []
        assert second["conversation_state"]["last_recommendation"] is None
    history = SessionStore(tmp_path / "state.db").recommendation_history(3)
    assert len(history.recommendations) == 1 and history.revision == 1


def test_successful_local_edit_updates_last_snapshot_and_one_meal_receipt(tmp_path):
    llm = ScriptedLLM(
        [first_intent(), Intent(action="replace", replace_slot=2), Intent(action="explain")]
    )
    with client_for(tmp_path, catalog(), llm) as client:
        client.app.state.agent.experiment_cross_meal_rotation = True
        first = client.post("/chat", json=dict(user_id=3, message="1人晚餐，不辣，花生过敏")).json()
        sid = first["conversation_state"]["session_id"]
        local = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="只换第二道")
        ).json()
        explained = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="解释菜单")
        ).json()
        assert local["conversation_state"]["last_recommendation"]["recipe_names"] == names(local)
        assert local["conversation_state"]["last_recommendation_sequence"] == 0
        assert names(explained) == names(local)
    history = SessionStore(tmp_path / "state.db").recommendation_history(3)
    assert len(history.recommendations) == 1 and history.revision == 2
    assert history.recommendations[0].recipe_names == names(local)
