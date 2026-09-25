"""Multi-diner constraints are attributed, aggregated, and reversible by attendance."""

from app.agent.diners import (
    DinerConflict,
    aggregate_constraints,
    apply_diner_updates,
    diner_suitability,
    profile_diner,
)
from app.domain.models import (
    Constraints,
    Diner,
    DinerUpdate,
    Ingredient,
    Recipe,
    UserProfile,
)
from app.rules.engine import RuleEngine


def synthetic_profile() -> UserProfile:
    return UserProfile(
        data_scope="synthetic",
        user_id=900001,
        age=30,
        sex="女",
        height_cm=165,
        weight_kg=55,
        bmi=20.2,
        allergies=["鸡蛋"],
        preferences=["清淡"],
        health_goals=["控糖"],
    )


def recipe(recipe_id: str, ingredients: list[str]) -> Recipe:
    return Recipe(
        recipe_id=recipe_id,
        name=recipe_id,
        raw_ingredients="；".join(ingredients),
        ingredients=[Ingredient(raw=value, name=value) for value in ingredients],
        steps="蒸熟后装盘。",
        categories=["vegetable"],
        methods=["蒸"],
        meal_types=["晚餐"],
        source_row=2,
        fingerprint=recipe_id,
    )


def test_active_diner_constraints_are_attributed_and_aggregated() -> None:
    owner = profile_diner(synthetic_profile())
    diners = apply_diner_updates(
        [owner],
        [
            DinerUpdate(
                diner="爸爸",
                aliases=["我爸"],
                attendance=True,
                allergies=["花生"],
                preferences=["软烂"],
            ),
            DinerUpdate(
                diner="妈妈",
                aliases=["我妈"],
                attendance=True,
                no_spicy=True,
                health_goals=["降压"],
            ),
        ],
        session_id="a" * 32,
    )

    aggregate = aggregate_constraints(Constraints(excluded_ingredients=["香菜"], people=3), diners)

    assert {diner.display_name for diner in diners} == {"用户", "爸爸", "妈妈"}
    assert aggregate.allergies == ["鸡蛋", "花生"]
    assert aggregate.excluded_ingredients == ["香菜"]
    assert aggregate.no_spicy is True
    assert aggregate.preferences == ["清淡", "软烂"]
    assert aggregate.health_goals == ["控糖", "降压"]


def test_absent_diner_is_retained_but_stops_contributing_constraints() -> None:
    dad = Diner(
        diner_id="dad",
        display_name="爸爸",
        aliases=["爸爸", "我爸"],
        attendance=True,
        allergies=["花生"],
    )
    mom = Diner(
        diner_id="mom",
        display_name="妈妈",
        aliases=["妈妈", "我妈"],
        attendance=True,
        no_spicy=True,
    )

    diners = apply_diner_updates(
        [dad, mom],
        [DinerUpdate(diner="我爸", attendance=False)],
        session_id="b" * 32,
    )
    aggregate = aggregate_constraints(Constraints(people=1), diners)

    assert len(diners) == 2
    assert next(diner for diner in diners if diner.diner_id == "dad").attendance is False
    assert aggregate.allergies == []
    assert aggregate.no_spicy is True


def test_existing_alias_updates_one_stable_diner() -> None:
    diners = apply_diner_updates(
        [],
        [DinerUpdate(diner="爸爸", aliases=["我爸"], attendance=True)],
        session_id="c" * 32,
    )
    updated = apply_diner_updates(
        diners,
        [DinerUpdate(diner="我爸", excluded_ingredients=["牛肉"])],
        session_id="c" * 32,
    )

    assert len(updated) == 1
    assert updated[0].diner_id == diners[0].diner_id
    assert updated[0].excluded_ingredients == ["牛肉"]


def test_ambiguous_alias_is_rejected_instead_of_merging_people() -> None:
    diners = [
        Diner(diner_id="one", display_name="大伯", aliases=["长辈"]),
        Diner(diner_id="two", display_name="叔叔", aliases=["长辈"]),
    ]

    try:
        apply_diner_updates(
            diners,
            [DinerUpdate(diner="长辈", no_spicy=True)],
            session_id="d" * 32,
        )
    except DinerConflict as error:
        assert "无法确定" in str(error)
    else:
        raise AssertionError("Ambiguous aliases must not update an arbitrary diner")


def test_per_diner_suitability_reports_known_scope_without_fake_nutrition() -> None:
    diners = [
        Diner(
            diner_id="dad",
            display_name="爸爸",
            aliases=["爸爸"],
            allergies=["花生"],
            preferences=["软烂"],
        ),
        Diner(
            diner_id="mom",
            display_name="妈妈",
            aliases=["妈妈"],
            no_spicy=True,
            preferred_ingredients=["西兰花"],
        ),
    ]
    menu = [recipe("清蒸白菜", ["白菜"])]

    result = diner_suitability(menu, diners, RuleEngine())

    assert [item.diner_id for item in result] == ["dad", "mom"]
    assert all(item.hard_constraints_satisfied for item in result)
    assert result[0].known_constraints == ["过敏：花生", "口味偏好：软烂"]
    assert result[1].unmet_preferences == ["未覆盖偏好食材：西兰花"]
    assert all("已知" in item.scope_note for item in result)


def test_participant_limit_is_enforced_before_session_persistence() -> None:
    diners = [profile_diner(synthetic_profile())]
    updates = [DinerUpdate(diner=f"成员{index}", attendance=True) for index in range(1, 9)]

    try:
        apply_diner_updates(diners, updates, session_id="session")
    except DinerConflict as error:
        assert "最多支持记录 8 位" in str(error)
    else:
        raise AssertionError("The persisted participant list must respect its schema limit")
