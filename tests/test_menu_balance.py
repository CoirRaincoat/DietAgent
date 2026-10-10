"""Observable menu-balance behavior over verified recipe metadata."""

import csv

import pytest

from app.agent.menu_balance import analyze_menu_balance, balance_summary, serving_temperature
from app.domain.models import Ingredient, Recipe
from app.infrastructure.data import PROJECT_ROOT, RECIPE_PATH, normalize_recipes


def recipe(
    recipe_id: str,
    *,
    categories: list[str],
    methods: list[str],
    name: str | None = None,
    steps: str = "制作完成后装盘。",
) -> Recipe:
    return Recipe(
        recipe_id=recipe_id,
        name=name or recipe_id,
        raw_ingredients="食材",
        ingredients=[Ingredient(raw="食材", name="食材")],
        steps=steps,
        source_row=1,
        fingerprint=recipe_id,
        categories=categories,
        methods=methods,
    )


def test_balanced_menu_reports_categories_methods_and_temperature_evidence() -> None:
    menu = [
        recipe("清蒸鱼", categories=["protein"], methods=["蒸"], steps="鱼蒸熟后装盘。"),
        recipe("炒青菜", categories=["vegetable"], methods=["炒"], steps="青菜炒熟后装盘。"),
        recipe(
            "凉拌黄瓜",
            name="凉拌黄瓜",
            categories=["vegetable"],
            methods=["拌"],
            steps="食材焯水后放凉，拌匀装盘。",
        ),
        recipe("米饭", categories=["staple"], methods=["煮"], steps="煮熟后装盘。"),
    ]

    result = analyze_menu_balance(menu)

    assert result.status == "balanced"
    assert result.score >= 80
    assert result.category_counts == {
        "protein": 1,
        "vegetable": 2,
        "staple": 1,
        "soup": 0,
    }
    assert result.method_counts == {"蒸": 1, "炒": 1, "拌": 1, "煮": 1}
    assert result.temperature_counts == {"hot": 3, "cold": 1, "unknown": 0}
    assert any("冷热" in strength for strength in result.strengths)
    summary = balance_summary(result)
    assert "balanced" not in summary
    assert "100" not in summary
    assert "/100" not in summary
    assert "2 道蔬菜类菜" in summary
    assert "蒸、炒、拌、煮" in summary
    assert "未知" not in summary


def test_repeated_protein_dishes_expose_balance_gaps_without_nutrition_claims() -> None:
    menu = [recipe(f"炸肉{index}", categories=["protein"], methods=["炸"]) for index in range(4)]

    result = analyze_menu_balance(menu)

    assert result.status != "balanced"
    assert any("蔬菜" in gap for gap in result.gaps)
    assert any("烹饪方式" in gap for gap in result.gaps)
    assert any("份量" in limitation for limitation in result.limitations)
    assert all("克" not in text and "千卡" not in text for text in result.strengths + result.gaps)


def test_missing_metadata_stays_unknown_instead_of_becoming_hot_or_healthy() -> None:
    menu = [recipe("未知做法", categories=[], methods=[])]

    result = analyze_menu_balance(menu)

    assert result.temperature_counts == {"hot": 0, "cold": 0, "unknown": 1}
    assert result.status == "limited"
    assert any("无法判断" in limitation for limitation in result.limitations)


@pytest.fixture(scope="module")
def source_recipes() -> dict[int, Recipe]:
    # Read actual source recipes through production normalization. No profile
    # data or manually shortened copy of the cooking instructions is involved.
    with (PROJECT_ROOT / RECIPE_PATH).open(encoding="gb18030", newline="") as stream:
        recipes = normalize_recipes(csv.DictReader(stream))
    return {item.source_row: item for item in recipes.values()}


@pytest.mark.parametrize(
    ("source_row", "name", "expected"),
    [
        (26, "梅干菜扣肉", "hot"),
        (187, "葱香土豆泥", "hot"),
        (83, "烤火鸡腿", "hot"),
        # Final unspecified machine execution overrides earlier filling heat.
        (50, "蒸木耳素饺子", "unknown"),
        (371, "凉拌海带丝", "cold"),
        (854, "凉拌秋葵", "cold"),
        (1558, "糟卤冰镇小龙虾", "cold"),
        (197, "无锡排骨", "unknown"),
        (332, "皂角银耳羹", "unknown"),
        (1911, "玉米粒蒸排骨", "unknown"),
    ],
)
def test_source_recipes_use_finished_dish_temperature(
    source_recipes: dict[int, Recipe], source_row: int, name: str, expected: str
) -> None:
    item = source_recipes[source_row]
    assert item.name == name
    assert serving_temperature(item) == expected


def test_preparation_cooling_does_not_claim_cold_food_balance(
    source_recipes: dict[int, Recipe],
) -> None:
    menu = [source_recipes[row] for row in (26, 187, 83, 50)]

    result = analyze_menu_balance(menu)

    assert result.temperature_counts == {"hot": 3, "cold": 0, "unknown": 1}
    assert any("未识别到有明确冷食证据" in gap for gap in result.gaps)
    summary = balance_summary(result)
    assert "冷食 0 道" not in summary
    assert "未知" not in summary


@pytest.mark.parametrize(
    ("name", "steps", "methods", "expected"),
    [
        ("配菜", "冷藏腌制后，入锅蒸熟即可。", ["蒸"], "hot"),
        ("配菜", "煮熟后放凉，装盘食用。", ["煮"], "cold"),
        ("配菜", "煮熟后放凉，再炒热装盘。", ["煮", "炒"], "hot"),
        ("配菜", "冷藏腌制后备用。", ["蒸"], "unknown"),
        ("配菜", "炒匀后放凉，馅料就炒好了。", ["炒"], "unknown"),
        ("配菜", "食材放凉后放在蒸盘上备用。", ["蒸"], "unknown"),
        ("配菜", "制作完成后装盘。", ["煮"], "unknown"),
        ("配菜", "煮熟后冷藏保存。", ["煮"], "unknown"),
        ("配菜", "煮熟后冷藏2小时即可食用。", ["煮"], "cold"),
        ("凉拌配菜", "炒熟装盘，趁热食用。", ["炒"], "hot"),
        ("配菜", "蒸熟后可热食，也可冷食。", ["蒸"], "unknown"),
        ("配菜", "蒸熟后稍微放凉，即可食用。", ["蒸"], "unknown"),
        ("配菜", "蒸熟后趁热享用或放凉后享用皆可。", ["蒸"], "unknown"),
    ],
)
def test_temperature_order_and_incomplete_evidence(
    name: str, steps: str, methods: list[str], expected: str
) -> None:
    item = recipe("case", name=name, steps=steps, methods=methods, categories=[])

    assert serving_temperature(item) == expected
