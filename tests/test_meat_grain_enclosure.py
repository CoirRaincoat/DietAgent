"""Authored meat-exterior contrasts; not source records or a quality score."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.menu_balance import analyze_menu_balance
from app.agent.planner import MenuPlanner
from app.api.main import create_app
from app.api.presentation import build_card
from app.domain.culinary_focus import culinary_food_focus
from app.domain.food_variety import food_families
from app.domain.meat_enclosure import grain_in_meat_enclosure
from app.domain.models import Constraints, Intent, Recipe, SessionState, UserProfile
from app.infrastructure.data import DataCatalog, normalize_recipes
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine


def record(name: str, foods: str, steps: str) -> Recipe:
    """Normalize a public developer fixture without rewriting its source."""
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "晚餐"}]
            ).values()
        )
    )


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        (
            "鸡翅酿饭",
            "鸡翅200克；米饭80克；盐1克",
            "鸡翅去骨。将炒饭装入鸡翅中，用牙签封口。放进烤箱烤熟后装盘。",
        ),
        (
            "鸡腿酿饭",
            "去骨鸡腿200克；米饭80克",
            "将米饭填入鸡腿内，封口后蒸熟装盘。",
        ),
        (
            "鸡胸肉卷饭",
            "鸡胸肉200克；熟米饭80克",
            "鸡胸肉切片摊开，包入米饭后卷起，用牙签固定，蒸熟后装盘。",
        ),
        (
            "鸭腿酿饭",
            "鸭腿200克；米饭80克",
            "米饭塞入鸭腿内，用牙签封口，放烤箱烤熟。",
        ),
        (
            "肉卷饭",
            "猪肉片200克；米饭80克",
            "猪肉片摊开，裹入米饭后卷起，蒸熟后装盘。",
        ),
        (
            "鱿鱼酿饭",
            "鱿鱼200克；米饭80克",
            "将米饭填入鱿鱼筒内，用牙签封口，蒸熟后切片装盘。",
        ),
        (
            "鸡翅酿饭",
            "鸡翅中200克；米饭80克",
            "把米饭填入鸡翅中内，用牙签封口，烤熟后装盘。",
        ),
        (
            "无标题线索饭",
            "米饭80克；鸡翅200克",
            "第1步：将 米饭 装入 鸡翅 中，用牙签封口。第2步：烤熟后装盘。",
        ),
    ],
)
def test_declared_meat_is_the_exterior_not_a_grain_entree(
    name: str, foods: str, steps: str
) -> None:
    sample = record(name, foods, steps)
    assert sample.categories == ["protein"]
    assert sample.raw_ingredients == foods and sample.steps == steps
    assert analyze_menu_balance([sample]).category_counts["staple"] == 0
    assert "主食类" not in build_card(sample).badges
    witness = grain_in_meat_enclosure((item.name for item in sample.ingredients), steps)
    assert witness is not None
    assert witness.text == steps[witness.start : witness.end]
    assert witness.wrapper_food in [item.name for item in sample.ingredients]
    assert witness.filling_food in [item.name for item in sample.ingredients]


@pytest.mark.parametrize(
    "name,foods,steps,role",
    [
        ("鸡翅盖饭", "鸡翅200克；米饭80克", "鸡翅烤熟，放到米饭上装盘。", "staple"),
        ("鸡肉炒饭", "鸡肉200克；米饭80克", "鸡肉与米饭炒熟后装盘。", "staple"),
        ("鸡翅饭", "鸡翅200克；米饭80克", "鸡翅放入米饭碗内，拌匀装盘。", "staple"),
        (
            "鸡翅饭",
            "鸡翅200克；米饭80克",
            "将米饭装入鸡翅碗中，用牙签固定鸡翅后烤熟。",
            "staple",
        ),
        ("鸡翅饭", "鸡翅200克；米饭80克", "米饭装入鸡翅中餐盘，配上烤鸡翅。", "staple"),
        (
            "鸡翅饭",
            "鸡翅200克；米饭80克",
            "不要将米饭装入鸡翅中，用牙签封口。鸡翅另烤，配米饭装盘。",
            "staple",
        ),
        (
            "鸡翅饭",
            "鸡翅200克；米饭80克",
            "如果将米饭装入鸡翅中，可用牙签封口。这里鸡翅另烤，配饭装盘。",
            "staple",
        ),
        (
            "鸡翅饭",
            "鸡翅200克；米饭80克",
            "可以将米饭装入鸡翅中，用牙签封口。这里另将鸡翅烤熟，配饭装盘。",
            "staple",
        ),
        (
            "鸡翅饭",
            "鸡翅200克；米饭80克",
            "米饭蒸熟；另将米饭装入鸡翅中，用牙签封口作为配菜。",
            "staple",
        ),
        (
            "鸡翅饭",
            "鸡翅200克；米饭80克",
            "米饭蒸熟；鸡翅另行包入米饭卷起蒸熟作配菜。",
            "staple",
        ),
        (
            "鸡肉饭团",
            "鸡肉200克；米饭80克",
            "米饭摊开，包入鸡肉后捏成饭团，蒸熟。",
            "staple",
        ),
        (
            "鸡肉酿饭汤",
            "鸡翅200克；米饭80克；水500克",
            "米饭装入鸡翅中封口，入汤煮熟。",
            "soup",
        ),
        (
            "鸡肉夹馅饼",
            "鸡翅200克；米饭80克；面粉150克",
            "米饭装入鸡翅中封口。面粉揉成面团，擀成面皮包入鸡翅馅后烤熟。",
            "staple",
        ),
    ],
)
def test_reverse_geometry_unexecuted_sides_soups_and_outer_dough_keep_roles(
    name: str, foods: str, steps: str, role: str
) -> None:
    assert record(name, foods, steps).categories == [role]


def test_corrected_role_does_not_erase_grain_or_chili_evidence() -> None:
    sample = record(
        "鸡翅酿饭",
        "鸡翅200克；米饭80克；韩式辣椒酱10克",
        "米饭装入鸡翅中，用牙签封口后烤熟。",
    )
    assert sample.categories == ["protein"]
    assert food_families(sample) == frozenset({"chicken", "rice"})
    assert culinary_food_focus(sample).families == frozenset({"chicken"})
    assert "米饭" in analyze_recipe(sample, Constraints()).carbohydrate_sources
    assert not RuleEngine().evaluate(sample, Constraints(no_spicy=True)).allowed
    assert not RuleEngine().evaluate(sample, Constraints(allergies=["鸡肉"])).allowed
    assert MenuPlanner(RuleEngine()).plan([sample], Constraints(no_spicy=True)).failure


def test_outer_meat_does_not_satisfy_both_protein_and_staple_slots() -> None:
    meat = record("鸡翅酿饭", "鸡翅200克；米饭80克", "米饭装入鸡翅中封口后烤熟。")
    vegetables = record("清炒白菜", "白菜200克；盐1克", "白菜炒熟后装盘。")
    rice = record("米饭", "大米80克；水100克", "大米加水煮熟后装盘。")
    planner = MenuPlanner(RuleEngine())
    constraints = Constraints(dish_count=3)
    pool = [meat, vegetables, rice]
    first = planner.plan(pool, constraints)
    assert first.failure is None
    assert analyze_menu_balance(first.recipes).category_counts == {
        "protein": 1,
        "vegetable": 1,
        "staple": 1,
        "soup": 0,
    }
    assert planner.plan(pool, constraints).recipes == first.recipes
    assert planner.plan(pool, constraints, current=first.recipes).recipes == first.recipes


@pytest.mark.parametrize(
    "foods,steps",
    [
        (["鸡翅酱", "米饭"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米醋"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅粉", "米饭"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅菇", "米饭"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米粉"], "米饭装入鸡翅中，用牙签封口。"),
        (["雞翅", "米飯"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡腿", "米饭"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭酱"], "米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅", "小米辣"], "小米装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "虾米装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "花生米装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "玉米装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "鸡头米装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "小米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "米饭装入鸡翅中，不封口。"),
        (["鸡翅", "米饭"], "米饭装入鸡翅中，但无需封口。"),
        (["鸡翅", "米饭"], "可把米饭装入鸡翅中，用牙签封口。"),
        (["鸡翅", "米饭"], "米饭装入鸡翅中。另用牙签封口。"),
        (["鸡翅", "米饭"], "米饭装入鸡翅中\n用牙签封口。"),
        (["鸡翅", "米饭"], "米饭放在鸡翅旁边，用牙签固定。"),
        (["鸡胸肉", "米饭"], "米饭摊开，包入鸡胸肉后卷起。"),
        (["鸡胸肉末", "米饭"], "鸡胸肉摊开，包入米饭后卷起。"),
        ([], "米饭装入鸡翅中，用牙签封口。"),
    ],
)
def test_only_bound_whole_foods_and_positive_closed_geometry_establish_enclosure(
    foods: list[str], steps: str
) -> None:
    assert grain_in_meat_enclosure(iter(foods), steps) is None


@pytest.mark.parametrize(
    "steps",
    [
        "不加盐，米饭装入鸡翅中，用牙签封口。",
        "不要加辣椒，米饭装入鸡翅中，用牙签封口。",
        "如果用别的做法可省略封口。米饭装入鸡翅中，用牙签封口。",
        "米饭另做炒饭。米饭装入鸡翅中，用牙签封口。",
        "将熟米饭装入鸡翅中，用牙签封口。",
        "把炒好的米饭装入鸡翅中，用牙签封口。",
        "取米饭装入鸡翅中，用牙签封口。",
    ],
)
def test_other_clause_negation_or_optional_advice_does_not_erase_executed_geometry(
    steps: str,
) -> None:
    witness = grain_in_meat_enclosure(["鸡翅", "米饭"], steps)
    assert witness is not None
    assert witness.text == steps[witness.start : witness.end]


@pytest.mark.parametrize("grain", ["小米", "燕麦", "米"])
def test_other_finite_declared_grains_are_bound_to_the_same_direction(grain: str) -> None:
    steps = f"{grain}填入鸡腿内，用牙签封口。"
    witness = grain_in_meat_enclosure([grain, "鸡腿"], steps)
    assert witness is not None and witness.filling_food == grain


class ParsedIntents(BaseLLM):
    """Test-only known intents; these tests do not validate an AI's parsing."""

    def __init__(self) -> None:
        self.intents = [
            Intent(
                people=1,
                meal_type="晚餐",
                restrictions_confirmed=True,
                preferred_ingredients=["鸡翅"],
                no_spicy=True,
            )
        ]
        self.parse_calls = 0

    async def parse(self, message: str, state: SessionState, profile: UserProfile) -> Intent:
        self.parse_calls += 1
        return self.intents.pop(0) if self.intents else Intent()

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return list(facts)

    async def aclose(self) -> None:
        pass


def api_catalog() -> DataCatalog:
    """Public fixtures only, not original profiles, conversations or recipes."""
    samples = [
        record("鸡翅酿饭", "鸡翅200克；米饭80克", "米饭装入鸡翅中封口后烤熟。"),
        record("鸡胸肉卷饭", "鸡胸肉200克；米饭80克", "鸡胸肉摊开，包入米饭后卷起蒸熟。"),
        record("辣鸡翅酿饭", "鸡翅200克；米饭80克；辣椒酱5克", "米饭装入鸡翅中封口后烤熟。"),
        record("清炒白菜", "白菜200克；盐1克", "白菜炒熟后装盘。"),
        record("清蒸西兰花", "西兰花200克；水100克", "西兰花蒸熟后装盘。"),
        record("米饭", "大米80克；水100克", "大米加水煮熟后装盘。"),
    ]
    return DataCatalog(
        recipes={item.recipe_id: item for item in samples},
        profiles={
            3: UserProfile(
                data_scope="synthetic",
                user_id=3,
                age=30,
                sex="女",
                height_cm=165,
                weight_kg=55,
                bmi=20.2,
            )
        },
        quality_report={},
    )


def api_client(path: Path, catalog: DataCatalog, llm: ParsedIntents) -> TestClient:
    # Explicit validated fixture values; never load local credentials or .env.
    settings = Settings.model_construct(
        deepseek_api_key=SecretStr(""), session_db=path / "state.db"
    )
    return TestClient(create_app(settings, llm, catalog, SessionStore(settings.database_path)))


def test_http_recipe_role_card_nutrition_replay_local_edit_and_restart(tmp_path: Path) -> None:
    catalog, llm = api_catalog(), ParsedIntents()
    with api_client(tmp_path, catalog, llm) as client:
        first_request = {
            "user_id": 3,
            "message": "一人晚餐，要鸡翅，不辣，没有其它忌口。",
            "request_id": "enclosure-first",
        }
        first = client.post("/chat", json=first_request).json()
        assert first["status"] == "ok"
        session_id = first["conversation_state"]["session_id"]
        meat = next(item for item in first["menu"] if item["name"] == "鸡翅酿饭")
        assert "主食类" not in meat["card"]["badges"]
        assert meat["ingredients"] == ["鸡翅", "米饭"]
        assert "米饭" in meat["nutrition"]["carbohydrate_sources"]
        assert meat["steps"] == catalog.recipes[meat["recipe_id"]].steps
        assert "1 道蛋白质来源菜" in first["reason"]
        assert "1 道主食" in first["reason"]
        repeated = client.post("/chat", json={**first_request, "session_id": session_id}).json()
        assert repeated == first and llm.parse_calls == 1
        llm.intents.append(Intent(action="replace", replace_slot=2))
        replaced = client.post(
            "/chat", json={"user_id": 3, "session_id": session_id, "message": "只换第二道"}
        ).json()
        assert replaced["status"] == "ok"
        before = [item["recipe_id"] for item in first["menu"]]
        after = [item["recipe_id"] for item in replaced["menu"]]
        assert before[0] == after[0] and before[2] == after[2] and before[1] != after[1]
        assert all("辣椒酱" not in item["ingredients"] for item in replaced["menu"])
    restored = SessionStore(tmp_path / "state.db").get(session_id, 3)
    assert restored is not None
    assert restored.menu_ids == after
    assert restored.constraints.no_spicy
    # Plain continuation after changing a preferred dish is a separately
    # preserved failing regression and implementation topic, not a relaxed
    # assertion that any other safe menu is acceptable.


@pytest.mark.parametrize("stream", [False, True])
def test_openai_sse_and_plain_answers_use_the_corrected_role(tmp_path: Path, stream: bool) -> None:
    with api_client(tmp_path, api_catalog(), ParsedIntents()) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "fangtai-meal-agent",
                "user": "3",
                "stream": stream,
                "messages": [{"role": "user", "content": "一人晚餐，要鸡翅，不辣。"}],
            },
        )
    assert response.status_code == 200
    if stream:
        lines = [line for line in response.text.splitlines() if line.startswith("data: ")]
        assert lines[-1] == "data: [DONE]"
        chunks = [json.loads(line.removeprefix("data: ")) for line in lines[:-1]]
        answer = "".join(chunk["choices"][0]["delta"].get("content", "") for chunk in chunks)
    else:
        answer = response.json()["choices"][0]["message"]["content"]
    assert "鸡翅酿饭" in answer
    assert "1 道蛋白质来源菜" in answer and "1 道主食" in answer
    assert "辣鸡翅" not in answer
