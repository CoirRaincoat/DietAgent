"""Current defaults: food references, sodium attention and actual menu scope.

These are authored policy/engineering tests, not clinical or human labels.
"""

import json

import pytest

from app.agent.meal_structure import repair_shared_soup_entree
from app.agent.planner import MenuPlanner
from app.agent.response_copy import required_fact_ids
from app.domain.health_evidence import HealthRule, health_evidence
from app.domain.models import Constraints, DinerUpdate, Intent, UserProfile
from app.nutrition.structured import analyze_recipe
from app.rules.engine import RuleEngine
from tests.test_component_slots import dish


@pytest.mark.parametrize("goal", ["降压", "护心"])
@pytest.mark.parametrize(
    "food", ["盐", "生抽", "酱油", "蚝油", "鱼露", "味噌", "鸡汁", "鸡粉", "豆豉"]
)
def test_ordinary_sodium_presence_is_attention_not_a_rank_penalty(goal, food):
    plain = dish("蒸白菜", "白菜200克", "白菜蒸熟装盘。")
    seasoned = dish("蒸白菜", f"白菜200克；{food}适量", "白菜蒸熟装盘。")
    rules = RuleEngine()
    a, b = rules.goal_evidence(plain, goal), rules.goal_evidence(seasoned, goal)
    assert a.score == b.score
    assert b.has_attention and not b.has_rank_caution
    c = Constraints(dish_count=1, health_goals=[goal])
    assert rules.evaluate(seasoned, c).allowed
    match = analyze_recipe(seasoned, c).goal_matches[0]
    assert any("含钠来源待核" in reason for reason in match.reasons)
    assert "不能判定低钠" in match.limitation
    assert MenuPlanner(rules).plan(
        [seasoned, plain], c, current=[seasoned]
    ).recipes == [seasoned]


@pytest.mark.parametrize("where", ["raw", "step"])
def test_unparsed_or_step_only_attention_survives_without_hidden_penalty(where):
    record = dish("蒸白菜", "白菜200克", "白菜蒸熟装盘。")
    rules = RuleEngine()
    prior = rules.goal_evidence(record, "降压")
    if where == "raw":
        record.raw_ingredients += "；生抽适量"
    else:
        record.steps += "装盘后加入生抽。"
    result = rules.goal_evidence(record, "降压")
    assert result.score == prior.score and result.has_attention
    assert "生抽" in (result.raw_attention if where == "raw" else result.step_attention)


def test_only_subject_food_reference_earns_credit_and_auxiliary_facts_remain():
    rules = RuleEngine()
    chicken = dish("鸡肉玉米肠", "鸡胸肉200克；胡萝卜20克；玉米20克", "鸡肉蒸熟装盘。")
    egg_product = dish("虾仁玉子豆腐", "虾仁100克；玉子豆腐100克", "食材蒸熟装盘。")
    tofu = dish(
        "鲈鱼蒸豆腐", "鲈鱼200克；豆腐100克；盐2克；白糖少许", "鲈鱼豆腐蒸熟装盘。"
    )
    a, b, c = [rules.goal_evidence(r, "护心") for r in (chicken, egg_product, tofu)]
    assert not a.category_rank_enabled and a.category_foods == ("胡萝卜",)
    assert b.preferred_rank_terms == ()
    assert c.preferred_rank_terms == ("豆腐",)
    assert c.has_rank_caution  # Sugar still independently disclosed.
    text = "".join(
        analyze_recipe(chicken, Constraints(health_goals=["护心"]))
        .goal_matches[0]
        .reasons
    )
    assert "胡萝卜" in text and "不凭辅料给整道菜加健康分" in text


def test_repeated_food_credit_saturates_but_declared_foods_are_not_deleted():
    record = dish("蒸豆腐", "豆腐200克", "豆腐蒸熟装盘。")
    evidence = RuleEngine().goal_evidence(record, "护心")
    assert evidence.score_with_covered_terms(set()) == evidence.score
    assert evidence.score_with_covered_terms({"豆腐"}) == evidence.score - 2
    assert evidence.preferred_foods == ("豆腐",)
    legacy = health_evidence(record, HealthRule(prefer_terms=("豆腐",)))
    assert legacy.score_with_covered_terms({"豆腐"}) == legacy.score


@pytest.mark.parametrize("protect", ["vegan", "allergy", "spicy", "local"])
def test_health_reference_never_buys_hard_or_local_permission(protect):
    old = dish("蒸豆腐", "豆腐200克", "豆腐蒸熟装盘。")
    candidate = dish("鱼豆腐", "鱼肉200克；豆腐100克；辣椒1克", "食材蒸熟装盘。")
    c = Constraints(dish_count=1, health_goals=["护心", "降压"])
    if protect == "vegan":
        c.diet_mode = "vegan"
    elif protect == "allergy":
        c.allergies = ["鱼"]
    elif protect == "spicy":
        c.no_spicy = True
    else:
        c.no_spicy = True
    result = MenuPlanner(RuleEngine()).plan(
        [old, candidate],
        c,
        current=[old],
        replace_slot=1 if protect == "local" else None,
    )
    assert candidate not in result.recipes
    assert all(RuleEngine().evaluate(r, c).allowed for r in result.recipes)


def test_shared_soft_reference_can_yield_but_explicit_mixed_request_cannot():
    menu = [
        dish("肉末蒸白菜", "白菜200克；肉末20克", "白菜肉末蒸熟装盘。"),
        dish("鸡汁蒸萝卜", "白萝卜200克；鸡汁1克", "萝卜蒸熟装盘。"),
        dish("蒸鱼", "鱼肉200克", "鱼肉蒸熟装盘。"),
        dish("温泉蛋", "鸡蛋2个", "鸡蛋煮熟后享用。"),
        dish("米饭", "大米200克", "大米加水煮熟后食用。"),
        dish("排骨汤", "排骨200克；水500克", "排骨煮汤后连汤盛出。"),
    ]
    chicken = dish("蒸鸡胸肉", "鸡胸肉200克", "鸡肉蒸熟装盘。")
    c = Constraints(people=5, meal_type="晚餐", dish_count=6, soup_count=1)
    common = dict(
        scores={},
        order={},
        food_matches=lambda r, t: bool(RuleEngine().food_matches(r, t)),
    )
    changed = repair_shared_soup_entree(menu, [chicken], c, **common)
    assert changed.recipes[3] == chicken and changed.changed_indices == {3}
    c.preferences = ["荤素搭配"]
    assert repair_shared_soup_entree(menu, [chicken], c, **common).recipes == menu
    c.preferences = ["要温泉蛋"]
    assert repair_shared_soup_entree(menu, [chicken], c, **common).recipes == menu


@pytest.mark.parametrize("model", [Constraints, Intent, DinerUpdate, UserProfile])
def test_explicit_health_goal_aliases_are_canonical_but_unknowns_not_invented(model):
    extra = {"diner": "父亲"} if model is DinerUpdate else {}
    if model is UserProfile:
        extra = {"user_id": 900001, "age": 30, "sex": "男"}
    value = model(
        health_goals=[
            "控压",
            "控制血压",
            "降血压",
            "控制血糖",
            "减重",
            "未知目标",
            "如果控压",
        ],
        **extra,
    )
    assert value.health_goals == [
        "降压",
        "降压",
        "降压",
        "控糖",
        "减脂",
        "未知目标",
        "如果控压",
    ]


def test_food_preference_gap_cannot_be_hidden_by_minimal_explanation():
    facts = {
        "opening": "硬约束已核",
        "constraints": "不辣",
        "balance": "3菜",
        "food_preferences": "当前菜单未覆盖食材偏好：豆腐",
    }
    assert "food_preferences" in required_fact_ids(Intent(action="plan"), facts)
    assert "food_preferences" in required_fact_ids(
        Intent(action="replace", replace_slot=2), facts
    )


@pytest.mark.parametrize("stream", [False, True])
def test_unavailable_vegan_tofu_is_disclosed_on_native_compat_and_sse(tmp_path, stream):
    from app.infrastructure.data import DataCatalog
    from tests.test_agent_api import (
        ScriptedLLM,
        client_for,
        complete_intent,
        openai_payload,
        parse_sse,
    )

    class OpeningOnlyLLM(ScriptedLLM):
        async def explain(self, facts):
            return ["opening"]

    records = [
        dish("蒸白菜", "白菜200克", "白菜蒸熟装盘。"),
        dish("腐竹炒木耳", "腐竹100克；木耳50克", "腐竹木耳炒熟装盘。"),
        dish("米饭", "大米200克；水300克", "大米加水煮熟。"),
    ]
    for r in records:
        r.meal_types = ["晚餐"]
    profile = UserProfile(user_id=3, data_scope="synthetic", age=30, sex="女")
    catalog = DataCatalog(
        profiles={3: profile},
        recipes={r.recipe_id: r for r in records},
        quality_report={},
    )
    intent = complete_intent(
        diet_mode="vegan",
        no_spicy=True,
        dish_count=3,
        soup_count=0,
        health_goals=["降压"],
        preferred_ingredients=["豆腐"],
        # A genuinely unavailable generation case: strict inventory does not
        # contain tofu. The new fallback may not invent available ingredients.
        inventory=["白菜", "腐竹", "木耳", "大米", "水"],
    )
    with client_for(tmp_path, catalog, OpeningOnlyLLM([intent])) as client:
        request = {
            "user_id": 3,
            "message": "一人晚餐纯素不辣，三菜零汤，要豆腐；只使用白菜、腐竹、木耳、大米和水",
            "request_id": "gap",
        }
        native = client.post("/chat", json=request).json()
        compat = client.post(
            "/v1/chat/completions",
            json=openai_payload(
                stream=stream,
                session_id=native["conversation_state"]["session_id"],
                request_id="gap",
                messages=[{"role": "user", "content": request["message"]}],
            ),
        )
    assert native["status"] == "ok" and len(native["menu"]) == 3
    assert native["conversation_state"]["constraints"]["diet_mode"] == "vegan"
    assert "当前菜单未覆盖食材偏好：豆腐" in native["reason"]
    assert "未擅自放宽素食、过敏、不辣或数量要求" in native["reason"]
    assert all(item["provenance"]["origin"] == "catalog" for item in native["menu"])
    assert compat.status_code == 200
    if stream:
        content = "".join(
            c["choices"][0]["delta"].get("content", "") for c in parse_sse(compat.text)
        )
    else:
        content = compat.json()["choices"][0]["message"]["content"]
    body, marker, payload = content.rpartition("\n\n【菜谱JSON】\n```json\n")
    assert marker and content.count(marker) == 1 and payload.endswith("\n```")
    assert body.endswith(native["reason"])
    assert json.loads(payload[:-4]) == [
        {"recipe_id": item["recipe_id"], "name": item["name"]} for item in native["menu"]
    ]
