"""Local tofu generation: actual API output, safety, scope and persistence."""

import pytest
from fastapi.testclient import TestClient

from app.agent.recipe_generation import propose_missing_tofu, resolve_generated_recipe
from app.api.main import create_app
from app.api.presentation import recipe_provenance
from app.domain.models import (
    Constraints,
    Ingredient,
    Intent,
    Recipe,
    ScopedMethod,
    UserProfile,
)
from app.domain.protein_food_references import named_protein_foods
from app.infrastructure.data import DataCatalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.runtime_catalog import load_runtime_catalog
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.rules.engine import RuleEngine


class LocalIntents(BaseLLM):
    def __init__(self, intents):
        self.intents = list(intents)

    async def parse(self, message, state, profile):
        return self.intents.pop(0)

    async def explain(self, facts):
        # Generated provenance must not be optional explanation text.
        return ["opening"]

    async def aclose(self):
        pass


def request_intent(**updates):
    return Intent(
        **{
            "people": 2,
            "meal_type": "晚餐",
            "restrictions_confirmed": True,
            "diet_mode": "vegan",
            "no_spicy": True,
            "preferred_ingredients": ["豆腐"],
            "dish_count": 3,
            "soup_count": 0,
            **updates,
        }
    )


def sample_catalog(include_tofu=False):
    recipes = {}
    for index, (name, foods, role) in enumerate(
        [
            ("蒸西兰花", ["西兰花", "水"], "vegetable"),
            ("腐竹炒木耳", ["腐竹", "木耳", "水"], "protein"),
            ("蒸米饭", ["大米", "水"], "staple"),
            ("蒸南瓜", ["南瓜", "水"], "vegetable"),
            *([("原库清蒸豆腐", ["老豆腐", "水"], "protein")] if include_tofu else []),
        ]
    ):
        recipe = Recipe(
            recipe_id=f"source_{index}",
            name=name,
            raw_ingredients="；".join(foods),
            ingredients=[Ingredient(raw=food, name=food) for food in foods],
            steps="将食材蒸熟后装盘。",
            categories=[role],
            methods=["蒸"],
            meal_types=["晚餐"],
            source_row=index + 2,
            fingerprint=f"source_{index}",
        )
        recipes[recipe.recipe_id] = recipe
    return DataCatalog(
        profiles={
            i: UserProfile(user_id=i, data_scope="synthetic", age=30, sex="男")
            for i in (900001, 900003)
        },
        recipes=recipes,
        quality_report={},
    )


def make_client(tmp_path, catalog, intents):
    settings = Settings(
        _env_file=None, deepseek_api_key="", session_db=tmp_path / "state.db"
    )
    return TestClient(
        create_app(
            settings,
            LocalIntents(intents),
            catalog,
            SessionStore(settings.database_path),
        )
    )


def generated_item(result):
    return next(
        item for item in result["menu"] if item["provenance"]["origin"] == "generated"
    )


def test_actual_api_generates_to_fill_tofu_main_and_keeps_source_catalog(tmp_path):
    catalog = sample_catalog()
    frozen = {key: value.model_dump() for key, value in catalog.recipes.items()}
    with make_client(tmp_path, catalog, [request_intent()]) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐纯素不辣，3菜0汤，要豆腐菜，没有其他忌口",
            },
        ).json()
        assert result["status"] == "ok"
        assert len(result["menu"]) == 3
        item = generated_item(result)
        assert item["name"] == "清蒸豆腐（新生成）"
        assert item["ingredients"] == ["老豆腐", "水"]
        assert item["source"] != "方太菜谱库"
        assert item["provenance"]["source_row"] is None
        assert item["provenance"]["generator_version"] == "local-tofu-main-v1"
        assert item["nutrition"]["source_row"] is None
        assert all(
            entry["source_row"] is None
            for entry in item["nutrition"]["ingredient_contributions"]
        )
        assert all(entry["quantity"] is None for entry in item["ingredient_details"])
        assert item["card"]["servings"] is None
        assert item["card"]["cooking_minutes"] is None
        assert "待试做" in result["reason"] and "不是原2000菜谱" in result["reason"]
        assert "不承诺低钠达标" in result["reason"]
        assert "尚未覆盖有实际食材依据的蛋白菜名称参考：豆腐" not in result["reason"]
        assert any(event["name"] == "recipe_generate" for event in result["tool_calls"])
        assert {
            key: value.model_dump() for key, value in catalog.recipes.items()
        } == frozen
        assert all(
            person["hard_constraints_satisfied"]
            for person in result["diner_suitability"]
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"allergies": ["大豆"]},
        {"allergies": ["未知过敏原"]},
        {"excluded_ingredients": ["豆腐"]},
        {"inventory": ["西兰花", "大米"]},
        {"preferred_ingredients": ["虾"]},
        {"meal_type": "早餐"},
        {"max_minutes": 15},
    ],
)
def test_proposal_cannot_waive_constraints_or_invent_unsupported_food(updates):
    constraints = Constraints(
        preferred_ingredients=["豆腐"], diet_mode="vegan", no_spicy=True
    )
    constraints = Constraints.model_validate({**constraints.model_dump(), **updates})
    assert propose_missing_tofu(constraints, [], [], set(), RuleEngine()) is None


def test_available_source_tofu_and_rejection_never_count_as_missing_library():
    rules = RuleEngine()
    constraints = Constraints(
        preferred_ingredients=["豆腐"], diet_mode="vegan", no_spicy=True
    )
    catalog = sample_catalog(include_tofu=True)
    source = list(catalog.recipes.values())
    assert propose_missing_tofu(constraints, source, [], set(), rules) is None
    assert (
        propose_missing_tofu(constraints, source, [], set(catalog.recipes), rules)
        is None
    )


def test_rejected_generated_id_is_not_reissued_and_identity_is_not_forgeable():
    constraints = Constraints(
        preferred_ingredients=["豆腐"], diet_mode="vegan", no_spicy=True
    )
    proposal = propose_missing_tofu(constraints, [], [], set(), RuleEngine())
    assert proposal is not None and named_protein_foods(proposal) == {"豆腐"}
    assert resolve_generated_recipe(proposal.recipe_id) == proposal
    assert resolve_generated_recipe("generated_arbitrary") is None
    assert (
        propose_missing_tofu(constraints, [], [], {proposal.recipe_id}, RuleEngine())
        is None
    )
    assert recipe_provenance(proposal).origin == "generated"


def test_replay_restart_read_only_continuation_and_cross_user_isolation(tmp_path):
    catalog = sample_catalog()
    message = "两人晚餐纯素不辣，3菜0汤，要豆腐，没有其他忌口"
    with make_client(tmp_path, catalog, [request_intent()]) as client:
        body = {"user_id": 900001, "message": message, "request_id": "first"}
        first = client.post("/chat", json=body).json()
        assert first["status"] == "ok"
        assert client.post("/chat", json=body).json() == first
    session_id = first["conversation_state"]["session_id"]
    with make_client(
        tmp_path,
        catalog,
        [Intent(action="explain"), Intent(), request_intent(preferred_ingredients=[])],
    ) as client:
        for message in ("解释这份菜单，菜单不变", "继续"):
            result = client.post(
                "/chat",
                json={"user_id": 900001, "session_id": session_id, "message": message},
            ).json()
            assert result["status"] == "ok"
            assert result["menu"] == first["menu"]
            assert "待试做" in result["reason"]
            assert not any(
                event["name"] == "recipe_generate" for event in result["tool_calls"]
            )
        forbidden = client.post(
            "/chat",
            json={"user_id": 900003, "session_id": session_id, "message": "继续"},
        )
        assert forbidden.status_code == 409
        other = client.post(
            "/chat",
            json={"user_id": 900003, "message": "两人晚餐纯素不辣，没有其他忌口"},
        ).json()
        assert other["status"] == "ok"
        assert all(item["provenance"]["origin"] == "catalog" for item in other["menu"])


def test_local_replacement_does_not_touch_other_slots(tmp_path):
    catalog = sample_catalog()
    with make_client(
        tmp_path,
        catalog,
        [
            request_intent(preferred_ingredients=[]),
            Intent(action="replace", replace_slot=2, preferred_ingredients=["豆腐"]),
        ],
    ) as client:
        first = client.post(
            "/chat",
            json={"user_id": 900001, "message": "两人晚餐纯素不辣3菜0汤，没有其他忌口"},
        ).json()
        assert first["menu"][1]["name"] == "腐竹炒木耳"
        second = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": first["conversation_state"]["session_id"],
                "message": "只换第二道，要豆腐",
            },
        ).json()
        assert second["status"] == "ok"
        assert second["menu"][0] == first["menu"][0]
        assert second["menu"][2] == first["menu"][2]
        assert second["menu"][1]["provenance"]["origin"] == "generated"


def test_explicit_added_tofu_request_repairs_existing_menu_without_other_slot_changes(
    tmp_path,
):
    catalog = sample_catalog()
    with make_client(
        tmp_path,
        catalog,
        [
            request_intent(preferred_ingredients=[]),
            Intent(preferred_ingredients=["豆腐"]),
            Intent(),
        ],
    ) as client:
        first = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐纯素不辣，三菜零汤，没有其他忌口",
            },
        ).json()
        session_id = first["conversation_state"]["session_id"]
        second = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": session_id,
                "message": "再加一个要求：希望有一道豆腐主体菜，其他要求不变",
            },
        ).json()
        assert second["status"] == "ok"
        assert second["menu"][0] == first["menu"][0]
        assert second["menu"][2] == first["menu"][2]
        assert second["menu"][1]["provenance"]["origin"] == "generated"
        before = first["conversation_state"]["constraints"]
        after = second["conversation_state"]["constraints"]
        assert {
            key: value
            for key, value in before.items()
            if key != "preferred_ingredients"
        } == {
            key: value for key, value in after.items() if key != "preferred_ingredients"
        }
        assert after["preferred_ingredients"] == ["豆腐"]
        third = client.post(
            "/chat",
            json={"user_id": 900001, "session_id": session_id, "message": "继续"},
        ).json()
        assert third["menu"] == second["menu"]
        assert not any(
            event["name"] == "recipe_generate" for event in third["tool_calls"]
        )


def test_source_h02_default_api_now_has_tofu_body(tmp_path):
    catalog = load_runtime_catalog()
    rules = RuleEngine()
    constraints = Constraints(
        people=2,
        meal_type="晚餐",
        dish_count=3,
        soup_count=0,
        diet_mode="vegan",
        no_spicy=True,
        health_goals=["降压"],
        preferred_ingredients=["豆腐"],
    )
    before_count = len(catalog.recipes)
    with make_client(
        tmp_path, catalog, [request_intent(health_goals=["降压"])]
    ) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900003,
                "message": "两人晚餐，3道菜不含汤。整餐纯素，不吃辣，想兼顾降压，希望有豆腐菜，其他没有忌口。",
            },
        ).json()
        assert result["status"] == "ok"
        assert [item["name"] for item in result["menu"]] == [
            "蒸秋季时蔬",
            "清蒸豆腐（新生成）",
            "红薯米饭",
        ]
        state = client.app.state.agent.store.get(
            result["conversation_state"]["session_id"], 900003
        )
        records = client.app.state.agent._recipes(state)
        chosen = [records[item["recipe_id"]] for item in result["menu"]]
        assert all(rules.evaluate(recipe, constraints).allowed for recipe in chosen)
        assert sum("soup" in recipe.categories for recipe in chosen) == 0
        assert any("豆腐" in named_protein_foods(recipe) for recipe in chosen)
        assert len(catalog.recipes) == before_count == 2000
        assert generated_item(result)["provenance"]["source_row"] is None


def test_explicit_tofu_frying_not_silently_replaced_by_steaming(tmp_path):
    intent = request_intent()
    with make_client(tmp_path, sample_catalog(), [intent]) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐纯素不辣三菜零汤，要炒豆腐，没有其他忌口",
            },
        ).json()
        assert result["status"] == "no_feasible_menu"
        assert not result["menu"]
        assert result["conversation_state"]["constraints"]["scoped_methods"]
    # Required scoped methods are also enforced for retained-menu generation.
    proposal = propose_missing_tofu(
        Constraints(preferred_ingredients=["豆腐"]), [], [], set(), RuleEngine()
    )
    assert proposal is not None
    from app.agent.planner import MenuPlanner

    result = MenuPlanner(RuleEngine()).plan(
        [proposal],
        Constraints(
            dish_count=1,
            preferred_ingredients=["豆腐"],
            scoped_methods=[ScopedMethod(food="豆腐", method="炒")],
        ),
    )
    assert result.failure


@pytest.mark.parametrize(
    "message,expected",
    [
        ("要炒豆腐", "炒"),
        ("想吃清蒸豆腐", "蒸"),
        ("不要炒豆腐", None),
        ("是否要炒豆腐？", None),
        ("例如要炒豆腐", None),
        ("他说‘要炒豆腐’", None),
    ],
)
def test_method_first_request_does_not_gain_authority_from_negation_or_mentions(
    message, expected
):
    from app.domain.scoped_methods import explicit_scoped_methods

    scopes = explicit_scoped_methods(message)
    assert [scope.method for scope in scopes] == ([expected] if expected else [])


def test_available_original_tofu_prevents_generation_on_default_api(tmp_path):
    catalog = sample_catalog(include_tofu=True)
    with make_client(tmp_path, catalog, [request_intent()]) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐纯素不辣三菜零汤，要豆腐，没有其他忌口",
            },
        ).json()
        assert result["status"] == "ok"
        assert any(item["name"] == "原库清蒸豆腐" for item in result["menu"])
        assert all(item["provenance"]["origin"] == "catalog" for item in result["menu"])
        assert not any(
            event["name"] == "recipe_generate" for event in result["tool_calls"]
        )


@pytest.mark.parametrize("slot", [1, 3])
def test_local_other_role_slot_does_not_gain_generation_authority(tmp_path, slot):
    catalog = sample_catalog()
    with make_client(
        tmp_path,
        catalog,
        [
            request_intent(preferred_ingredients=[]),
            Intent(action="replace", replace_slot=slot, preferred_ingredients=["豆腐"]),
        ],
    ) as client:
        first = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐纯素不辣三菜零汤，没有其他忌口",
            },
        ).json()
        second = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": first["conversation_state"]["session_id"],
                "message": f"只换第{slot}道，要豆腐",
            },
        ).json()
        assert not any(
            event["name"] == "recipe_generate" for event in second["tool_calls"]
        )
        if second["status"] == "ok":
            assert all(
                item["provenance"]["origin"] == "catalog" for item in second["menu"]
            )
            assert all(
                old == new
                for index, (old, new) in enumerate(zip(first["menu"], second["menu"]))
                if index != slot - 1
            )
