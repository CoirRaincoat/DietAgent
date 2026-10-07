"""Finite source-bound culinary scene suggestions, separate from source labels.

The assistant read these ingredients and completion processes. These are not
independent review, storage/transport guarantees, portion or health claims.
No original recipe field or cooking instruction is changed.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from app.domain.meal_references import recipe_record_sha256
from app.domain.models import Recipe

SCENE_REFERENCE_VERSION = "assistant-source-bound-scene-reference-v3-sliced-fish"
_BOTH = ("家庭聚餐", "便当")
_FAMILY = ("家庭聚餐",)


@dataclass(frozen=True)
class SceneReference:
    source_row: int
    name: str
    record_sha256: str
    scenes: tuple[str, ...]
    ingredient_excerpt: str
    preparation_excerpt: str
    rationale: str
    origin: str = "assistant_review_reference"
    setup_reference: Literal["unreviewed", "main_pot_program", "mould_and_piping"] = "unreviewed"


# Whole-record binding includes source, parsed food, eligibility and role.
# Quoted excerpts are literal; no packaging, reheating or health steps invented.
SCENE_REFERENCES = MappingProxyType(
    {
        "recipe_f6c6540e479893edd39147b4": SceneReference(
            37,
            "西兰花饭团",
            "847b778b6cff62ec0e734306713ba38501b6c615a2e2618a535748f09892de49",
            _BOTH,
            "熟米饭300g",
            "搓成大小适中的饭团即可",
            "熟米饭与沥干西兰花成团，可作分装主食或家庭共享主食的候选参考；保留盐与油，不判断份量或携带保存条件。",
        ),
        "recipe_0005dd605c72d9c0d4a7d729": SceneReference(
            42,
            "白灼芥蓝",
            "b2611aa01a77f89bf2dee3593bef0ee0309c16ce7c1228a2b914467dfec0201e",
            _FAMILY,
            "芥蓝300克",
            "结束后取出装盘",
            "装盘蔬菜配料汁，可作家庭共享配菜参考；不据清淡标签判断低钠，不将带料汁装盘直接标成便当。",
        ),
        "recipe_d95ba3f1be8692b2f0fcaaba": SceneReference(
            132,
            "香菇酿肉丸",
            "b1c61b4b51286fb1652182e8ddff695d4dc7783c659c1dfe408c30b5aabd2c57",
            _BOTH,
            "肉末200克",
            "蒸制完成，淋上鲍鱼汁",
            "肉馅成圆状放入香菇且完成蒸制，可作分装肉菜或家庭共享配菜参考；保留蛋清与全部酱汁，不保证品牌配方或保存安全。",
        ),
        "recipe_38c25b22c5f3542f35b11dc6": SceneReference(
            150,
            "嫩滑蒸鱼片",
            "471171bc071069f61bb628d8f3abe9aedbfdc3a4a9f6c90330cc713de4ec841a",
            ("便当",),
            "龙利鱼400克",
            "蒸好取出，淋上生抽，撒上葱花",
            "原鱼肉切薄片、腌制后在蒸箱完成蒸制，可作分装鱼菜参考，而非整鱼；蛋清、生抽、香油、白胡椒和20分钟腌制均保留，原设备程序温时未明，不保证单人份量、冷藏携带或再加热安全，也不认证低钠或护心功效。",
        ),
        "recipe_59128e0ebe50c137dcf172d3": SceneReference(
            299,
            "蒸米饭",
            "60b1d919b0a1cab3d409a514b30af3fb597072ca5bf7126c82443d182c239df2",
            _BOTH,
            "大米200克",
            "取出蒸米饭，撒上黑芝麻装饰即可",
            "原文已有成品米饭，可作分装主食或家庭共享主食参考；保留黑芝麻，不补做法或推断烹饪时间、人数份量与保存条件。",
        ),
        "recipe_2de6045fbb05f089a78affb9": SceneReference(
            314,
            "花胶鸡汤",
            "e924404f3aa74ec3233237a1c8f42e00bde7104cc5ec6cc97c5dfc011b8a8599",
            _FAMILY,
            "母鸡半只",
            "放入发好的花胶，煮开即可",
            "鸡块与汤完成煮制，可作家庭共享汤菜参考；仍占汤位，不作便当固体配菜或营养充足证明，保留干贝、猪肉与盐。",
        ),
        "recipe_5174d98f34b5933ecb5839e4": SceneReference(
            356,
            "清炒芥蓝",
            "cc41a9806d3e9e692f413d699d76c5638f73be571eb1ab71105e464afb40b86d",
            _BOTH,
            "芥蓝300g",
            "烹饪完成，取出食物即可食用",
            "原文先沥干再完成煸炒的蔬菜，可作分装配菜或家庭共享配菜参考；保留蚝油和盐，不由固体形态保证隔夜、携带或低钠。",
        ),
        "recipe_98ae65f56ccc4e0b8f5149bc": SceneReference(
            377,
            "香菇芹菜牛肉丸",
            "1ac51328804988473c3d7fbdd24d21046772e0d5598a09e77b72d5d95aadef5a",
            _BOTH,
            "牛肉200克",
            "蒸10分钟至熟",
            "牛肉馅成丸并蒸熟，可作分装肉菜或家庭共享配菜参考；原辣味标签、鸡粉与蛋黄保留，不辣和过敏门禁独立执行。",
        ),
        "recipe_33886709348367271c1f164f": SceneReference(
            469,
            "鸡肉玉米肠",
            "4e09d24c73bd5e4d5079ab532bde8b3327ecd5eb06d99c68c6aeb69e6adfcc7b",
            _BOTH,
            "鸡胸肉200g",
            "烹饪完成，冷却脱模后即可食用",
            "鸡肉糊成模蒸制并脱模，可作分装肉菜或家庭共享配菜参考；不证明充分营养、保存安全或人数份量。",
            setup_reference="mould_and_piping",
        ),
        "recipe_3a1ac31b16265e472d012ce2": SceneReference(
            566,
            "红烧栗子鸡",
            "5916f564e887f1ed1de1bc0d17094f33d94633dcc4678504f3d579c211335279",
            _BOTH,
            "鸡腿肉400g",
            "烹饪完成，取出食物趁热食用",
            "原主锅内鸡腿肉切块与板栗完成炖煮／焖煮，可作分装配菜参考；原步骤不另用烤肠模具或裱花袋，仍需原主锅程序，趁热要求保留，不认证通用锅具替代、保存安全、总时间或份量。",
            setup_reference="main_pot_program",
        ),
        "recipe_6c711134de090c024c99b13f": SceneReference(
            502,
            "蒜香豆豉蒸秋葵",
            "cbf4980653e3df9c40eaa8d628104b5ae79298df70a34cf6f0f0ab82e0906a0c",
            _BOTH,
            "秋葵150克",
            "蒸5分钟至熟",
            "切段秋葵完成蒸制，可作分装配菜或家庭共享配菜参考；保留豆豉、豉油与油，不推断低钠、纯素或保存条件。",
        ),
        "recipe_04ff6b95be7d42a2d0c086ae": SceneReference(
            594,
            "时蔬烤黄鱼",
            "92460e00af775e06803be3fec4be17d722ca37616c584fded427b184ab973290",
            _FAMILY,
            "大黄鱼750g",
            "烤制完成即可实用",
            "整鱼、蔬菜与花蛤完成同盘烤制，可作家庭共享鱼菜参考；整鱼不直接作便当参考，保留骨刺、海鲜与全部调味的审核边界。",
        ),
        "recipe_02d6e41b6ec823f7f6380790": SceneReference(
            786,
            "蚕豆炒韭菜",
            "f0d4efc0597c84f7d05af23bebf6c994d172c1ef2fee621c1a750c3dc7bff5d6",
            _BOTH,
            "鲜蚕豆350g",
            "烹饪完成，取出装盘即可食用",
            "豆类和韭菜完成煸炒，可作分装配菜或家庭共享配菜参考；保留豆类、油与盐，不据此承诺携带安全或个体适用性。",
        ),
        "recipe_24534b016699050aa1335afa": SceneReference(
            1817,
            "西兰花炒虾仁",
            "13a1085d7366f7d85ef046cb58ec5779e8f851d7769ca752e3657a410b32428e",
            _BOTH,
            "虾仁100g",
            "烹饪结束，趁热食用",
            "虾仁与小朵西兰花完成翻炒，可作分装或家庭共享配菜参考；源文要求趁热，不保证凉食隔夜条件，虾过敏必须排除。",
        ),
    }
)


def scene_reference(recipe: Recipe) -> SceneReference | None:
    """Only the read, intact source record receives the authored suggestion."""
    reference = SCENE_REFERENCES.get(recipe.recipe_id)
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
