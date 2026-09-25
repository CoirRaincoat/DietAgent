"""Observable menu-balance behavior over verified recipe metadata."""

from app.agent.menu_balance import analyze_menu_balance, balance_summary
from app.domain.models import Ingredient, Recipe


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
        recipe("清蒸鱼", categories=["protein"], methods=["蒸"]),
        recipe("炒青菜", categories=["vegetable"], methods=["炒"]),
        recipe(
            "凉拌黄瓜",
            name="凉拌黄瓜",
            categories=["vegetable"],
            methods=["拌"],
            steps="食材焯水后放凉，拌匀装盘。",
        ),
        recipe("米饭", categories=["staple"], methods=["煮"]),
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
    assert "蔬菜类菜 2 道" in summary
    assert "蒸、炒、拌、煮" in summary


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
