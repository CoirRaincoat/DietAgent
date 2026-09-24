"""Explicit menu rejection survives later turns; local replacement stays local."""

import pytest

from app.agent.service import MealAgent
from app.domain.models import Ingredient, Intent, Recipe, UserProfile
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore


class ScriptedLLM(BaseLLM):
    def __init__(self, intents):
        self.intents = list(intents)
        self.parse_calls = 0

    async def parse(self, message, state, profile):
        self.parse_calls += 1
        return self.intents.pop(0)

    async def explain(self, facts):
        return list(facts)

    async def aclose(self):
        pass


def catalog_with(count):
    profile = UserProfile(
        data_scope="synthetic", user_id=1, age=30, sex="女",
        height_cm=165, weight_kg=55, bmi=20.2,
    )
    recipes = {}
    for index in range(count):
        key = f"dish_{index:02}"
        recipes[key] = Recipe(
            recipe_id=key, name=f"清蒸南瓜{index}", raw_ingredients="南瓜；水",
            ingredients=[Ingredient(raw="南瓜", name="南瓜"), Ingredient(raw="水", name="水")],
            steps="将南瓜蒸熟后装盘。", categories=["vegetable"], methods=["蒸"],
            meal_types=["晚餐"], source_row=index + 2, fingerprint=key,
        )
    return DataCatalog(profiles={1: profile}, recipes=recipes, quality_report={})


def first_intent():
    return Intent(people=1, meal_type="晚餐", restrictions_confirmed=True)


async def first_menu(agent):
    result = await agent.chat(1, "1人晚餐，没有其他忌口", request_id="first")
    assert result.status == "ok"
    return result


def menu_ids(result):
    return [dish.recipe_id for dish in result.menu]


def assert_absent(result, rejected):
    assert rejected.isdisjoint(menu_ids(result))
    assert rejected.isdisjoint(dish.recipe_id for dish in result.replacement_suggestions)


async def test_repeated_rejection_and_later_plan_do_not_repeat_menus_or_suggestions(tmp_path):
    llm = ScriptedLLM([
        first_intent(), Intent(action="reject"), Intent(action="reject"),
        Intent(), Intent(action="explain"),
    ])
    agent = MealAgent(catalog_with(12), SessionStore(tmp_path / "state.db"), llm)
    first = await first_menu(agent)
    sid = first.conversation_state.session_id
    rejected = set(menu_ids(first))
    second = await agent.chat(1, "这份菜单都不要", sid, "reject-one")
    assert second.status == "ok"
    assert_absent(second, rejected)
    assert set(second.conversation_state.rejected_recipe_ids) == rejected
    assert await agent.chat(1, "这份菜单都不要", sid, "reject-one") == second
    assert llm.parse_calls == 2

    rejected.update(menu_ids(second))
    third = await agent.chat(1, "这份也都不要", sid, "reject-two")
    assert third.status == "ok"
    assert_absent(third, rejected)
    assert set(third.conversation_state.rejected_recipe_ids) == rejected

    planned = await agent.chat(1, "再按已有要求安排", sid, "plan-again")
    assert planned.status == "ok"
    assert_absent(planned, rejected)
    assert menu_ids(planned) == menu_ids(third)
    explained = await agent.chat(1, "解释当前菜单", sid, "explain")
    assert explained.status == "ok"
    assert menu_ids(explained) == menu_ids(planned)
    assert not {"recipe_search", "menu_modify"}.intersection(
        event.name for event in explained.tool_calls
    )


async def test_rejection_survives_restart_and_new_session_has_separate_memory(tmp_path):
    catalog = catalog_with(12)
    path = tmp_path / "state.db"
    agent = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        first_intent(), Intent(action="reject"),
    ]))
    first = await first_menu(agent)
    sid = first.conversation_state.session_id
    second = await agent.chat(1, "全部不要", sid, "reject")
    rejected = set(menu_ids(first))
    assert set(SessionStore(path).get(sid, 1).rejected_recipe_ids) == rejected

    restarted = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        Intent(), Intent(action="reject"), first_intent(),
    ]))
    planned = await restarted.chat(1, "继续安排", sid)
    assert_absent(planned, rejected)
    rejected.update(menu_ids(second))
    third = await restarted.chat(1, "这些也全部不要", sid)
    assert third.status == "ok"
    assert_absent(third, rejected)

    fresh = await restarted.chat(1, "1人晚餐，没有其他忌口")
    assert fresh.status == "ok"
    assert fresh.conversation_state.rejected_recipe_ids == []
    assert menu_ids(fresh) == menu_ids(first)


async def test_rejected_same_name_recipe_variant_never_appears_as_menu_or_suggestion(tmp_path):
    catalog = catalog_with(12)
    agent = MealAgent(catalog, SessionStore(tmp_path / "state.db"), ScriptedLLM([
        first_intent(), Intent(action="reject"), Intent(),
    ]))
    first = await first_menu(agent)
    sid = first.conversation_state.session_id
    rejected_names = {dish.name for dish in first.menu}
    # A separate source row with the same name remains the same rejected dish.
    original = catalog.recipes[menu_ids(first)[0]]
    duplicate = original.model_copy(update={"recipe_id": "other_row", "fingerprint": "other"})
    catalog.recipes[duplicate.recipe_id] = duplicate
    restarted = MealAgent(catalog, agent.store, agent.llm)
    for message in ("这些都不要", "继续安排"):
        result = await restarted.chat(1, message, sid)
        assert result.status == "ok"
        assert rejected_names.isdisjoint(
            dish.name for dish in [*result.menu, *result.replacement_suggestions]
        )


async def test_rejection_remains_after_no_feasible_result_and_explanation_does_not_revive_it(tmp_path):
    catalog = catalog_with(3)
    path = tmp_path / "state.db"
    agent = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        first_intent(), Intent(action="reject"),
    ]))
    first = await first_menu(agent)
    sid = first.conversation_state.session_id
    rejected = set(menu_ids(first))
    failure = await agent.chat(1, "这三道都不要", sid, "reject")
    assert failure.status == "no_feasible_menu"
    assert not failure.menu and not failure.replacement_suggestions
    assert set(SessionStore(path).get(sid, 1).rejected_recipe_ids) == rejected
    assert await agent.chat(1, "这三道都不要", sid, "reject") == failure

    restarted = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        Intent(action="explain"), Intent(),
    ]))
    explained = await restarted.chat(1, "解释一下", sid)
    assert explained.status == "clarification_required"
    assert not explained.menu
    assert not {"recipe_search", "menu_modify"}.intersection(
        event.name for event in explained.tool_calls
    )
    planned = await restarted.chat(1, "那继续安排", sid)
    assert planned.status == "no_feasible_menu"
    assert not planned.menu
    assert set(planned.conversation_state.rejected_recipe_ids) == rejected


async def test_rejection_is_saved_before_planner_crash(tmp_path, monkeypatch):
    catalog = catalog_with(9)
    path = tmp_path / "state.db"
    agent = MealAgent(catalog, SessionStore(path), ScriptedLLM([
        first_intent(), Intent(action="reject"),
    ]))
    first = await first_menu(agent)
    sid = first.conversation_state.session_id

    def fail_plan(*args, **kwargs):
        raise RuntimeError("simulated planner failure")

    monkeypatch.setattr(agent.planner, "plan", fail_plan)
    with pytest.raises(RuntimeError, match="simulated planner failure"):
        await agent.chat(1, "这一份全部不要", sid)
    persisted = SessionStore(path).get(sid, 1)
    assert set(persisted.rejected_recipe_ids) == set(menu_ids(first))
    assert not persisted.menu_valid

    restarted = MealAgent(catalog, SessionStore(path), ScriptedLLM([Intent()]))
    recovered = await restarted.chat(1, "继续安排", sid)
    assert recovered.status == "ok"
    assert_absent(recovered, set(menu_ids(first)))


async def test_local_replacement_preserves_other_slots_without_permanent_rejection(tmp_path):
    agent = MealAgent(catalog_with(4), SessionStore(tmp_path / "state.db"), ScriptedLLM([
        first_intent(), Intent(action="replace", replace_slot=2),
        Intent(action="replace", replace_slot=2),
    ]))
    first = await first_menu(agent)
    sid = first.conversation_state.session_id
    second = await agent.chat(1, "只换第二道", sid)
    third = await agent.chat(1, "第二道再换一道", sid)
    before, after, again = menu_ids(first), menu_ids(second), menu_ids(third)
    assert before[0] == after[0] == again[0]
    assert before[2] == after[2] == again[2]
    assert before[1] != after[1]
    assert again[1] == before[1]
    assert second.conversation_state.rejected_recipe_ids == []
    assert third.conversation_state.rejected_recipe_ids == []
