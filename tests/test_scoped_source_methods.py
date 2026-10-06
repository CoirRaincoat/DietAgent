"""Public finite food/slot method contracts; source actions, not recipe edits."""

import pytest

from app.agent.planner import MenuPlanner
from app.domain.models import Constraints, Intent, SessionState
from app.infrastructure.sessions import SessionStore
from app.rules.engine import RuleEngine
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_explicit_method_protection import dish
from tests.test_independent_meat_quota import public_catalog


def scope(food, method, slot=None, required=True):
    from app.domain.models import ScopedMethod

    return ScopedMethod(food=food, method=method, slot=slot, required=required)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("豆腐喜欢蒸，鸡胸不要蒸要煎", [("豆腐", "蒸", None, False), ("鸡胸肉", "煎", None, True)]),
        ("南瓜要蒸的", [("南瓜", "蒸", None, True)]),
        ("只把第二道换成蒸南瓜，第一道和第三道汤都保留", [("南瓜", "蒸", 2, True)]),
        ("第3道换成清蒸鱼", [("鱼", "蒸", 3, True)]),
        ("鸡肉偏好烤", [("鸡肉", "烤", None, False)]),
        ("如果南瓜要蒸的", []),
        ("南瓜要蒸的好吗？", []),
        ("解释‘鸡胸不要蒸要煎’", []),
        ("喜欢蒸烤箱", []),
        ("我只是喜欢孜然气味，别把孜然炒鸡当作煎鸡", []),
        ("第一道和第二道都换成蒸南瓜", []),
        ("第二道换成蒸南瓜或者烤南瓜", []),
        ("第二道换成蒸南瓜，还是烤南瓜", []),
    ],
)
def test_finite_raw_scope_does_not_guess_broad_language(message, expected):
    from app.domain.scoped_methods import explicit_scoped_methods

    actual = explicit_scoped_methods(message)
    assert [(r.food, r.method, r.slot, r.required) for r in actual] == expected


@pytest.mark.parametrize(
    "name,foods,steps,matched",
    [
        ("煎鸡胸", "鸡胸肉200克；盐1克", "鸡胸煎熟后装盘。", True),
        ("孜然炒鸡", "鸡胸肉200克；孜然1克", "鸡胸和孜然炒熟后装盘。", False),
        ("蒸豆腐", "豆腐200克", "豆腐蒸熟后装盘。", False),
        ("煎鸡腿", "鸡腿肉200克", "鸡腿煎熟后装盘。", False),
        ("煎鸡胸", "鸡胸肉200克", "放入蒸烤箱，点击开始烹饪。", False),
        ("鸡精煎白菜", "白菜200克；鸡精1克", "白菜煎熟后装盘。", False),
        ("煎鸡胸", "鸡胸肉200克", "鸡胸先煎熟再炒熟装盘。", False),
    ],
)
def test_scope_checks_declared_food_and_finished_source_not_cache(name, foods, steps, matched):
    from app.domain.scoped_methods import scoped_method_mask

    recipe = dish(name, steps, foods=foods).model_copy(update={"methods": ["煎"]})
    assert bool(scoped_method_mask(recipe, [scope("鸡胸肉", "煎")], slot=1)) is matched


@pytest.mark.parametrize(
    "message,expected", [("鲈鱼要蒸", "鲈鱼"), ("鸡腿要烤", "鸡腿肉"), ("嫩豆腐喜欢煮", "嫩豆腐")]
)
def test_specific_foods_do_not_expand_to_a_different_species_cut_or_tofu_type(message, expected):
    from app.domain.scoped_methods import explicit_scoped_methods, scoped_method_mask

    request = explicit_scoped_methods(message)[0]
    assert request.food == expected
    other = {
        "鲈鱼": dish("清蒸鳕鱼", "鳕鱼蒸熟装盘。", foods="鳕鱼200克"),
        "鸡腿肉": dish("烤鸡胸", "鸡胸烤熟装盘。", foods="鸡胸肉200克"),
        "嫩豆腐": dish("煮老豆腐", "豆腐煮熟装盘。", foods="老豆腐200克"),
    }[expected]
    assert scoped_method_mask(other, [request], slot=1) == 0


def test_method_identity_cannot_be_covered_on_another_food_or_slot():
    from app.domain.scoped_methods import scoped_method_coverage

    chicken = dish("煎鸡胸", "鸡胸煎熟装盘。", foods="鸡胸肉200克")
    pumpkin = dish("蒸南瓜", "南瓜蒸熟装盘。", foods="南瓜200克")
    assert scoped_method_coverage([pumpkin, chicken], [scope("南瓜", "蒸", 2)]) == 0
    assert scoped_method_coverage([chicken, pumpkin], [scope("南瓜", "蒸", 2)]) == 1
    assert scoped_method_coverage([pumpkin, chicken], [scope("鸡胸肉", "蒸")]) == 0


def pool():
    return [
        dish("孜然炒鸡", "鸡胸和孜然炒熟装盘。", foods="鸡胸肉200克；孜然1克"),
        dish("煎鸡胸", "鸡胸煎熟装盘。", foods="鸡胸肉200克；盐1克"),
        dish("清蒸豆腐", "豆腐蒸熟装盘。", foods="豆腐200克；水100克"),
        dish("清炒豆腐", "豆腐炒熟装盘。", foods="豆腐200克；盐1克"),
        dish("玉米冬瓜汤", "玉米和冬瓜煮熟后装碗。", foods="玉米100克；冬瓜200克；水500克"),
    ]


def test_initial_scope_priority_is_not_an_additive_flavor_tie():
    result = MenuPlanner(RuleEngine()).plan(
        pool(),
        Constraints(
            dish_count=3,
            soup_count=1,
            no_spicy=True,
            preferred_ingredients=["鸡胸肉", "豆腐"],
            preferences=["孜然"],
            scoped_methods=[scope("豆腐", "蒸", required=False), scope("鸡胸肉", "煎")],
        ),
    )
    assert result.failure is None
    assert {r.name for r in result.recipes} == {"煎鸡胸", "清蒸豆腐", "玉米冬瓜汤"}
    assert any("食材／菜位" in w for w in result.warnings)


def test_authorized_recheck_can_fix_a_scoped_gap_without_inventing_cumin():
    records = pool()
    result = MenuPlanner(RuleEngine()).plan(
        records,
        Constraints(
            dish_count=3,
            soup_count=1,
            preferred_ingredients=["鸡胸肉", "豆腐"],
            preferences=["孜然"],
            scoped_methods=[scope("鸡胸肉", "煎")],
        ),
        current=[records[0], records[2], records[4]],
    )
    assert result.failure is None and result.recipes[0].name == "煎鸡胸"
    assert "孜然" not in result.recipes[0].raw_ingredients
    assert any("食材／菜位" in c["reason"] for c in result.changes)


@pytest.mark.parametrize(
    "restriction,foods",
    [
        ({"no_spicy": True}, "鸡胸肉200克；辣椒2克"),
        ({"allergies": ["花生"]}, "鸡胸肉200克；花生油2克"),
    ],
)
def test_exact_method_never_admits_an_unsafe_witness(restriction, foods):
    safe = dish("炒鸡胸", "鸡胸炒熟装盘。", foods="鸡胸肉200克")
    unsafe = dish("煎鸡胸", "鸡胸煎熟装盘。", foods=foods)
    result = MenuPlanner(RuleEngine()).plan(
        [safe, unsafe],
        Constraints(dish_count=1, scoped_methods=[scope("鸡胸肉", "煎")], **restriction),
        current=[safe],
    )
    assert result.failure and not result.recipes


def test_soft_food_method_unavailable_is_disclosed_without_a_false_guarantee():
    only = dish("炒鸡胸", "鸡胸炒熟装盘。", foods="鸡胸肉200克")
    result = MenuPlanner(RuleEngine()).plan(
        [only],
        Constraints(dish_count=1, scoped_methods=[scope("鸡胸肉", "煎", required=False)]),
    )
    assert result.failure is None and result.recipes == [only]
    assert any("尚缺" in w and "鸡胸肉" in w for w in result.warnings)


def test_no_recheck_does_not_silently_optimize_a_soft_scope():
    records = pool()
    old = [records[0], records[2], records[4]]
    result = MenuPlanner(RuleEngine()).plan(
        records,
        Constraints(
            dish_count=3, soup_count=1, scoped_methods=[scope("鸡胸肉", "煎", required=False)]
        ),
        current=old,
        recheck_soft_preferences=False,
    )
    assert result.recipes == old and not result.changes


def test_new_raw_food_scope_survives_replay_restart_and_empty_continue(tmp_path):
    catalog = public_catalog(pool())
    # The extraction deliberately drops food/slot method requirements.
    llm = ScriptedLLM([complete_intent(people=2, dish_count=3, soup_count=1, no_spicy=True)])
    payload = dict(
        user_id=3,
        request_id="scope-first",
        message="2人晚餐，没有其他忌口，不辣。三道含一汤，豆腐喜欢蒸，鸡胸不要蒸要煎。",
    )
    with client_for(tmp_path, catalog, llm) as client:
        first = client.post("/chat", json=payload).json()
        sid = first["conversation_state"]["session_id"]
        replay = client.post("/chat", json=dict(payload, session_id=sid)).json()
    assert first["status"] == "ok" and first == replay and llm.parse_calls == 1
    assert "煎鸡胸" in [r["name"] for r in first["menu"]]
    assert first["conversation_state"]["constraints"]["scoped_methods"]
    with client_for(tmp_path, catalog, ScriptedLLM([Intent(), Intent(action="explain")])) as client:
        continued = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="继续")
        ).json()
        explained = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="只解释，不换菜")
        ).json()
        other = client.post("/chat", json=dict(user_id=4, session_id=sid, message="继续"))
    assert first["menu"] == continued["menu"] == explained["menu"] and other.status_code == 409
    assert "食材／菜位" in explained["reason"]


def test_local_raw_steamed_pumpkin_preserves_other_slots_and_does_not_set_global_steam(tmp_path):
    fish = dish("清蒸鱼", "鲈鱼蒸熟装盘。", foods="鲈鱼200克")
    tofu = pool()[3]
    soup = pool()[4]
    steam = dish("蒸南瓜", "南瓜蒸熟装盘。", foods="南瓜200克")
    roast = dish("南瓜烤块", "南瓜烤熟装盘。", foods="南瓜200克")
    # An explicitly seeded accepted menu tests slot two, not an unrelated
    # assertion about the planner's default order of tofu versus fish.
    sid = "b" * 32
    constraints = Constraints(dish_count=3, soup_count=1, no_spicy=True)
    SessionStore(tmp_path / "state.db").save(
        SessionState(
            session_id=sid,
            user_id=3,
            constraints=constraints,
            meal_constraints=constraints.model_copy(deep=True),
            menu_ids=[fish.recipe_id, tofu.recipe_id, soup.recipe_id],
            menu_valid=True,
            menu_structure_explicit=True,
            confirmed_fields=["people", "meal_type", "restrictions"],
        ),
        None,
    )
    llm = ScriptedLLM(
        [
            Intent(
                action="replace",
                replace_slot=2,
                preferred_ingredients=["南瓜"],
                query_terms=["蒸南瓜"],
            ),
            Intent(),
        ]
    )
    with client_for(tmp_path, public_catalog([fish, tofu, soup, roast, steam]), llm) as client:
        changed = client.post(
            "/chat",
            json=dict(
                user_id=3,
                session_id=sid,
                message="只把第二道换成蒸南瓜，第一道和第三道汤都保留，还是三道也别加辣。",
            ),
        ).json()
        continued = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="继续")
        ).json()
    assert changed["status"] == "ok" and changed["menu"][1]["name"] == steam.name
    assert changed["menu"][0]["recipe_id"] == fish.recipe_id
    assert changed["menu"][2]["recipe_id"] == soup.recipe_id
    assert changed["menu"] == continued["menu"]
    request = changed["conversation_state"]["constraints"]["scoped_methods"]
    assert request == [dict(food="南瓜", method="蒸", slot=2, required=True)]
    assert not changed["conversation_state"]["constraints"]["excluded_ingredients"]


def test_suggestion_and_soft_swaps_cannot_drop_a_covered_food_scope():
    from app.agent.suggestions import replacement_candidates
    from app.domain.method_preferences import method_swap_preserves

    records = pool()
    menu = [records[1], records[2], records[4]]
    constraint = Constraints(dish_count=3, soup_count=1, scoped_methods=[scope("鸡胸肉", "煎")])
    assert not method_swap_preserves(menu, 0, records[0], [], scoped=constraint.scoped_methods)
    assert not replacement_candidates(menu, [records[0]], "public-scope", constraints=constraint)


def test_conflicting_required_food_methods_are_not_resolved_by_arbitrary_ranking():
    result = MenuPlanner(RuleEngine()).plan(
        pool(),
        Constraints(
            dish_count=3,
            soup_count=1,
            scoped_methods=[scope("鸡胸肉", "煎"), scope("鸡胸肉", "蒸")],
        ),
    )
    assert result.failure and "未满足" in result.failure and not result.recipes


def test_local_method_cannot_be_satisfied_by_changing_a_protected_slot():
    records = pool()
    result = MenuPlanner(RuleEngine()).plan(
        records,
        Constraints(dish_count=3, soup_count=1, scoped_methods=[scope("鸡胸肉", "煎", slot=1)]),
        current=[records[0], records[2], records[4]],
        replace_slot=2,
    )
    assert result.failure and not result.recipes


def test_new_scope_is_not_an_llm_selectable_intent_field():
    from pydantic import ValidationError

    assert Constraints().scoped_methods == []
    with pytest.raises(ValidationError):
        Intent.model_validate({"scoped_methods": [dict(food="南瓜", method="蒸")]})


def test_accumulated_finite_targets_remain_deserializable_when_a_plan_is_unresolved():
    # Like existing accumulated preferences, a persisted constraint must not
    # become unreadable merely because more than 30 finite clauses accumulated.
    requests = [
        scope(food, method)
        for food in ("鸡肉", "牛肉", "南瓜", "豆腐")
        for method in ("蒸", "煮", "炖", "炒", "烤", "煎", "烧", "焖")
    ]
    constraints = Constraints(scoped_methods=requests)
    assert Constraints.model_validate_json(constraints.model_dump_json()) == constraints


def test_food_method_routing_does_not_hide_a_neighboring_flavor_clause():
    from app.domain.matching_tags import flavor_preference_issues, supported_flavor_preferences

    values = ["第二道换成蒸南瓜", "鸡胸不要蒸要煎", "豆腐喜欢蒸，喜欢酸"]
    assert supported_flavor_preferences(values) == ("酸",)
    assert flavor_preference_issues(values) == ()


def test_explain_revalidates_a_persisted_required_scope_and_never_repairs_slots(tmp_path):
    records = pool()
    sid = "d" * 32
    constraints = Constraints(dish_count=3, soup_count=1, scoped_methods=[scope("鸡胸肉", "煎")])
    old_ids = [records[0].recipe_id, records[2].recipe_id, records[4].recipe_id]
    SessionStore(tmp_path / "state.db").save(
        SessionState(
            session_id=sid,
            user_id=3,
            constraints=constraints,
            meal_constraints=constraints.model_copy(deep=True),
            menu_ids=old_ids,
            menu_valid=True,
            menu_structure_explicit=True,
            confirmed_fields=["people", "meal_type", "restrictions"],
        ),
        None,
    )
    with client_for(
        tmp_path, public_catalog(records), ScriptedLLM([Intent(action="explain")])
    ) as client:
        result = client.post(
            "/chat", json=dict(user_id=3, session_id=sid, message="只解释，不换菜")
        ).json()
    assert result["status"] == "clarification_required" and not result["menu"]
    assert result["conversation_state"]["menu_ids"] == old_ids
    assert result["conversation_state"]["menu_valid"] is False
