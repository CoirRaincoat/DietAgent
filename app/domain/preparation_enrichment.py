"""Offline recipe-library enrichment, never a replacement for source provenance.

Every record is screened by finite rules. Unflagged does not mean certified
complete. Draft additions are assistant-authored cooking suggestions, not
recovered source text, human-approved recipes, clinical or execution evidence.
The serving catalog is deliberately not mutated by this offline module.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.cooking_methods import source_cooking_method_evidence
from app.domain.models import Recipe
from app.domain.source_preparation import grain_completion_issue

ENRICHMENT_VERSION = "offline-preparation-enrichment-v1"
CURATION_VERSION: Literal["assistant-preparation-curation-v1"] = "assistant-preparation-curation-v1"
ReviewStatus = Literal["keep_source", "draft_supplement", "review_required"]
CurationDisposition = Literal[
    "supplement_draft", "retain_component", "source_conflict", "needs_confirmation"
]
_NUMBERED = re.compile(r"第\s*(\d+)\s*步\s*[:：]")
_HEAT = re.compile(
    r"蒸(?!锅|笼|架|盘|箱)|烤(?!箱|盘|架)|煮|炖(?!锅)|煎|炸|焖|烧熟|卤(?!汁|料)|煲"
    r"|开始烹饪|开启烹饪|普通蒸|常规烘焙|慢煮粥|老火汤"
)
_NEGATIVE = re.compile(r"无需|不需|不用|不要|不可|不能|尚未|未曾|没有|尚无|不建议|可选|如果")
_OVEN_SETTING = re.compile(
    r"(?:放入|送入|放进|置入)[^。；;\r\n]{0,30}烤箱[^。；;\r\n]{0,40}"
    r"\d{2,3}\s*(?:℃|度|°[Cc])[^。；;\r\n]{0,20}\d+\s*分钟"
)
_HERBAL = ("黄芪", "北芪", "党参", "当归", "白术", "茯苓", "甘草", "沙参", "玉竹")
_GRAINS = ("红豆", "绿豆", "薏米", "薏仁", "粳米", "大米", "糯米", "小米")
_RICE = ("米饭", "熟米饭", "熟饭")
_AROMATICS = ("洋葱", "姜", "蒜", "葱")
_DECLARED_BEEF_BOWL = frozenset(
    {
        "水",
        "米饭",
        "熟米饭",
        "熟饭",
        "肥牛片",
        "牛肉片",
        "胡萝卜片",
        "西兰花",
        "食用油",
        "洋葱丝",
        "姜丝",
        "大蒜",
        "老抽",
        "生抽",
        "味淋",
        "牛丼调味汁",
        "白糖",
    }
)
_PLAIN_BREAD_FOODS = frozenset(
    {
        "面粉",
        "高筋面粉",
        "中筋面粉",
        "低筋面粉",
        "高筋粉",
        "中筋粉",
        "低筋粉",
        "全麦面粉",
        "全麦面包粉",
        "酵母",
        "干酵母",
        "活性干酵母",
        "速发干酵母",
        "燕子酵母",
        "水",
        "牛奶",
        "纯牛奶",
        "黄油",
        "软化黄油",
        "盐",
        "糖",
        "细砂糖",
        "白砂糖",
        "白糖",
        "全蛋液",
        "鸡蛋",
        "蛋清",
        "奶粉",
        "南瓜泥",
        "玉米油",
        "食用油",
    }
)
_DISCLAIMER = (
    "以下为助手补全草稿，不是原始菜谱或已完成的人工审核。原文保留；"
    "不新增食材或估算营养、份数、熟化安全与设备参数。"
    "设备设置、原料状态及成品效果需要实际复核；品牌调味料完整成分仍未知。"
)


@dataclass(frozen=True)
class StepAddition:
    """One labelled insertion; dependencies must be declared source ingredients."""

    __pydantic_config__ = ConfigDict(extra="forbid")

    placement: Literal["before_source", "after_source", "clarification"]
    text: str
    ingredient_names: tuple[str, ...]


class CuratedTag(BaseModel):
    """An authored descriptive candidate, not a verified source or health tag."""

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    tag: str = Field(min_length=1)
    dimension: Literal["culinary_form", "ingredient_structure", "data_quality", "processing_record"]

    @field_validator("tag")
    @classmethod
    def nonblank_tag(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Curation tags cannot be blank")
        return value


class AssistantCuration(BaseModel):
    """Versioned assistant-authored decision anchored to a whole source record.

    This is a review registry entry, not human approval or a serving eligibility
    override. A selected entry is validated again against the current recipe.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")

    policy_version: Literal["assistant-preparation-curation-v1"] = CURATION_VERSION
    recipe_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    disposition: CurationDisposition
    reason: str = Field(min_length=1)
    additions: tuple[StepAddition, ...] = ()
    tag_suggestions: tuple[CuratedTag, ...] = ()
    remaining_questions: tuple[str, ...] = ()
    reviewer_kind: Literal["assistant"] = "assistant"

    @field_validator("recipe_id", "source_name", "reason")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Curation text cannot be blank")
        return value

    @field_validator("additions")
    @classmethod
    def valid_additions(cls, values: tuple[StepAddition, ...]) -> tuple[StepAddition, ...]:
        for addition in values:
            if addition.placement not in {"before_source", "after_source", "clarification"}:
                raise ValueError("Unknown curation step placement")
            if not addition.text.strip():
                raise ValueError("Curation additions cannot be blank")
            if any(not ingredient.strip() for ingredient in addition.ingredient_names):
                raise ValueError("Curation ingredient dependencies cannot be blank")
        return values

    @field_validator("remaining_questions")
    @classmethod
    def nonblank_questions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("Curation questions cannot be blank")
        return values


@dataclass(frozen=True)
class PreparationEnrichment:
    """Sidecar anchored to an exact input, with untouched source text separately."""

    recipe_id: str
    source_fingerprint: str
    source_steps_sha256: str
    source_content_sha256: str
    status: ReviewStatus
    findings: tuple[str, ...]
    additions: tuple[StepAddition, ...]
    revision_sha256: str | None
    limitations: tuple[str, ...]
    policy_version: str = ENRICHMENT_VERSION
    human_review: str = "not_reviewed"
    provider_calls: int = 0
    assistant_curation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _positive_heat(steps: str) -> bool:
    # Negation is local: an unrelated later clause cannot erase real cooking.
    for clause in re.split(r"[。；;\r\n]", steps):
        setting = _OVEN_SETTING.search(clause)
        if setting and not _NEGATIVE.search(clause[: setting.start()]):
            return True
        for action in _HEAT.finditer(clause):
            if not _NEGATIVE.search(clause[: action.start()]):
                return True
    return False


def _content_sha(recipe: Recipe) -> str:
    return hashlib.sha256(
        json.dumps(
            recipe.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rice_gap(recipe: Recipe, foods: set[str], text: str) -> bool:
    """Known truncated pattern: reserve main food, then fry aromatics, no rice."""
    if not recipe.name.strip().endswith("饭") or not foods.intersection(_RICE):
        return False
    # Mere omission is not enough: require explicit preliminary stages and
    # no grain assembly. This is not a blanket ban on any recipe named 饭.
    if "饭" in text or "备用" not in text or "焯" not in text:
        return False
    parts = _NUMBERED.split(recipe.steps)
    tail = parts[-1] if len(parts) > 1 else recipe.steps
    return (
        any(food in tail for food in _AROMATICS)
        and "煸炒" in tail
        and not any(word in tail for word in ("牛肉", "肥牛", "装盘", "盛出", "出锅", "即可食用"))
    )


def _baked_dough_gap(recipe: Recipe, text: str, foods: set[str]) -> bool:
    return (
        any("面粉" in food or "面包粉" in food for food in foods)
        and any(word in text for word in ("揉面", "面团", "水油皮", "油酥"))
        and not _positive_heat(recipe.steps)
        and not any(word in recipe.name for word in ("面团", "揉面", "发酵", "派皮", "测试"))
    )


def _number_gap(steps: str) -> bool:
    numbers = [int(match[1]) for match in _NUMBERED.finditer(steps)]
    return bool(numbers) and numbers != list(range(1, len(numbers) + 1))


def _all_bread_foods_used(recipe: Recipe) -> bool:
    """A finite lexical contrast, not an invented ingredient substitution."""
    for ingredient in recipe.ingredients:
        name = ingredient.name
        aliases = {name, name.removeprefix("软化"), name.removeprefix("固态")}
        if name in {"高筋面粉", "低筋面粉", "中筋面粉"}:
            aliases.add(name.replace("面粉", "粉"))
        if "酵母" in name:
            aliases.add("酵母")
        if not any(alias and alias in recipe.steps for alias in aliases):
            return False
    return True


def _draft(recipe: Recipe, findings: list[str]) -> tuple[StepAddition, ...]:
    foods = {item.name for item in recipe.ingredients}
    # These blockers leave the original mismatch visible instead of covering
    # it with fluent but unsupported instructions.
    if (
        any(term in food for term in _HERBAL for food in foods)
        or "unparsed_ingredients" in recipe.quality_flags
    ):
        return ()
    if "placeholder_or_missing_steps" in findings or "numbered_step_gap" in findings:
        return ()
    if findings == ["missing_grain_cooking"]:
        grains = tuple(food for food in _GRAINS if food in foods)
        if grains and "水" in foods and foods <= set((*_GRAINS, "水", "红糖", "白糖", "冰糖")):
            return (
                StepAddition(
                    "before_source",
                    "先淘洗清单中的"
                    + "、".join(grains)
                    + "，与清单中的水入锅煮至豆谷软烂、形成粥体，再接原文的末段混合步骤。"
                    "水量分配与火力需按锅具和原料状态复核，不把末段混合当成生豆谷烹饪。",
                    (*grains, "水"),
                ),
            )
    if findings == ["missing_rice_and_main_food_assembly"] and foods <= _DECLARED_BEEF_BOWL:
        meat = next((name for name in ("肥牛片", "牛肉片") if name in foods), None)
        rice = next((name for name in _RICE if name in foods), None)
        if meat and rice and {"水", "胡萝卜片", "西兰花"} <= foods:
            seasonings = tuple(
                name for name in ("老抽", "生抽", "味淋", "牛丼调味汁", "白糖") if name in foods
            )
            seasoning_text = "、".join(seasonings)
            return (
                StepAddition(
                    "after_source",
                    "在原文炒香的底料中放回已备用的"
                    + meat
                    + ("，加入清单中的" + seasoning_text if seasonings else "")
                    + "继续翻炒并煮制，使肉片与调味汁结合。"
                    "原文的焯水不作为整菜已经完成的依据。",
                    (meat, *seasonings),
                ),
                StepAddition(
                    "after_source",
                    "放回已备用的胡萝卜片、西兰花并继续烹饪；将清单中的"
                    + rice
                    + "盛入碗中，把肉片、蔬菜和锅中汁料铺在饭上，完成盖饭组装。"
                    "这里以清单中的饭为已制好的饭，不自行补写电饭煲比例或设备程序。",
                    (meat, "胡萝卜片", "西兰花", rice),
                ),
            )
    if findings == ["dough_without_finishing_heat"]:
        plain_bread = recipe.name.endswith(("面包", "吐司", "欧包"))
        yeast = any("酵母" in food for food in foods)
        if plain_bread and yeast and foods <= _PLAIN_BREAD_FOODS and _all_bread_foods_used(recipe):
            # Missing fillings/glazes stay manual. All source ingredients must
            # already participate before this conservative finishing suggestion.
            proofed = bool(re.search(r"发酵至(?:两|2)倍", recipe.steps))
            proofing = "" if proofed else "先完成一次发酵，使面团明显膨胀；"
            shaping = (
                "排气、松弛后擀卷整形，放入适配吐司模具"
                if recipe.name.endswith("吐司")
                else "排气后分割整形，放入适配烤盘"
            )
            return (
                StepAddition(
                    "after_source",
                    "接原文已揉好的面团：" + proofing + shaping + "，完成末次醒发。"
                    "随后使用适配烤箱烘烤成形，取出放凉。"
                    "温度、时间、模具与醒发终点尚未验证，需要试做复核；不沿用揉面档作为烘烤程序。",
                    tuple(dict.fromkeys(item.name for item in recipe.ingredients)),
                ),
            )
    return ()


def audit_preparation(
    recipe: Recipe, *, curation: AssistantCuration | None = None
) -> PreparationEnrichment:
    """Inspect any library record without altering ingredient/source identities."""
    text = "".join(recipe.steps.split())
    foods = {item.name for item in recipe.ingredients}
    findings: list[str] = []
    if not text or re.fullmatch(r"(?:第\d+步[:：])?\d*", text):
        findings.append("placeholder_or_missing_steps")
    if _number_gap(recipe.steps):
        findings.append("numbered_step_gap")
    if grain_completion_issue(recipe.name, foods, recipe.steps):
        findings.append("missing_grain_cooking")
    if _rice_gap(recipe, foods, text):
        findings.append("missing_rice_and_main_food_assembly")
    if _baked_dough_gap(recipe, text, foods):
        findings.append("dough_without_finishing_heat")
    if "preparation_only_steps" in recipe.quality_flags and not _positive_heat(recipe.steps):
        # Drink blending/juice is actual preparation, not raw-meat readiness.
        if not any(word in text for word in ("打碎", "打浆", "榨汁", "研磨", "搅打")):
            findings.append("preparation_only_source")
    if "unparsed_ingredients" in recipe.quality_flags:
        findings.append("unparsed_declared_ingredients")
    additions = _draft(recipe, findings)
    status: ReviewStatus = (
        "draft_supplement" if additions else "review_required" if findings else "keep_source"
    )
    content_sha = _content_sha(recipe)
    selected_curation: dict[str, Any] | None = None
    if curation is not None:
        # Revalidation also rejects model_copy/model_construct shortcuts that
        # would otherwise bypass the frozen, extra-forbid registry schema.
        curation = AssistantCuration.model_validate(curation.model_dump(mode="python"))
        if (
            recipe.recipe_id != curation.recipe_id
            or recipe.name != curation.source_name
            or recipe.fingerprint != curation.source_fingerprint
            or content_sha != curation.source_content_sha256
        ):
            raise ValueError("Curation source identity or whole source content changed")
        if any(not set(item.ingredient_names) <= foods for item in curation.additions):
            raise ValueError("Curation uses an undeclared ingredient")
        selected_curation = curation.model_dump(mode="json")
        if curation.disposition == "supplement_draft":
            # A descriptive-only curation must not erase an existing conservative
            # finishing draft (for example the plain white bread contrast).
            additions = curation.additions or additions
            status = (
                "draft_supplement"
                if additions
                else "review_required" if findings else "keep_source"
            )
        else:
            # Source conflicts and unspecified appliance/component records do
            # not become a meal simply because a partial suggestion was written.
            additions = curation.additions
            status = "review_required"
    payload = {
        "source_id": recipe.recipe_id,
        "fingerprint": recipe.fingerprint,
        "source_steps": recipe.steps,
        "source_content_sha256": content_sha,
        "policy": ENRICHMENT_VERSION,
        "additions": [asdict(addition) for addition in additions],
        "assistant_curation": selected_curation,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return PreparationEnrichment(
        recipe_id=recipe.recipe_id,
        source_fingerprint=recipe.fingerprint,
        source_steps_sha256=hashlib.sha256(recipe.steps.encode("utf-8")).hexdigest(),
        source_content_sha256=content_sha,
        status=status,
        findings=tuple(findings),
        additions=additions,
        revision_sha256=digest if additions or selected_curation is not None else None,
        limitations=(
            "未触发规则不等于已确认完整；有限规则会漏掉缺口。",
            "离线补全稿不自动进入推荐；原料/份量/原始健康标签保持不变。",
            _DISCLAIMER,
        ),
        assistant_curation=selected_curation,
    )


def supplemented_steps(
    recipe: Recipe, review: PreparationEnrichment, *, curation: AssistantCuration | None = None
) -> str:
    """Return a labelled review-only view; stale or mismatched source is rejected."""
    if (
        recipe.recipe_id != review.recipe_id
        or recipe.fingerprint != review.source_fingerprint
        or hashlib.sha256(recipe.steps.encode("utf-8")).hexdigest() != review.source_steps_sha256
        or _content_sha(recipe) != review.source_content_sha256
    ):
        raise ValueError("Enrichment source identity or original steps changed")
    declared = {item.name for item in recipe.ingredients}
    if any(not set(addition.ingredient_names) <= declared for addition in review.additions):
        raise ValueError("Enrichment uses an undeclared ingredient")
    if audit_preparation(recipe, curation=curation) != review:
        raise ValueError("Enrichment is not the versioned draft for this source")
    if not review.additions:
        return recipe.steps
    before = [
        "【助手补全草稿·原文之前】" + item.text
        for item in review.additions
        if item.placement == "before_source"
    ]
    after = [
        "【助手补全草稿·原文之后】" + item.text
        for item in review.additions
        if item.placement == "after_source"
    ]
    clarifications = [
        "【助手补全草稿·做法澄清】" + item.text
        for item in review.additions
        if item.placement == "clarification"
    ]
    return "\n".join(
        [_DISCLAIMER, *before, "【原始步骤·未改写】", recipe.steps, *after, *clarifications]
    )


def tag_enrichment(
    recipe: Recipe, review: PreparationEnrichment, *, curation: AssistantCuration | None = None
) -> dict[str, Any]:
    """Propose descriptive tags; supplied health/population tags stay unverified.

    An empty action/role proposal for flagged preparation is intentional. Draft
    baking/boiling is not promoted as an observed action in the original record.
    Meal-time and flavor labels supplied by the source remain references, not
    confirmations of adequacy, measured heat, dietary safety or health benefit.
    """
    supplemented_steps(recipe, review, curation=curation)  # Same exact-source binding.
    raw = tuple(
        dict.fromkeys(
            token.strip() for token in re.split(r"[、,，;；\n]+", recipe.raw_label) if token.strip()
        )
    )
    role_names = {
        "vegetable": "蔬菜类成菜",
        "protein": "肉蛋豆类成菜",
        "staple": "主食类",
        "soup": "汤羹类",
        "dessert": "甜品类",
        "drink": "饮品类",
        "component": "加工组件",
    }
    proposals = []
    role_method_blocked = bool(review.findings) or (
        curation is not None and curation.disposition != "supplement_draft"
    )
    if not role_method_blocked:
        for role in recipe.categories:
            if role in role_names:
                proposals.append(
                    {
                        "tag": role_names[role],
                        "dimension": "culinary_role",
                        "basis": "原始食材/名称/步骤的有限主要角色判定；不代表营养含量。",
                        "status": "heuristic_not_human_reviewed",
                    }
                )
        evidence = source_cooking_method_evidence(recipe.steps)
        for method in evidence.main_methods:
            proposals.append(
                {
                    "tag": method,
                    "dimension": "cooking_method",
                    "basis": "原始步骤的有限成菜动作证据；不采用助手补全动作。",
                    "status": "source_text_rule_not_execution_measurement",
                }
            )
    if curation is not None:
        for suggestion in curation.tag_suggestions:
            proposals.append(
                {
                    "tag": suggestion.tag,
                    "dimension": suggestion.dimension,
                    "basis": "助手逐条核查的描述性建议：" + curation.reason,
                    "status": "assistant_authored_not_human_reviewed",
                }
            )
    source_reference = list(dict.fromkeys(recipe.labels))
    unverified = [tag for tag in raw if tag not in source_reference]
    return {
        "source_raw_label": recipe.raw_label,
        "source_tokens": raw,
        "source_reference_tags": source_reference,
        "source_unverified_tokens": unverified,
        "proposed_descriptive_tags": proposals,
        "tags_to_add_to_review_view": [
            proposal["tag"] for proposal in proposals if proposal["tag"] not in raw
        ],
        "preparation_gap_blocks_verified_role_and_method_tags": role_method_blocked,
        "human_review": "not_reviewed",
        "automatically_applied": False,
        "limitations": [
            "已有标签只是参考；未识别的原标签保留待核查，不从原文件删除。",
            "不新增健康功效、人群适用、过敏安全、定量营养或餐次适配结论。",
            "未发现做法缺口不是菜谱完整性或标签准确率认证。",
        ],
    }
