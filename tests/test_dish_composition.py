"""Authored explicit meat/no-meat requests; no private users or model calls."""

import json
from itertools import combinations, product
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.dish_composition import explicit_dish_composition, plan_composition
from app.agent.planner import MenuPlanner
from app.agent.suggestions import replacement_candidates
from app.api.main import create_app
from app.domain.dish_composition import dish_kind
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


def fixture_dish(name: str, foods: str, steps: str = "煮熟后装盘。") -> Recipe:
    """Only handwritten source facts, independent of production role labels."""
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "午餐、晚餐"}]
            ).values()
        )
    )


def recipe_pool() -> list[Recipe]:
    return [
        fixture_dish("蒸鸡肉", "鸡肉200克；盐1克"),
        fixture_dish("蒸牛肉", "牛肉200克；盐1克"),
        fixture_dish("蒸猪肉", "猪肉200克；盐1克"),
        fixture_dish("肉末白菜", "白菜200克；猪肉末20克；盐1克"),
        fixture_dish("清炒白菜", "白菜200克；食用油3克；盐1克", "清炒后装盘。"),
        fixture_dish("清炒青菜", "青菜200克；食用油3克；盐1克", "清炒后装盘。"),
        fixture_dish("蒸豆腐", "豆腐200克；盐1克"),
        fixture_dish("炒鸡蛋", "鸡蛋100克；食用油3克；盐1克", "炒熟装盘。"),
        fixture_dish("鸡精白菜", "白菜200克；鸡精1克"),
        fixture_dish("米饭", "大米200克；水200克"),
        fixture_dish("冬瓜汤", "冬瓜200克；水500克；盐1克"),
        fixture_dish("排骨汤", "排骨200克；水500克；盐1克"),
    ]


class CompositionLLM(BaseLLM):
    """Deliberately omits new quantities; explicit text must be independently read."""

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        if "只换" in message:
            return Intent(action="replace", replace_slot=2)
        if "解释" in message:
            return Intent(action="explain")
        if state.menu_ids:
            return Intent(action="plan")
        return Intent(
            action="plan",
            people=5,
            meal_type="晚餐",
            restrictions_confirmed=True,
            dish_count=5,
            soup_count=1,
            no_spicy=True,
        )

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return ["opening", "constraints"]

    async def aclose(self) -> None:
        pass


def make_app(
    tmp_path: Path, recipes: list[Recipe] | None = None, llm: BaseLLM | None = None
) -> Any:
    chosen = recipe_pool() if recipes is None else recipes
    profile = UserProfile(
        user_id=900001,
        data_scope="synthetic",
        age=30,
        sex="未指定",
        height_cm=170,
        weight_kg=65,
        bmi=22.49,
    )
    settings = Settings.model_construct(session_db=tmp_path / "state.db")
    return create_app(
        settings,
        llm or CompositionLLM(),
        DataCatalog({profile.user_id: profile}, {r.recipe_id: r for r in chosen}, {}),
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


@pytest.mark.parametrize(
    "message,meat,vegetarian",
    [
        ("5人晚餐共5道菜，其中1道汤，2荤2素，不辣，无其他忌口。", 2, 2),
        ("5人晚餐共6道菜，其中1道汤，三荤两素，不辣，无其他忌口。", 3, 2),
        ("5人晚餐共7道菜，其中2道汤，2道荤菜和3道素菜，不辣，无其他忌口。", 2, 3),
        ("5人晚餐共4道菜，其中1道汤，0荤3素，不辣，无其他忌口。", 0, 3),
    ],
)
def test_explicit_counts_are_executed_not_just_stored(
    tmp_path: Path, message: str, meat: int, vegetarian: int
) -> None:
    with TestClient(make_app(tmp_path)) as client:
        result = ask(client, message)
    assert result["status"] == "ok"
    state = result["conversation_state"]["constraints"]
    assert state["meat_dish_count"] == meat
    assert state["vegetarian_dish_count"] == vegetarian
    # Test expectations use authored raw facts, not production category verdicts.
    non_soup = [item for item in result["menu"] if not item["name"].endswith("汤")]
    animal = {"鸡肉", "牛肉", "猪肉", "猪肉末"}
    meat_found = sum(bool(set(item["ingredients"]) & animal) for item in non_soup)
    assert meat_found == meat
    assert len(non_soup) - meat_found == vegetarian
    assert all("鸡精" not in item["ingredients"] for item in non_soup)
    assert "荤菜" in result["reason"] and "素菜" in result["reason"]
    assert "蛋奶" in result["reason"]


@pytest.mark.parametrize(
    "message",
    [
        "5人晚餐共5道菜，其中1道汤，3荤3素，无其他忌口。",
        "5人晚餐共5道菜，其中1道汤，2到3荤2素，无其他忌口。",
        "5人晚餐共5道菜，其中1道汤，2荤还是3荤，无其他忌口？",
    ],
)
def test_conflicting_or_ambiguous_counts_require_clarification(
    tmp_path: Path, message: str
) -> None:
    with TestClient(make_app(tmp_path)) as client:
        result = ask(client, message)
    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert "荤" in result["reason"] or "素" in result["reason"]


def test_new_composition_changes_old_menu_and_retry_survives_restart(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        first = ask(client, "5人晚餐共5道菜，其中1道汤，无其他忌口，不辣。")
        sid = first["conversation_state"]["session_id"]
        updated = ask(client, "这餐改成3荤1素，其他要求不变。", sid)
    with TestClient(make_app(tmp_path)) as client:
        retry = ask(client, "按刚才的要求继续", sid)
    assert updated["status"] == retry["status"] == "ok"
    assert updated["conversation_state"]["constraints"]["meat_dish_count"] == 3
    assert [r["recipe_id"] for r in updated["menu"]] == [r["recipe_id"] for r in retry["menu"]]
    assert first["menu"] != updated["menu"]


def test_not_enough_safe_meat_never_relaxes_counts(tmp_path: Path) -> None:
    candidates = [
        r for r in recipe_pool() if r.name not in {"蒸鸡肉", "蒸牛肉", "蒸猪肉", "肉末白菜"}
    ]
    with TestClient(make_app(tmp_path, candidates)) as client:
        result = ask(client, "5人晚餐共5道菜，其中1道汤，2荤2素，不辣，无其他忌口。")
    assert result["status"] == "no_feasible_menu"
    assert result["menu"] == []
    assert "荤" in result["reason"]


def test_only_replace_second_preserves_other_slots_and_composition(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        first = ask(client, "5人晚餐共5道菜，其中1道汤，2荤2素，不辣，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
        result = ask(client, "只换第二道，其他不变。", sid)
    assert result["status"] == "ok"
    assert result["menu"][1]["recipe_id"] != first["menu"][1]["recipe_id"]
    assert [r["recipe_id"] for i, r in enumerate(result["menu"]) if i != 1] == [
        r["recipe_id"] for i, r in enumerate(first["menu"]) if i != 1
    ]
    assert result["conversation_state"]["constraints"]["meat_dish_count"] == 2


@pytest.mark.parametrize(
    "foods,steps,expected",
    [
        ("白菜200克；猪肉末20克", "煮熟装盘。", "meat"),
        ("白菜200克；蚝油2克", "炒熟装盘。", "meat"),
        ("白菜200克", "加入猪油炒熟装盘。", "meat"),
        ("白菜200克；鸡精1克", "炒熟装盘。", "other"),
        ("白菜200克", "加入高汤煮熟装盘。", "other"),
        ("白菜200克；神秘白菜水10克", "煮熟装盘。", "other"),
        ("白菜200克；鱼香酱2克", "炒熟装盘。", "other"),
        ("白菜200克；鸡蛋100克", "炒熟装盘。", "vegetarian"),
        ("白菜200克；牛奶100克", "煮熟装盘。", "vegetarian"),
        ("白菜200克；素排骨100克", "煮熟装盘。", "other"),
        ("白菜200克；羊肚菌20克", "煮熟装盘。", "vegetarian"),
        ("白菜200克；马蹄20克", "煮熟装盘。", "vegetarian"),
        ("贝贝南瓜200克；蔬菜粒50克", "贝贝南瓜蒸熟装盘。", "vegetarian"),
        ("贝贝南瓜200克；扇贝50克", "蒸熟装盘。", "meat"),
    ],
)
def test_meat_evidence_does_not_certify_unknown_or_plant_names(
    foods: str, steps: str, expected: str
) -> None:
    recipe = fixture_dish("家常白菜", foods, steps)
    assert dish_kind(recipe) == expected


@pytest.mark.parametrize(
    "message,expected",
    [
        ("两荤三素", {"meat_dish_count": 2, "vegetarian_dish_count": 3}),
        ("2道荤菜3道素菜，不要辣", {"meat_dish_count": 2, "vegetarian_dish_count": 3}),
        ("0荤4素", {"meat_dish_count": 0, "vegetarian_dish_count": 4}),
        ("只换第二道，保留2荤2素", {"meat_dish_count": 2, "vegetarian_dish_count": 2}),
        ("荤素搭配", {}),
        ("解释三荤两素", {}),
        ("上次三荤两素", {}),
        ("第一荤菜换掉", {}),
        ("解释“三荤两素”是什么意思", {}),
    ],
)
def test_finite_composition_parser_keeps_counts_distinct_from_ordinals(
    message: str, expected: dict[str, int]
) -> None:
    counts, issue = explicit_dish_composition(message)
    assert counts == expected
    assert issue is None


@pytest.mark.parametrize(
    "message", ["2-3荤2素", "不要3荤，要2荤", "9荤", "十一素", "一二荤", "2荤3荤"]
)
def test_finite_composition_parser_clarifies_unsupported_counts(message: str) -> None:
    counts, issue = explicit_dish_composition(message)
    assert counts == {}
    assert issue is not None


def test_whole_meal_vegan_mode_survives_retry_and_explicit_lacto_change(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        first = ask(client, "5人晚餐共4道菜，其中1道汤，0荤3素，要纯素，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
    with TestClient(make_app(tmp_path)) as client:
        retry = ask(client, "继续，按刚才的要求", sid)
        clarified = ask(client, "不是纯素，素菜允许蛋奶，继续。", sid)
    assert first["status"] == retry["status"] == "ok"
    assert [d["recipe_id"] for d in first["menu"]] == [d["recipe_id"] for d in retry["menu"]]
    assert retry["conversation_state"]["constraints"]["diet_mode"] == "vegan"
    assert "纯素" in retry["reason"]
    assert clarified["status"] == "ok"


@pytest.mark.parametrize(
    "total,soups,meat,vegetarian",
    [
        (total, soups, meat, vegetarian)
        for total, soups, pair in product(
            range(1, 9),
            range(3),
            [(0, 0), (0, 2), (1, 2), (2, 0), (2, 2), (None, 1), (1, None), (None, None)],
        )
        for meat, vegetarian in [pair]
    ],
)
def test_quantity_feasibility_matches_independent_exhaustive_small_pool(
    total: int,
    soups: int,
    meat: int | None,
    vegetarian: int | None,
) -> None:
    all_recipes = recipe_pool()
    chosen = [all_recipes[i] for i in (0, 1, 2, 4, 5, 6, 9, 10, 11)]
    groups = ["meat"] * 3 + ["vegetarian"] * 3 + ["other", "soup", "soup"]
    expected = any(
        sum(groups[i] == "soup" for i in selected) == soups
        and (meat is None or sum(groups[i] == "meat" for i in selected) == meat)
        and (vegetarian is None or sum(groups[i] == "vegetarian" for i in selected) == vegetarian)
        for selected in combinations(range(len(chosen)), total)
    )
    constraints = Constraints(
        dish_count=total, soup_count=soups, meat_dish_count=meat, vegetarian_dish_count=vegetarian
    )
    result = plan_composition(chosen, constraints, [], choose=lambda options, selected: options[0])
    assert (result.failure is None) is expected
    if expected:
        found = [groups[chosen.index(r)] for r in result.recipes]
        assert len(result.recipes) == len(set(r.recipe_id for r in result.recipes)) == total
        assert found.count("soup") == soups
        assert meat is None or found.count("meat") == meat
        assert vegetarian is None or found.count("vegetarian") == vegetarian
    else:
        assert result.recipes == []


def test_added_quantity_conflict_with_only_one_slot_edit_requests_permission(
    tmp_path: Path,
) -> None:
    class LocalLLM(CompositionLLM):
        async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
            if state.menu_ids:
                return Intent(action="replace", replace_slot=2)
            return await super().parse(message, state, profile)

    with TestClient(make_app(tmp_path, llm=LocalLLM())) as client:
        first = ask(client, "5人晚餐5道含1汤，2荤2素，无其他忌口。")
        result = ask(
            client, "只换第二道，改成4荤0素，其他不变。", first["conversation_state"]["session_id"]
        )
    assert result["status"] == "no_feasible_menu"
    assert result["menu"] == []
    assert "整餐" in result["reason"]


def test_ambiguous_composition_stays_pending_until_explicit_answer(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        first = ask(client, "5人晚餐5道含1汤，2到3荤2素，无其他忌口。")
        sid = first["conversation_state"]["session_id"]
    with TestClient(make_app(tmp_path)) as client:
        retry = ask(client, "继续", sid)
        answer = ask(client, "明确2荤2素，无其他忌口。", sid)
    assert first["status"] == retry["status"] == "clarification_required"
    assert retry["menu"] == []
    assert answer["status"] == "ok"


def test_ungrounded_model_quota_is_not_applied(tmp_path: Path) -> None:
    class WrongCountLLM(CompositionLLM):
        async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
            intent = await super().parse(message, state, profile)
            return intent.model_copy(update={"meat_dish_count": 2, "vegetarian_dish_count": 2})

    with TestClient(make_app(tmp_path, llm=WrongCountLLM())) as client:
        result = ask(client, "5人晚餐5道含1汤，注意荤素搭配，无其他忌口。")
    assert result["status"] == "clarification_required"
    assert result["menu"] == []
    assert result["conversation_state"]["constraints"]["meat_dish_count"] is None


def test_food_preference_and_suggestions_never_break_explicit_composition() -> None:
    candidates = recipe_pool()
    constraints = Constraints(
        dish_count=5,
        soup_count=1,
        meat_dish_count=2,
        vegetarian_dish_count=2,
        preferred_ingredients=["白菜"],
        no_spicy=True,
    )
    planned = MenuPlanner(RuleEngine()).plan(candidates, constraints)
    assert planned.failure is None
    assert any("白菜" in r.raw_ingredients for r in planned.recipes)
    for option in replacement_candidates(
        planned.recipes, candidates, "authored-example", constraints=constraints
    ):
        menu = [option, *planned.recipes[1:]]
        animal = {"鸡肉", "牛肉", "猪肉", "猪肉末"}
        nonsoup = [r for r in menu if not r.name.endswith("汤")]
        assert sum(bool({i.name for i in r.ingredients} & animal) for r in nonsoup) == 2
        assert all("鸡精" not in r.raw_ingredients for r in nonsoup)


def test_no_safe_replacement_does_not_change_unrelated_composition_slots() -> None:
    candidates = recipe_pool()
    current = [candidates[i] for i in (0, 1, 4, 5, 10)]
    pool = [candidates[i] for i in (0, 1, 4, 5, 6, 10, 11)]
    constraints = Constraints(
        dish_count=5, soup_count=1, meat_dish_count=2, vegetarian_dish_count=2
    )
    result = MenuPlanner(RuleEngine()).plan(pool, constraints, current=current, replace_slot=1)
    assert result.failure is not None
    assert result.recipes == []


def test_explicit_quantities_never_relax_known_food_exclusions() -> None:
    constraints = Constraints(
        dish_count=5,
        soup_count=1,
        meat_dish_count=2,
        vegetarian_dish_count=2,
        excluded_ingredients=["鸡肉", "牛肉", "猪肉"],
    )
    result = MenuPlanner(RuleEngine()).plan(recipe_pool(), constraints)
    assert result.failure is not None
    assert result.recipes == []


def test_streamed_menu_and_mandatory_explanation_show_verified_composition(tmp_path: Path) -> None:
    with TestClient(make_app(tmp_path)) as client:
        result = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "900001",
                "stream": True,
                "messages": [
                    {
                        "role": "user",
                        "content": "5人晚餐共5道菜，其中1道汤，2荤2素，无其他忌口，不辣。",
                    }
                ],
            },
        )
    assert result.status_code == 200
    events = [
        line.removeprefix("data: ")
        for line in result.text.splitlines()
        if line.startswith("data: ")
    ]
    assert events[-1] == "[DONE]"
    text = "".join(
        json.loads(line)["choices"][0]["delta"].get("content", "") for line in events[:-1]
    )
    assert "荤菜 2 道、素菜 2 道" in text
    assert "蛋奶" in text
    assert "鸡精白菜" not in text
