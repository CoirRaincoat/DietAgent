"""Variable proposals: explicit opt-in, local screening, ownership and replay."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_recipe_generation import LocalIntents, request_intent, sample_catalog

from app.api.main import create_app
from app.domain.generated_recipe import (
    RecipeDraft,
    normalize_proposal,
    verified_proposal,
)
from app.domain.models import Intent
from app.infrastructure.llm.deepseek import DeepSeekLLM
from app.infrastructure.settings import Settings


def braised_draft():
    return RecipeDraft(
        name="番茄焖豆腐",
        ingredients=["老豆腐", "番茄", "食用油", "水"],
        steps=[
            "将老豆腐切块，番茄洗净切块。",
            "锅中加入食用油，将番茄炒软后加入水和老豆腐。",
            "小火焖煮至豆腐热透，收汁后装盘。",
        ],
    )


class VariableProvider(LocalIntents):
    def __init__(self, intents, draft=None):
        super().__init__(intents)
        self.draft = draft if draft is not None else braised_draft()
        self.requests = []

    async def propose_recipe(self, user_messages):
        self.requests.append(user_messages)
        return self.draft


def client_for(tmp_path, provider, *, enabled=True, catalog=None):
    return TestClient(
        create_app(
            Settings(
                _env_file=None,
                deepseek_api_key="",
                allow_recipe_generation=enabled,
                session_db=tmp_path / "state.db",
            ),
            llm=provider,
            catalog=sample_catalog() if catalog is None else catalog,
        )
    )


MESSAGE = "两人晚餐纯素不辣，3菜0汤，要豆腐主体菜，没有其他忌口。"


def test_variable_proposal_changes_actual_menu_and_survives_restart(tmp_path):
    provider = VariableProvider([request_intent()])
    with client_for(tmp_path, provider) as client:
        body = {"user_id": 900001, "message": MESSAGE, "request_id": "proposal-first"}
        first = client.post("/chat", json=body).json()
        assert first["status"] == "ok"
        generated = next(
            item
            for item in first["menu"]
            if item["provenance"]["origin"] == "generated"
        )
        assert generated["name"] == "番茄焖豆腐（新生成）"
        assert (
            generated["provenance"]["generator_version"] == "provider-tofu-proposal-v1"
        )
        assert generated["provenance"]["source_row"] is None
        assert generated["nutrition"]["source_row"] is None
        assert all(item["quantity"] is None for item in generated["ingredient_details"])
        assert "已按已知本地限制筛查" in first["reason"]
        assert "仅老豆腐和水" not in first["reason"]
        assert "不承诺低钠达标" in first["reason"]
        assert "generated_recipes" not in first["conversation_state"]
        assert client.post("/chat", json=body).json() == first
        assert len(provider.requests) == 1
        assert provider.requests[0] == [MESSAGE]
        session = first["conversation_state"]["session_id"]
        state = client.app.state.agent.store.get(session, 900001)
        assert state is not None and len(state.generated_recipes) == 1
        assert len(client.app.state.agent.catalog.recipes) == 4
    second_provider = VariableProvider([Intent(action="explain"), Intent()])
    with client_for(tmp_path, second_provider) as client:
        for message in ("解释当前菜单，不换菜", "继续"):
            result = client.post(
                "/chat",
                json={"user_id": 900001, "session_id": session, "message": message},
            ).json()
            assert result["menu"] == first["menu"]
            assert "待试做" in result["reason"]
        assert second_provider.requests == []
        assert (
            client.post(
                "/chat",
                json={"user_id": 900003, "session_id": session, "message": "继续"},
            ).status_code
            == 409
        )


def test_key_or_profile_does_not_enable_paid_proposal_calls(tmp_path):
    provider = VariableProvider([request_intent()])
    with client_for(tmp_path, provider, enabled=False) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": MESSAGE}
        ).json()
        assert any(item["name"] == "清蒸豆腐（新生成）" for item in first["menu"])
        assert provider.requests == []
    assert (
        Settings(
            _env_file=None, deepseek_api_key="fake-parser-key"
        ).allow_recipe_generation
        is False
    )


def test_available_source_or_wrong_local_slot_never_calls_generator(tmp_path):
    provider = VariableProvider([request_intent()])
    with client_for(
        tmp_path, provider, catalog=sample_catalog(include_tofu=True)
    ) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": MESSAGE}
        ).json()
        assert any(item["name"] == "原库清蒸豆腐" for item in first["menu"])
        assert provider.requests == []


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "低钠护心豆腐"},
        {"ingredients": ["豆腐", "鸡精"]},
        {"ingredients": ["豆腐", "辣椒"]},
        {"ingredients": ["豆腐", "食用油", "食用油"]},
        {"steps": ["将豆腐切块。", "蒸十五分钟后装盘。"]},
        {"steps": ["将豆腐切块。", "加入盐三克后焖至豆腐热透。"]},
        {"steps": ["将豆腐切块。", "锅中炒豆腐后加入番茄，治疗高血压。"]},
    ],
)
def test_draft_never_supplies_health_facts_numbers_or_unknown_condiments(changes):
    with pytest.raises(ValueError):
        RecipeDraft.model_validate({**braised_draft().model_dump(), **changes})


def test_step_additions_and_modified_record_cannot_forge_verified_identity():
    draft = RecipeDraft(
        name="番茄焖豆腐",
        ingredients=["豆腐", "水"],
        steps=["将豆腐切块。", "锅中加水和番茄，焖豆腐至熟透后装盘。"],
    )
    with pytest.raises(ValueError):
        normalize_proposal(draft)
    recipe = normalize_proposal(braised_draft())
    assert verified_proposal(recipe)
    assert not verified_proposal(
        recipe.model_copy(update={"steps": "添加辣椒后炒熟。"})
    )
    assert not verified_proposal(
        recipe.model_copy(update={"recipe_id": "generated_forged"})
    )
    assert not verified_proposal(recipe.model_copy(update={"source_row": 5}))


def test_proposal_payload_is_user_text_only_with_original_profile_and_source_state():
    observed = []

    def respond(request):
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {"draft": braised_draft().model_dump()},
                                ensure_ascii=False,
                            )
                        },
                    }
                ]
            },
        )

    async def run():
        provider = DeepSeekLLM(
            "fake-key", client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
        )
        try:
            result = await provider.propose_recipe([MESSAGE])
            assert result == braised_draft()
        finally:
            await provider._client.aclose()

    import asyncio

    asyncio.run(run())
    assert len(observed) == 1
    payload = json.loads(observed[0]["messages"][1]["content"])
    assert set(payload) == {"user_messages", "public_foods"}
    assert payload["user_messages"] == [MESSAGE]
    assert (
        "profile" not in payload
        and "constraints" not in payload
        and "recipes" not in payload
    )


def test_native_request_cannot_supply_generated_records(tmp_path):
    provider = VariableProvider([])
    with client_for(tmp_path, provider) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": MESSAGE,
                "generated_recipes": {"fake": {}},
            },
        )
        assert result.status_code == 422
        assert provider.requests == []


@pytest.mark.parametrize("method", ["炒", "煎", "焖"])
def test_failed_initial_source_plan_can_retry_without_dropping_method(tmp_path, method):
    draft = RecipeDraft(
        name=f"香菇{method}豆腐",
        ingredients=["老豆腐", "香菇", "食用油", "水"],
        steps=[
            "老豆腐切块，香菇洗净切片。",
            f"锅中加入食用油，将香菇与豆腐{method}至熟透后装盘。",
        ],
    )
    message = f"两人晚餐纯素不辣，3菜0汤，要豆腐主体菜，豆腐要{method}，没有其他忌口。"
    baseline = VariableProvider([request_intent()])
    with client_for(tmp_path / "before", baseline, enabled=False) as client:
        before = client.post(
            "/chat", json={"user_id": 900001, "message": message}
        ).json()
        assert before["status"] == ("ok" if method == "煎" else "no_feasible_menu")
    provider = VariableProvider([request_intent()], draft)
    with client_for(tmp_path / "after", provider) as client:
        after = client.post(
            "/chat", json={"user_id": 900001, "message": message}
        ).json()
        assert after["status"] == "ok"
        assert len(after["menu"]) == 3
        assert any(item["name"] == draft.name + "（新生成）" for item in after["menu"])
        assert len(provider.requests) == 1
        state = client.app.state.agent.store.get(
            after["conversation_state"]["session_id"], 900001
        )
        assert state.constraints.scoped_methods[0].method == method


@pytest.mark.parametrize(
    "updates", [{"allergies": ["大豆"]}, {"inventory": ["西兰花", "大米", "水"]}]
)
def test_hard_exclusion_never_triggers_generation(tmp_path, updates):
    provider = VariableProvider([request_intent(**updates)])
    with client_for(tmp_path, provider) as client:
        client.post("/chat", json={"user_id": 900001, "message": MESSAGE})
        assert provider.requests == []


def test_local_nonprotein_target_never_triggers_generation(tmp_path):
    provider = VariableProvider(
        [request_intent(), Intent(action="replace", replace_slot=1)]
    )
    with client_for(tmp_path, provider) as client:
        first = client.post(
            "/chat", json={"user_id": 900001, "message": MESSAGE}
        ).json()
        record = client.app.state.agent.catalog.recipes[first["menu"][0]["recipe_id"]]
        assert record.categories == ["vegetable"]
        client.post(
            "/chat",
            json={
                "user_id": 900001,
                "session_id": first["conversation_state"]["session_id"],
                "message": "只换第1道菜",
            },
        )
        assert len(provider.requests) == 1


@pytest.mark.parametrize("envelope", ["wrapped", "direct", "declined", "extra"])
def test_provider_proposal_envelope_is_strict(envelope):
    import asyncio

    value = {"draft": braised_draft().model_dump()}
    if envelope == "direct":
        value = braised_draft().model_dump()
    elif envelope == "declined":
        value = {"draft": None}
    elif envelope == "extra":
        value["nutrition"] = 100

    async def run():
        from app.infrastructure.llm.base import LLMOutputError

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": json.dumps(value)},
                            }
                        ]
                    },
                )
            )
        ) as client:
            provider = DeepSeekLLM("fake-key", client=client)
            if envelope == "extra":
                with pytest.raises(LLMOutputError):
                    await provider.propose_recipe([MESSAGE])
            else:
                result = await provider.propose_recipe([MESSAGE])
                assert result == (None if envelope == "declined" else braised_draft())

    asyncio.run(run())


def test_pan_fried_then_braised_draft_is_rejected_and_local_fried_proposal_is_used(
    tmp_path,
):
    draft = RecipeDraft(
        name="香煎豆腐",
        ingredients=["老豆腐", "食用油", "水"],
        steps=[
            "将老豆腐切片。",
            "锅中放食用油，将豆腐煎至两面金黄。",
            "加入水，将豆腐焖至入味后装盘。",
        ],
    )
    provider = VariableProvider([request_intent()], draft)
    with client_for(tmp_path, provider) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐，纯素不辣，3菜0汤，豆腐要煎，没有其他忌口。",
            },
        ).json()
        assert result["status"] == "ok"
        assert any(row["name"] == "香煎豆腐（新生成）" for row in result["menu"])
        assert not any(
            row["provenance"]["generator_version"] == "provider-tofu-proposal-v1"
            for row in result["menu"]
        )
        assert "不是原2000菜谱或模型原提案" in result["reason"]
        assert len(provider.requests) == 1


def test_moisture_is_not_water_addition_but_later_water_addition_is_checked():
    draft = RecipeDraft(
        name="香煎豆腐",
        ingredients=["老豆腐", "食用油"],
        steps=[
            "将老豆腐切片，用厨房纸吸干表面水分。",
            "锅中加食用油，将豆腐煎至两面金黄且热透后装盘。",
        ],
    )
    assert normalize_proposal(draft).methods == ["煎"]
    with pytest.raises(ValueError):
        normalize_proposal(
            draft.model_copy(
                update={"steps": [*draft.steps, "加入水，将豆腐焖至入味。"]}
            )
        )


def test_default_local_pan_is_persistent_and_never_calls_provider(tmp_path):
    provider = VariableProvider([request_intent(excluded_ingredients=["蒜"])])
    message = "两人晚餐，3菜0汤，纯素不辣，豆腐要煎，不放蒜，没有其他忌口。"
    body = {"user_id": 900001, "message": message, "request_id": "fried"}
    with client_for(tmp_path, provider, enabled=False) as client:
        first = client.post("/chat", json=body).json()
        assert first["status"] == "ok"
        generated = next(
            item
            for item in first["menu"]
            if item["provenance"]["origin"] == "generated"
        )
        assert generated["provenance"]["generator_version"] == "local-pan-fried-tofu-v1"
        assert generated["provenance"]["source_row"] is None
        assert generated["ingredients"] == ["老豆腐", "食用油"]
        assert "不承诺少油" in first["reason"]
        assert client.post("/chat", json=body).json() == first
        assert provider.requests == []
    second = VariableProvider([Intent(action="explain"), Intent()])
    with client_for(tmp_path, second, enabled=False) as client:
        for text in ("解释当前菜单，不换菜", "继续"):
            response = client.post(
                "/chat",
                json={
                    "user_id": 900001,
                    "session_id": first["conversation_state"]["session_id"],
                    "message": text,
                },
            ).json()
            assert response["menu"] == first["menu"]
        assert second.requests == []


@pytest.mark.parametrize("source_method", ["蒸", "煎"])
def test_source_priority_includes_user_requested_tofu_method(tmp_path, source_method):
    catalog = sample_catalog(include_tofu=True)
    source = next(r for r in catalog.recipes.values() if r.name == "原库清蒸豆腐")
    if source_method == "煎":
        catalog.recipes[source.recipe_id] = source.model_copy(
            update={
                "name": "原库香煎豆腐",
                "steps": "将老豆腐煎至中心熟透后装盘。",
                "methods": ["煎"],
            }
        )
    provider = VariableProvider([request_intent()])
    with client_for(tmp_path, provider, catalog=catalog, enabled=False) as client:
        result = client.post(
            "/chat",
            json={
                "user_id": 900001,
                "message": "两人晚餐，3菜0汤，纯素不辣，豆腐要煎，没有其他忌口。",
            },
        ).json()
        assert result["status"] == "ok"
        assert any(
            row["name"]
            == ("原库香煎豆腐" if source_method == "煎" else "香煎豆腐（新生成）")
            for row in result["menu"]
        )
        assert provider.requests == []


def test_local_pan_does_not_bypass_oil_inventory_or_rejection():
    from app.agent.recipe_generation import propose_missing_tofu
    from app.domain.models import Constraints, ScopedMethod
    from app.rules.engine import RuleEngine

    constraints = Constraints(
        meal_type="晚餐",
        preferred_ingredients=["豆腐"],
        scoped_methods=[ScopedMethod(food="豆腐", method="煎")],
        inventory=["老豆腐", "水"],
    )
    assert propose_missing_tofu(constraints, [], [], set(), RuleEngine()) is None
    constraints.inventory = None
    proposal = propose_missing_tofu(constraints, [], [], set(), RuleEngine())
    assert proposal is not None
    assert (
        propose_missing_tofu(constraints, [], [], {proposal.recipe_id}, RuleEngine())
        is None
    )
