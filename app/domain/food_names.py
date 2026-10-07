"""Finite plant-name masking for culinary animal-source heuristics only."""

_PLANT_ANIMAL_HOMONYMS = (
    "鸡腿菇",
    "鸡枞",
    "鸡头米",
    "鱼腥草",
    "蟹味菇",
    "鸭梨",
    "牛肝菌",
    "马蹄",
    "贝贝南瓜",
    "果肉",
    "荔枝肉",
    "桂圆肉",
    "龙眼肉",
    "椰肉",
    "榴莲肉",
    "肉桂",
    "肉蔻",
    "豆蔻",
)


def without_plant_animal_homonyms(text: str) -> str:
    """Mask known plant words before an animal-name substring scan.

    This does not normalize allergens, prove vegetarian status, or remove any
    source ingredient from the stored recipe. Mixed fruit/meat strings retain
    the actual meat word, rather than discarding the complete ingredient.
    """
    for plant in _PLANT_ANIMAL_HOMONYMS:
        text = text.replace(plant, "")
    return text
