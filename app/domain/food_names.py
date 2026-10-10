"""Finite plant-name masking for culinary animal-source heuristics only."""

_PLANT_ANIMAL_HOMONYMS = (
    "杏鲍菇",
    "鸡毛菜",
    "猪肚菇",
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
    "大枣肉",
    "枣肉",
    "梨肉",
    "芒果肉",
    "龙眼肉",
    "椰肉",
    "椰子肉",
    "榴莲肉",
    "肉桂",
    "肉豆蔻",
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
        # Preserve word boundaries: 排 + 肉桂 + 骨 is not declared 排骨.
        text = text.replace(plant, " ")
    return text
