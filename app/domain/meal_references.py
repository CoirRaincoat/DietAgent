"""Finite assistant-authored culinary meal references, NOT source labels.

Only the explicitly listed, fully source-bound records are supplemented. The
author read their ingredients and completion process. This is not human/AI
independent review, serving adequacy, a safety gate, or a health-effect label.
No source ingredients, steps, normalized identities or original tags change.
"""

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.models import Recipe

MEAL_REFERENCE_VERSION = "assistant-source-bound-meal-reference-v1"


@dataclass(frozen=True)
class MealReference:
    source_row: int
    name: str
    record_sha256: str
    meals: tuple[str, ...]
    ingredient_excerpt: str
    preparation_excerpt: str
    rationale: str
    origin: str = "assistant_review_reference"


# SHA binds the ENTIRE normalized Recipe, not just a title or a mutable ID.
# Excerpts below are literal source substrings, never rewritten instructions.
MEAL_REFERENCES = MappingProxyType(
    {
        "recipe_fe6dbfd548f0c8c73b77b8c9": MealReference(
            64,
            "蒜蓉油麦菜",
            "9995f99c884036a6cbfdc6f89e5e7f86ac25930d98570a2cee65769a631859b6",
            ("午餐", "晚餐"),
            "油麦菜180克",
            "关门继续烹饪",
            "加热蔬菜配菜可作为午晚餐候选；声明含猪油，不证明纯素、低脂或整餐适配。",
        ),
        "recipe_146ff6f99e4b7a55fb747a0a": MealReference(
            212,
            "黄豆酱蒸鱼",
            "7bdc3c7ec0c4d49a54df48fa075ba901bf86c2a13d93c0cbdaaf6d78d15c542d",
            ("午餐", "晚餐"),
            "鲈鱼1条",
            "蒸好取出",
            "蒸制整鱼配菜可作为午晚餐候选；小米椒仍受不辣硬约束，鱼过敏不得放行。",
        ),
        "recipe_c19eefa39602ccc959feca1b": MealReference(
            365,
            "黑芝麻核桃粥",
            "68944b02c4713065e9ea62b011fe49c610c21fd21897a480a6671c6f7f4468ef",
            ("早餐",),
            "大米30克",
            "普通蒸100℃，60分钟",
            "米类与坚果加水蒸煮的粥可作为早餐主食候选；保留核桃、芝麻和白糖，不证明健康功效。",
        ),
        "recipe_0a8f39a4eecd5ba8fe2df633": MealReference(
            570,
            "豌豆荚馒头",
            "fa36902d6959650ee4a95fe1b18ae25cc746cda12cd6d7d84e62008e22f403c6",
            ("早餐",),
            "面粉300克",
            "100度，蒸10分钟即可",
            "发酵面团成形并蒸制的馒头可作为早餐主食候选；菜名造型不证明含豌豆或低糖。",
        ),
        "recipe_13bb347ce86acd50bd7ac971": MealReference(
            744,
            "清蒸农家鸡",
            "19651bd27a04e699954cd474ff6189f1c3943295d20fb2f518dda6220c8fc74c",
            ("午餐", "晚餐"),
            "土鸡半只",
            "蒸制完成取出",
            "蒸制鸡块配菜可作为午晚餐候选；不从清蒸或标题推断低钠、护心或份量充足。",
        ),
        "recipe_9ac15e691ea5eacad873013a": MealReference(
            865,
            "鳕鱼豆腐汤",
            "d00253ed807f6f0a5eafeefb0ebdb90bf86ecb9f42bd7ae7973854e231b338bd",
            ("午餐", "晚餐"),
            "鳕鱼肉200克",
            "普通蒸100℃，30分钟",
            "鱼与豆腐加水蒸制的汤可作为午晚餐汤位候选；不以汤替代整餐肉菜，不证明低钠。",
        ),
        "recipe_d67a37cfff64bfda4c3335f7": MealReference(
            866,
            "快手清蒸黄花鱼",
            "b23b6696c0d74e7acb8e557b4c1cb881d54612953128ebf982acba23e5d841fb",
            ("午餐", "晚餐"),
            "黄花鱼300克",
            "热油浇淋在鱼身上即可",
            "完成蒸制、浇淋的鱼配菜可作为午晚餐候选；不据快手标题承诺耗时或低脂。",
        ),
        "recipe_0d93a63795e19c99d55ef8d6": MealReference(
            1110,
            "南瓜二米粥",
            "e82e1e33d7b25ec7d5a152c2945d15bdbe53a399ac732348c68687e725b0b7f2",
            ("早餐",),
            "大米30克；小米30克",
            "普通蒸100℃，70分钟",
            "大米、小米、南瓜加水蒸煮的粥可作为早餐主食候选；不证明总营养、人数份量或快速可做。",
        ),
        "recipe_ca6429145e7a7d01c64dbd98": MealReference(
            1167,
            "清炒西兰花",
            "dc4b01bd80b6b076402fc83682520c31ca4b9c29d6bacb54fcd8b40ce1d2412d",
            ("午餐", "晚餐"),
            "西兰花350克",
            "3分钟/猛火/热炒",
            "热炒西兰花配菜可作为午晚餐候选；保留蚝油，不证明纯素、低脂或低钠。",
        ),
        "recipe_90c4bd82b4d2fc9b55e16b87": MealReference(
            1328,
            "鲜虾娃娃菜卷",
            "d85590de51ceddf4781d6e3659feaa62f8db0cb2bd9acb9ba0602a0854fe7666",
            ("午餐", "晚餐"),
            "虾仁100克",
            "娃娃菜卷蒸好以后",
            "有虾肉馅且完成蒸制的菜卷可作为午晚餐配菜候选；不可只凭菜叶名认作素菜。",
        ),
        "recipe_6b6bded3ba728af2ce0fc9ab": MealReference(
            1515,
            "清蒸鸡肉白菜卷",
            "9ebc3cd882476f1097a635f6769b60f2723f74c72ceb9f158750a735f34b3429",
            ("午餐", "晚餐"),
            "鸡胸肉150克",
            "100度，蒸20分钟",
            "鸡肉馅白菜卷完成蒸制，可作为午晚餐配菜候选；保留全部调料，不证明低钠或纯素。",
        ),
        "recipe_7b09e527ce7ac2ded59031f5": MealReference(
            1987,
            "生滚粥",
            "540a4c2e8f56ad67a4ecd4e2905e7343dae7b30b400fb0f5f74b5046e58c3657",
            ("早餐",),
            "大米100克",
            "5分钟/5档",
            "米粥继续煮入虾、蛤蜊的成菜可作为早餐主食候选；海鲜、花生酱等过敏硬约束不变。",
        ),
    }
)


def recipe_record_sha256(recipe: Recipe) -> str:
    payload = json.dumps(
        recipe.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def meal_reference(recipe: Recipe) -> MealReference | None:
    """Changed/misattributed records receive no reference, even with copied IDs."""
    reference = MEAL_REFERENCES.get(recipe.recipe_id)
    if reference is None:
        return None
    if (
        recipe.source_row != reference.source_row
        or recipe.name != reference.name
        or reference.ingredient_excerpt not in recipe.raw_ingredients
        or reference.preparation_excerpt not in recipe.steps
        or recipe_record_sha256(recipe) != reference.record_sha256
    ):
        return None
    return reference
