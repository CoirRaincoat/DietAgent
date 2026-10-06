"""Synthetic whole-meal diet requests; raw facts, not returned category verdicts."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.diet_mode import explicit_diet_mode, ground_diet_intent
from app.agent.diners import aggregate_constraints, apply_diner_updates, profile_diner
from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.domain.models import (
    Constraints,
    Diner,
    DinerUpdate,
    Intent,
    Recipe,
    SessionState,
    UserProfile,
)
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.diet import diet_reasons
from app.rules.engine import RuleEngine


def dish(name: str, foods: str, steps: str = "煮熟后装盘即可食用。") -> Recipe:
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": name,
                        "食材清单": foods,
                        "烹饪步骤": steps,
                        "label": "午餐、晚餐",
                    }
                ]
            ).values()
        )
    )


def pool() -> list[Recipe]:
    return [
        dish("蒸鸡肉", "鸡肉200克；盐1克"),
        dish("肉末白菜", "白菜200克；猪肉末20克；盐1克"),
        dish("清炒白菜", "白菜200克；食用油3克；盐1克", "炒熟后装盘。"),
        dish("蒸豆腐", "豆腐200克；盐1克"),
        dish("炒鸡蛋", "鸡蛋100克；盐1克", "炒熟后装盘。"),
        dish("奶油菠菜", "菠菜200克；牛奶50克；盐1克"),
        dish("米饭", "大米200克；水300克"),
        dish("鸡汤", "鸡肉200克；水500克；盐1克"),
        dish("排骨汤", "排骨200克；水500克；盐1克"),
        dish("冬瓜汤", "冬瓜200克；水500克；盐1克"),
        dish("白菜汤", "白菜200克；水500克", "加入高汤煮熟后装盘。"),
        dish("鸡精青菜", "青菜200克；鸡精1克"),
    ]


class DietLLM(BaseLLM):
    """Omit diet fields deliberately: raw requests must remain enforceable."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "只换" in message:
            return Intent(action="replace", replace_slot=2)
        if "解释" in message:
            return Intent(action="explain")
        if state.menu_ids or state.pending_plan:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=1,
            meal_type="晚餐",
            dish_count=3,
            soup_count=1,
            restrictions_confirmed=True,
            no_spicy=True,
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def app_for(tmp_path: Path, recipes: list[Recipe] | None = None, llm: BaseLLM | None = None) -> Any:
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    chosen = pool() if recipes is None else recipes
    settings = Settings.model_construct(session_db=tmp_path / "sessions.db")
    return create_app(
        settings,
        llm or DietLLM(),
        DataCatalog({900001: profile}, {r.recipe_id: r for r in chosen}, {}),
        SessionStore(settings.database_path),
    )


def ask(client: TestClient, message: str, sid: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"user_id": 900001, "message": message}
    if sid:
        body["session_id"] = sid
    response = client.post("/chat", json=body)
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    return result


def assert_no_meat(result: dict[str, Any], *, vegan: bool = False) -> None:
    # Authored fixture foods, no production classification or self-report.
    forbidden = {"鸡肉", "猪肉末", "排骨", "鸡精", "高汤"}
    if vegan:
        forbidden |= {"鸡蛋", "牛奶", "蜂蜜"}
    for item in result["menu"] + result["replacement_suggestions"]:
        assert not forbidden.intersection(item["ingredients"])
        assert not any(food in item["steps"] for food in forbidden)


@pytest.mark.parametrize(
    "message,mode",
    [
        ("一人晚餐共3道含1汤，全餐蛋奶素，没有其他忌口，不辣。", "ovo_lacto_vegetarian"),
        ("一人晚餐共3道含1汤，全餐不吃肉和肉汤，允许蛋奶，没有其他忌口。", "ovo_lacto_vegetarian"),
        ("一人晚餐共3道含1汤，全餐纯素，没有其他忌口。", "vegan"),
    ],
)
def test_whole_meal_diet_includes_soups_steps_and_proposals(
    tmp_path: Path, message: str, mode: str
) -> None:
    with TestClient(app_for(tmp_path)) as client:
        result = ask(client, message)
    assert result["status"] == "ok"
    assert len(result["menu"]) == 3
    assert_no_meat(result, vegan=mode == "vegan")
    assert result["conversation_state"]["constraints"]["diet_mode"] == mode
    assert "整餐" in result["reason"] and "汤" in result["reason"]


def test_diet_change_replans_old_meat_soup_and_survives_restart(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "一人晚餐3道含1汤，无其他忌口，不辣。")
        sid = first["conversation_state"]["session_id"]
        updated = ask(client, "这餐改成蛋奶素，汤和调味也不含肉。", sid)
    with TestClient(app_for(tmp_path)) as client:
        retry = ask(client, "继续，按刚才要求", sid)
    assert first["menu"] != updated["menu"]
    assert updated["status"] == retry["status"] == "ok"
    assert_no_meat(updated)
    assert [d["recipe_id"] for d in updated["menu"]] == [d["recipe_id"] for d in retry["menu"]]


def test_unknown_broth_cannot_fill_whole_meal_vegetarian_soup(tmp_path: Path) -> None:
    recipes = [r for r in pool() if r.name != "冬瓜汤"]
    with TestClient(app_for(tmp_path, recipes)) as client:
        result = ask(client, "一人晚餐3道含1汤，全餐蛋奶素，无其他忌口。")
    assert result["status"] == "no_feasible_menu"
    assert result["menu"] == []


def test_plain_vegetarian_is_clarified_and_not_forgotten(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "一人晚餐3道含1汤，我吃素，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
    with TestClient(app_for(tmp_path)) as client:
        retry = ask(client, "继续", sid)
        clarified = ask(client, "我采用蛋奶素，汤也不含肉。", sid)
    assert first["status"] == retry["status"] == "clarification_required"
    assert first["menu"] == retry["menu"] == []
    assert clarified["status"] == "ok"
    assert_no_meat(clarified)


def test_only_slot_edit_does_not_secretly_expand_for_new_diet(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "一人晚餐3道含1汤，无其他忌口。")
        result = ask(
            client,
            "只换第二道，全餐改成蛋奶素，其余不变。",
            first["conversation_state"]["session_id"],
        )
    assert result["status"] != "ok"
    assert result["menu"] == []


def test_streamed_pure_vegan_reply_has_no_egg_milk_or_meat_soup(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "一人晚餐3道含1汤，全餐纯素，无其他忌口。"}
                ],
            },
        )
    events = [
        line.removeprefix("data: ")
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1] == "[DONE]"
    text = "".join(json.loads(e)["choices"][0]["delta"].get("content", "") for e in events[:-1])
    assert "冬瓜汤" in text
    assert "鸡汤" not in text and "炒鸡蛋" not in text and "奶油菠菜" not in text
    assert "品牌" in text and "整餐" in text


@pytest.mark.parametrize(
    "text,expected,pending",
    [
        ("整餐纯素", "vegan", False),
        ("我采用蛋奶素", "ovo_lacto_vegetarian", False),
        ("整餐不含任何肉类", "ovo_lacto_vegetarian", False),
        ("不是纯素，允许蛋奶", "ovo_lacto_vegetarian", False),
        ("整餐纯素并允许蛋奶", None, True),
        ("我吃素", None, True),
        ("0荤4素", None, False),
        ("不含蛋奶", None, False),
        ("全餐不允许蛋奶", None, False),
        ("不吃纯素", None, False),
        ("解释纯素", None, False),
        ("蛋奶素是什么意思", None, False),
        ("上次采用纯素", None, False),
        ("比如纯素", None, False),
        ("如果蛋奶素", None, False),
        ("纯素吗", None, False),
        ("是否蛋奶素", None, False),
        ("取消素食限制", "omnivore", False),
        ("改成杂食", "omnivore", False),
        ("随便吃", None, False),
    ],
)
def test_mode_parser_requires_actual_mode_not_quotas_or_questions(
    text: str, expected: str | None, pending: bool
) -> None:
    assert explicit_diet_mode(text) == (expected, pending)


@pytest.mark.parametrize(
    "foods,steps,lacto,vegan",
    [
        ("白菜200克；水500克", "煮熟后装盘。", True, True),
        ("白菜200克；猪肉末20克", "煮熟后装盘。", False, False),
        ("白菜200克；水500克", "加入猪油煮熟后装盘。", False, False),
        ("白菜200克；水500克", "加入高汤煮熟后装盘。", False, False),
        ("白菜200克；鸡精1克", "煮熟后装盘。", False, False),
        ("白菜200克；神秘白菜水10克", "煮熟后装盘。", False, False),
        ("白菜200克；鸡蛋100克", "煮熟后装盘。", True, False),
        ("白菜200克；牛奶100克", "煮熟后装盘。", True, False),
        ("白菜200克；羊奶100克", "煮熟后装盘。", True, False),
        ("白菜200克；奶油20克", "煮熟后装盘。", True, False),
        ("白菜200克；蜂蜜2克", "煮熟后装盘。", True, False),
        ("白菜200克；水500克", "加入鸡蛋煮熟后装盘。", True, False),
        ("白菜200克；水500克", "加入蜂蜜煮熟后装盘。", True, False),
        ("白菜200克；水500克", "打入鲜蛋煮熟后装盘。", True, False),
        ("贝贝南瓜200克；蔬菜粒50克", "蒸熟后装盘。", True, True),
        ("羊肚菌200克；鸡腿菇50克", "煮熟后装盘。", True, True),
        ("白菜200克；素肉50克", "煮熟后装盘。", False, False),
        ("白菜200克；明胶5克", "煮熟后装盘。", False, False),
    ],
)
@pytest.mark.parametrize("name", ["白菜", "白菜汤", "白菜米饭"])
def test_diet_rules_use_full_source_not_soup_staple_or_protein_labels(
    foods: str, steps: str, lacto: bool, vegan: bool, name: str
) -> None:
    recipe = dish(name, foods, steps)
    assert (not diet_reasons(recipe, Constraints(diet_mode="ovo_lacto_vegetarian"))) is lacto
    assert (not diet_reasons(recipe, Constraints(diet_mode="vegan"))) is vegan
    assert diet_reasons(recipe, Constraints()) == []


def test_incomplete_source_is_not_certified_even_when_parsed_names_look_plant() -> None:
    recipe = dish("白菜", "白菜200克").model_copy(
        update={"quality_flags": ["unparsed_ingredients"]}
    )
    assert diet_reasons(recipe, Constraints(diet_mode="ovo_lacto_vegetarian"))


def test_named_modes_are_aggregated_only_for_attending_people() -> None:
    owner = Diner(diner_id="owner", display_name="用户", aliases=["我"], diet_mode="omnivore")
    mom = Diner(diner_id="mom", display_name="妈妈", aliases=["我妈"], diet_mode="vegan")
    aggregate = aggregate_constraints(Constraints(), [owner, mom])
    assert aggregate.diet_mode == "vegan"
    absent = apply_diner_updates(
        [owner, mom], [DinerUpdate(diner="我妈", attendance=False)], session_id="test"
    )
    assert absent[1].diet_mode == "vegan"
    assert aggregate_constraints(Constraints(), absent).diet_mode == "omnivore"
    returned = apply_diner_updates(
        absent, [DinerUpdate(diner="妈妈", attendance=True)], session_id="test"
    )
    assert aggregate_constraints(Constraints(), returned).diet_mode == "vegan"
    assert returned[1].diner_id == "mom"


def test_named_ambiguous_diet_is_not_resolved_by_another_person() -> None:
    result = ground_diet_intent("妈妈吃素，用户采用蛋奶素", Intent(), [])
    assert result.pending_owners == {"妈妈"}
    assert result.owner_modes == {"用户": "ovo_lacto_vegetarian"}
    assert result.meal_mode is None


@pytest.mark.parametrize("message", ["妈妈纯素，妈妈蛋奶素", "她蛋奶素", "我和妈妈采用纯素"])
def test_owner_conflicts_or_pronouns_are_clarified_not_guessed(message: str) -> None:
    result = ground_diet_intent(message, Intent(), [])
    assert result.pending_meal or result.pending_owners
    assert not result.owner_modes


def test_quote_or_explanation_does_not_mutate_even_wrong_model_diet() -> None:
    result = ground_diet_intent(
        "解释，纯素",
        Intent(
            action="explain",
            diet_mode="vegan",
            diner_updates=[DinerUpdate(diner="妈妈", diet_mode="vegan")],
        ),
        [],
    )
    assert result.intent.diet_mode is None
    assert result.intent.diner_updates[0].diet_mode is None
    assert result.pending_meal is False


def test_model_guesses_cannot_change_table_or_owner_diet() -> None:
    result = ground_diet_intent(
        "一人晚餐，荤素搭配",
        Intent(diet_mode="vegan", diner_updates=[DinerUpdate(diner="妈妈", diet_mode="vegan")]),
        [],
    )
    assert result.intent.diet_mode is None
    assert result.intent.diner_updates[0].diet_mode is None
    assert result.pending_meal and result.pending_owners == {"妈妈"}


def test_profile_mode_is_explicit_text_not_derived_from_age_or_health() -> None:
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=70,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
        health_goals=["护心"],
        preferences=["纯素"],
    )
    assert profile_diner(profile).diet_mode == "vegan"
    assert profile_diner(profile.model_copy(update={"preferences": ["吃素"]})).pending_diet_mode
    assert profile_diner(profile.model_copy(update={"preferences": []})).diet_mode == "omnivore"
    assert profile_diner(
        profile.model_copy(update={"preferences": ["纯素", "允许蛋奶"]})
    ).pending_diet_mode


def test_positive_meat_quota_conflicts_with_whole_meal_diet(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        result = ask(client, "一人晚餐3道含1汤，全餐纯素，1荤1素，无其他忌口。")
    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert "冲突" in result["reason"]


def test_only_local_diet_edit_of_valid_menu_preserves_unrelated_slots(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "一人晚餐3道含1汤，全餐蛋奶素，无其他忌口。")
        updated = ask(client, "只换第二道，其他不变", first["conversation_state"]["session_id"])
    assert updated["status"] == "ok"
    assert updated["menu"][1]["recipe_id"] != first["menu"][1]["recipe_id"]
    assert [d["recipe_id"] for i, d in enumerate(updated["menu"]) if i != 1] == [
        d["recipe_id"] for i, d in enumerate(first["menu"]) if i != 1
    ]
    assert_no_meat(updated)


def test_diet_does_not_waive_allergy_or_explicit_food_exclusion() -> None:
    recipe = dish("鸡蛋白菜", "鸡蛋100克；白菜200克")
    assert (
        not RuleEngine()
        .evaluate(recipe, Constraints(diet_mode="ovo_lacto_vegetarian", allergies=["鸡蛋"]))
        .allowed
    )
    assert (
        not RuleEngine()
        .evaluate(
            recipe, Constraints(diet_mode="ovo_lacto_vegetarian", excluded_ingredients=["鸡蛋"])
        )
        .allowed
    )
    plan = MenuPlanner(RuleEngine()).plan(
        pool(), Constraints(dish_count=3, soup_count=1, meat_dish_count=1, diet_mode="vegan")
    )
    assert plan.failure is not None and plan.recipes == []


@pytest.mark.parametrize(
    "text", ["我们采用纯素", "我朋友采用纯素", "妈妈的朋友采用纯素", "我妈的朋友纯素"]
)
def test_prefix_or_group_is_not_the_profile_owner(text: str) -> None:
    result = ground_diet_intent(text, Intent(), [])
    assert not result.owner_modes
    assert result.pending_meal


def test_old_session_defaults_are_compatible_without_new_mode_fields() -> None:
    legacy = SessionState(
        session_id="old", user_id=900001, meal_constraints=Constraints()
    ).model_dump()
    legacy["constraints"].pop("diet_mode")
    legacy["meal_constraints"].pop("diet_mode")
    restored = SessionState.model_validate(legacy)
    assert restored.meal_constraints is not None
    assert restored.constraints.diet_mode == restored.meal_constraints.diet_mode == "omnivore"


@pytest.mark.parametrize("food", ["鹅蛋", "鹌鹑蛋"])
def test_other_eggs_are_lacto_but_not_vegan(food: str) -> None:
    recipe = dish("白菜汤", f"白菜200克；{food}100克")
    assert not diet_reasons(recipe, Constraints(diet_mode="ovo_lacto_vegetarian"))
    assert diet_reasons(recipe, Constraints(diet_mode="vegan"))


class NamedDietLLM(DietLLM):
    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        intent = await super().parse(message, state, profile)
        updates = []
        if "妈妈不参加" in message:
            updates = [DinerUpdate(diner="妈妈", attendance=False)]
        elif "妈妈回来" in message:
            updates = [DinerUpdate(diner="妈妈", attendance=True)]
        if not state.menu_ids and not state.pending_plan:
            intent = intent.model_copy(update={"people": 2})
        return intent.model_copy(update={"diner_updates": updates})


def test_owner_mode_exit_and_return_survive_http_and_restart(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path, llm=NamedDietLLM())) as client:
        first = ask(client, "两人晚餐3道含1汤，妈妈采用纯素，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
        absent = ask(client, "妈妈不参加这餐", sid)
    with TestClient(app_for(tmp_path, llm=NamedDietLLM())) as client:
        returned = ask(client, "妈妈回来参加晚餐", sid)
        retry = ask(client, "继续", sid)
    assert first["status"] == absent["status"] == returned["status"] == retry["status"] == "ok"
    assert first["conversation_state"]["constraints"]["diet_mode"] == "vegan"
    assert absent["conversation_state"]["constraints"]["diet_mode"] == "omnivore"
    assert returned["conversation_state"]["constraints"]["diet_mode"] == "vegan"
    assert_no_meat(returned, vegan=True)
    assert_no_meat(retry, vegan=True)
    before = next(d for d in first["conversation_state"]["diners"] if d["display_name"] == "妈妈")
    after = next(d for d in returned["conversation_state"]["diners"] if d["display_name"] == "妈妈")
    assert before["diner_id"] == after["diner_id"]


def test_other_owner_answer_does_not_clear_mom_uncertainty_http(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path, llm=NamedDietLLM())) as client:
        first = ask(client, "两人晚餐3道含1汤，妈妈吃素，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
        wrong = ask(client, "我采用蛋奶素", sid)
        answered = ask(client, "妈妈采用纯素", sid)
    assert first["status"] == wrong["status"] == "clarification_required"
    assert answered["status"] == "ok"
    assert_no_meat(answered, vegan=True)


def test_canceling_table_diet_keeps_hard_no_spicy_constraint(tmp_path: Path) -> None:
    with TestClient(app_for(tmp_path)) as client:
        first = ask(client, "一人晚餐3道含1汤，全餐纯素，不辣，无其他忌口。")
        updated = ask(client, "取消整餐素食限制", first["conversation_state"]["session_id"])
    assert updated["status"] == "ok"
    assert updated["conversation_state"]["constraints"]["diet_mode"] == "omnivore"
    assert updated["conversation_state"]["constraints"]["no_spicy"] is True


@pytest.mark.parametrize(
    "text", ["但妈妈采用纯素", "另外妈妈采用纯素", "只给妈妈纯素", "仅给妈妈纯素"]
)
def test_owner_connector_does_not_turn_personal_mode_into_table_mode(text: str) -> None:
    grounded = ground_diet_intent(text, Intent(), [])
    assert grounded.owner_modes == {"妈妈": "vegan"}
    assert grounded.meal_mode is None


@pytest.mark.parametrize("text", ["爸爸、妈妈采用纯素", "妈妈与爸爸采用纯素"])
def test_multi_owner_clause_cannot_silently_update_just_first_person(text: str) -> None:
    grounded = ground_diet_intent(text, Intent(), [])
    assert not grounded.owner_modes
    assert grounded.pending_meal
