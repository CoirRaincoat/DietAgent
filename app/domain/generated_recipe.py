"""Untrusted, variable tofu proposals, independently normalized and verified.

This stage accepts basic plant/tofu combinations, not unrestricted recipes,
measurements or asserted health facts. The source KB never enters this module.
"""

import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import Recipe

PROPOSAL_VERSION = "provider-tofu-proposal-v1"
PROPOSAL_FLAG = "generated_provider_tofu_v1"
PUBLIC_PROPOSAL_FOODS = (
    "豆腐",
    "老豆腐",
    "嫩豆腐",
    "番茄",
    "香菇",
    "木耳",
    "小葱",
    "蒜",
    "西兰花",
    "胡萝卜",
    "菠菜",
    "食用油",
    "盐",
    "酱油",
    "水",
)
_UNSUPPORTED_TEXT = re.compile(
    r"\d|[０-９]|克|毫升|小勺|大勺|汤匙|茶匙|分钟|小时|℃|摄氏|人份|份量|"
    r"低钠|降压|护心|控糖|治愈|治疗|营养|蛋白质|热量|大卡|"
    r"设备|程序|档位|模式|API|https?://|"
    r"肉|虾|鱼|鸡|蛋|奶|蜂蜜|黄油|蚝油|高汤|浓汤|汤料|"
    r"辣椒|辣酱|剁椒|小米辣|料包|调味料|酱料|复合"
)


class RecipeDraft(BaseModel):
    """Proposal, never a model-supplied Recipe, provenance or safety decision."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=35)
    ingredients: list[str] = Field(min_length=2, max_length=12)
    steps: list[str] = Field(min_length=2, max_length=6)

    @field_validator("name")
    @classmethod
    def tofu_name(cls, value: str) -> str:
        value = value.strip()
        if "豆腐" not in value or _UNSUPPORTED_TEXT.search(value) or "新生成" in value:
            raise ValueError("Unsupported proposal name")
        return value

    @field_validator("ingredients")
    @classmethod
    def declared_foods(cls, values: list[str]) -> list[str]:
        values = [value.strip() for value in values]
        if len(set(values)) != len(values) or any(
            value not in PUBLIC_PROPOSAL_FOODS for value in values
        ):
            raise ValueError("Only declared basic public foods are supported")
        if not any(value in {"豆腐", "老豆腐", "嫩豆腐"} for value in values):
            raise ValueError("Tofu must be declared")
        return values

    @field_validator("steps")
    @classmethod
    def modest_steps(cls, values: list[str]) -> list[str]:
        values = [value.strip() for value in values]
        if any(
            not 4 <= len(value) <= 160 or _UNSUPPORTED_TEXT.search(value)
            for value in values
        ):
            raise ValueError("No measurements, medical claims or unsupported additions")
        return values


class RecipeProposalResponse(BaseModel):
    """Required envelope; the provider may explicitly decline a proposal."""

    model_config = ConfigDict(extra="forbid")
    draft: RecipeDraft | None


def normalize_proposal(draft: RecipeDraft) -> Recipe:
    """Derive role/methods locally, rather than accepting model-assigned tags."""
    from app.infrastructure.data import normalize_recipes

    text = "\n".join(draft.steps)
    # Check supported step additions too; aliases of the declared tofu are OK.
    for food in PUBLIC_PROPOSAL_FOODS:
        # Moisture already in tofu is not an added water ingredient. Mask only
        # this precise noun, not the full instruction or any later water addition.
        food_text = re.sub(r"水分", "", text) if food == "水" else text
        if food in food_text and not any(
            food in declared or declared in food for declared in draft.ingredients
        ):
            raise ValueError("Undeclared step ingredient")
    source = {
        "名称": draft.name + "（新生成）",
        "食材清单": "；".join(draft.ingredients),
        "烹饪步骤": text,
        "label": "午餐、晚餐",
    }
    recipe = next(iter(normalize_recipes([source]).values()))
    if not recipe.eligible or recipe.categories != ["protein"] or not recipe.methods:
        raise ValueError("Proposal is not a supported cooked protein dish")
    payload = {"version": PROPOSAL_VERSION, "draft": draft.model_dump(mode="json")}
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return recipe.model_copy(
        update={
            "recipe_id": "generated_" + fingerprint[:24],
            "fingerprint": fingerprint,
            "source_row": 0,
            "quality_flags": [
                *recipe.quality_flags,
                PROPOSAL_FLAG,
                "proposal_not_kitchen_validated",
            ],
        }
    )


def verified_proposal(recipe: Recipe) -> bool:
    """Restore only exact normalized versioned records, never arbitrary IDs."""
    if PROPOSAL_FLAG not in recipe.quality_flags or recipe.source_row != 0:
        return False
    try:
        draft = RecipeDraft(
            name=recipe.name.removesuffix("（新生成）"),
            ingredients=[item.name for item in recipe.ingredients],
            steps=recipe.steps.splitlines(),
        )
        return normalize_proposal(draft) == recipe
    except ValueError:
        return False
