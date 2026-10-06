"""User-facing response copy stays concise, contextual and fact-grounded."""

from app.agent.menu_balance import analyze_menu_balance
from app.agent.response_copy import (
    clarification_copy,
    required_fact_ids,
    response_facts,
)
from app.domain.models import (
    ClarificationQuestion,
    Constraints,
    Diner,
    Ingredient,
    Intent,
    Recipe,
)


def recipe(recipe_id: str, name: str, category: str, method: str) -> Recipe:
    """Build a small verified recipe fixture."""
    return Recipe(
        recipe_id=recipe_id,
        name=name,
        raw_ingredients=name,
        ingredients=[Ingredient(raw=name, name=name)],
        steps=f"将{name}{method}熟后装盘。",
        source_row=1,
        fingerprint=recipe_id,
        categories=[category],
        methods=[method],
    )


def menu() -> list[Recipe]:
    """Return a compact menu with three distinct roles."""
    return [
        recipe("protein", "清蒸鱼", "protein", "蒸"),
        recipe("vegetable", "炒青菜", "vegetable", "炒"),
        recipe("staple", "米饭", "staple", "煮"),
    ]


def test_initial_plan_copy_uses_natural_verified_facts() -> None:
    chosen = menu()
    facts = response_facts(
        intent=Intent(action="plan"),
        constraints=Constraints(allergies=["花生"], no_spicy=True),
        diners=[],
        previous=[],
        chosen=chosen,
        balance=analyze_menu_balance(chosen),
    )

    rendered = "\n".join(facts.values())
    assert rendered.startswith("已根据你确认的人数、餐次和饮食要求安排好这餐。")
    assert "避开花生" in rendered
    assert "不辣" in rendered
    assert "1 道蔬菜类菜" in rendered
    assert "recipe_id" not in rendered
    assert "未计算蛋白质" not in rendered
    assert "冷热文字证据" not in rendered


def test_local_replacement_names_only_the_changed_slot() -> None:
    previous = menu()
    chosen = [previous[0], recipe("new", "蒜蓉生菜", "vegetable", "炒"), previous[2]]
    intent = Intent(action="replace", replace_slot=2)
    facts = response_facts(
        intent=intent,
        constraints=Constraints(),
        diners=[],
        previous=previous,
        chosen=chosen,
        balance=analyze_menu_balance(chosen),
    )

    assert facts["opening"] == ("已按你的要求，只将第 2 道“炒青菜”换成“蒜蓉生菜”，其他菜保持不变。")
    assert required_fact_ids(intent, facts) == ["opening", "constraints", "meal_context"]
    assert "餐次适配尚未核验" in facts["meal_context"]


def test_reject_and_explain_copy_do_not_use_mechanical_change_counts() -> None:
    previous = menu()
    chosen = [
        recipe("p2", "番茄炖牛肉", "protein", "炖"),
        recipe("v2", "清炒西兰花", "vegetable", "炒"),
        recipe("s2", "小米饭", "staple", "煮"),
    ]
    common = {
        "constraints": Constraints(),
        "diners": [],
        "previous": previous,
        "chosen": chosen,
        "balance": analyze_menu_balance(chosen),
    }

    rejected = response_facts(intent=Intent(action="reject"), **common)
    explained = response_facts(
        intent=Intent(action="explain"),
        **(common | {"chosen": previous, "balance": analyze_menu_balance(previous)}),
    )

    assert rejected["opening"] == "已按原有要求重新安排整份菜单，上一版菜品没有继续沿用。"
    assert "保留原位置" not in rejected["opening"]
    assert explained["opening"] == "这份菜单的搭配思路是："


def test_multi_diner_copy_attributes_restrictions() -> None:
    chosen = menu()
    facts = response_facts(
        intent=Intent(action="plan"),
        constraints=Constraints(allergies=["花生"], no_spicy=True),
        diners=[
            Diner(diner_id="self", display_name="我", attendance=True),
            Diner(
                diner_id="dad",
                display_name="爸爸",
                attendance=True,
                allergies=["花生"],
                no_spicy=True,
            ),
        ],
        previous=[],
        chosen=chosen,
        balance=analyze_menu_balance(chosen),
    )

    assert facts["diners"] == "多人要求方面，爸爸需要避开花生、不辣，已同时用于整桌筛选。"
    assert "diners" in required_fact_ids(Intent(action="plan"), facts)


def test_clarification_copy_groups_missing_context_once() -> None:
    questions = [
        ClarificationQuestion(field="people", prompt="这餐几个人吃？"),
        ClarificationQuestion(field="meal_type", prompt="安排哪一餐？"),
        ClarificationQuestion(
            field="restrictions",
            prompt="有什么过敏食材或忌口？没有也请说明。",
        ),
    ]

    assert clarification_copy(questions) == (
        "为了把这餐安排准确，还需要确认：这餐几个人吃、安排哪一餐，" "以及有没有过敏食材或忌口（没有也请说明）。"
    )
