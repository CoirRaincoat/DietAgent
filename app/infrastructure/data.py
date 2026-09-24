"""Read-only, traceable normalization of the three supplied competition datasets."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.domain.models import Ingredient, Recipe, UserProfile

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECIPE_PATH = Path("dataset/recipe_kb/recipes_sample_2000.csv")
PROFILE_PATH = Path("dataset/user_profile/50个用户健康档案_详细版7.13.json")
DIALOGUE_PATH = Path("dataset/evaluation/dialogues.json")
RECIPE_FIELDS = ("名称", "食材清单", "烹饪步骤", "label")

# Only descriptive metadata is promoted. Source health/population labels remain
# available in raw_label; they are not verified health or suitability claims.
MEAL_LABELS = {"早餐", "午餐", "晚餐", "下午茶", "夜宵"}
FLAVOR_LABELS = {
    "清淡", "甜", "咸", "辣", "酸", "香", "鲜", "鲜美", "微辣", "咸香",
    "蒜香", "咸鲜", "香脆", "酱香", "奶香", "豉香", "香辣", "脆", "酒香",
    "五香", "嫩", "黑椒味", "原味", "软糯", "油腻", "酸甜", "酸辣",
}
CUISINE_LABELS = {
    "西餐风味", "川湘菜", "江浙菜", "粤菜", "东北菜", "地方风味", "湘菜",
    "中式面点", "台式风味", "台菜", "西北风味", "韩式风味", "海鲜风味",
    "甜品", "甜点", "甜品风味",
}
LABEL_ALLOWLIST = MEAL_LABELS | FLAVOR_LABELS | CUISINE_LABELS
HEALTH_GOAL_ALIASES = {"降血压": "降压", "控制血糖": "控糖", "减重": "减脂"}
PREFERENCE_ALIASES = {"偏辣": "辣", "辛辣": "辣", "辣味": "辣", "酸味": "酸"}

_PREFIX = re.compile(r"^(?:主料|辅料|调料|配料|[A-Za-z]料)\s*[:：]\s*")
_NUMBER = r"\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?"
_UNIT = r"千克|公斤|毫升|人份|克|小勺|大勺|茶勺|小匙|大匙|茶匙|汤匙|小撮|小片|小块|小把|小个|小粒|勺|个|颗|只|条|根|片|瓣|块|张|粒|把|杯|袋|盒|枚|斤|两|滴|段|朵|束|包|棵|瓶|串|份|节|支|枝|盏|套|方|米|kg|ml|g|l|t"
_QUANTITY = re.compile(
    rf"^(?P<name>.+?)(?P<quantity>{_NUMBER})\s*(?P<unit>{_UNIT})\s*$",
    re.IGNORECASE,
)
_UNCERTAIN_QUANTITY = re.compile(
    rf"(?:{_NUMBER}\s*[-—~～至]\s*{_NUMBER}|半|几|少量|少许|适量|若干|各适量)"
    rf"(?:{_UNIT})?\s*$",
    re.IGNORECASE,
)


@dataclass
class DataCatalog:
    profiles: dict[int, UserProfile]
    recipes: dict[str, Recipe]
    quality_report: dict[str, Any]
    dialogues: list[dict[str, Any]] = field(default_factory=list)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _without_parentheses(value: str) -> str:
    # Handle duplicated/nested parentheses in the source without dropping raw.
    previous = None
    while value != previous:
        previous = value
        value = re.sub(r"[（(][^（）()]*[）)]", "", value)
    return value.strip()


def parse_ingredients(raw: str) -> tuple[list[Ingredient], list[str]]:
    """Extract explicitly written amounts only; never estimate grams or servings."""
    ingredients: list[Ingredient] = []
    flags: list[str] = []
    for part in re.split(r"[；;\n]+", raw):
        source = part.strip()
        if not source:
            continue
        cleaned = _without_parentheses(_PREFIX.sub("", source)).strip(" ，,。")
        quantity: float | None = None
        unit: str | None = None
        # Ranges need to be recognized before a numeric suffix (1-2克 != 2克).
        uncertain = _UNCERTAIN_QUANTITY.search(cleaned)
        match = None if uncertain else _QUANTITY.fullmatch(cleaned)
        if uncertain:
            name = cleaned[: uncertain.start()].strip()
        elif match and not re.search(rf"{_NUMBER}\s*(?:{_UNIT})|\d+[/+—-]$", match["name"], re.IGNORECASE):
            name = match["name"].strip()
            written = match["quantity"].split("/")
            quantity = float(written[0])
            if len(written) == 2:
                quantity = quantity / float(written[1]) if float(written[1]) else None
            unit = {"g": "克", "kg": "千克", "ml": "毫升", "l": "升"}.get(
                match["unit"].lower(), match["unit"]
            )
        else:
            # Ambiguous compound amounts keep the food name, never invent grams.
            amount = re.search(rf"{_NUMBER}\s*(?:{_UNIT})", cleaned, re.IGNORECASE)
            name = cleaned[: amount.start()].strip() if amount else cleaned
            if amount:
                flags.append("ambiguous_ingredient_quantity")
        if not name or len(name) > 35 or "自定义" in name or re.search(r"[:：；;{}\[\]]", name):
            flags.append("unparsed_ingredients")
        ingredients.append(Ingredient(raw=source, name=name or cleaned or source, quantity=quantity, unit=unit))
    if not ingredients:
        flags.append("missing_ingredients")
    return ingredients, _unique(flags)


def parse_labels(raw: str) -> tuple[list[str], list[str]]:
    if not raw.strip():
        return [], ["missing_labels"]
    tokens = [x.strip() for x in re.split(r"[、,，;；\n]+", raw) if x.strip()]
    labels = _unique(x for x in tokens if x in LABEL_ALLOWLIST)
    # No arbitrary JSON/prose extraction: an explanatory paragraph must not
    # become a collection of asserted labels just because it mentions them.
    flags = ["discarded_label_tokens"] if any(x not in LABEL_ALLOWLIST for x in tokens) else []
    return labels, flags


# Culinary protein-source metadata must have ingredient evidence. Names such as
# 鱼香茄子 and animal-named mushrooms do not prove the presence of fish or meat.
_PROTEIN_INGREDIENT_TOKENS = (
    "肉", "排骨", "鸡", "鸭", "鹅", "鱼", "虾", "蟹", "牛腩", "牛腱", "里脊",
    "蹄", "蛤", "蛏", "鲍", "生蚝", "牡蛎", "扇贝", "豆腐", "豆干", "腐竹",
    "百叶", "蛋", "牛排", "羊排", "羊腿", "小排", "牛仔骨", "肘", "鸽",
    "花甲", "豆皮", "香干", "豆花", "腊肠", "火腿", "培根", "猪", "凤爪",
    "龙骨", "青口贝", "银鲳", "东星斑", "骨头",
)
_PROTEIN_FALSE_FRIENDS = (
    "鸡精", "鸡粉", "鸡汁", "鸡汤", "鸡高汤", "鸡味", "鸡肉粉", "鸡油",
    "牛肉粉", "牛肉汁", "牛肉汤", "牛骨汤", "猪骨汤", "骨汤", "高汤",
    "鲍汁", "鲍鱼汁", "鱼露", "鱼油", "鱼豉油", "鱼香酱", "蚝油",
    "海鲜酱", "虾酱", "虾油", "虾粉", "蟹酱", "蟹粉调味", "猪油",
    "鸭油", "鹅油", "蛋黄酱", "鸡腿菇", "鸡枞", "鸡头米", "鱼腥草",
    "蟹味菇", "鸭梨",
)


def _has_protein_source(ingredients: list[Ingredient]) -> bool:
    return any(
        any(token in item.name for token in _PROTEIN_INGREDIENT_TOKENS)
        and not any(token in item.name for token in _PROTEIN_FALSE_FRIENDS)
        for item in ingredients
    )


def classify_recipe(name: str, ingredients: list[Ingredient], steps: str, labels: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Heuristic culinary categories, not evidence of nutrient concentrations."""
    primary = name + " " + " ".join(item.name for item in ingredients[:3])
    categories: list[str] = []
    if any(token in name for token in ("打发", "面团", "发酵", "揉面", "测试菜")) or name in {"冰糖粉", "乳化", "高温快煮", "68℃慢煮", "59℃慢煮", "派皮", "万能凉拌汁"} or name.endswith(("果酱", "辣椒酱", "番茄酱", "秋梨膏", "草莓酱", "蓝莓酱", "拌饭酱")):
        categories.append("component")
    if any(token in name for token in ("蛋糕", "饼干", "布丁", "冰淇淋", "冰激凌", "泡芙", "马卡龙", "巧克力", "蛋挞", "糖水", "桃胶", "雪媚娘", "麻薯", "双皮奶", "糯米糍", "大福", "慕斯", "糍粑", "冰棍", "班戟", "拉糕", "糯米糕", "月饼", "司康", "钵仔糕", "甜甜圈", "达克瓦兹")) or name.endswith(("派", "酥", "奶冻", "糖", "冻", "甜点")) or {"甜品", "甜点"} & set(labels):
        categories.append("dessert")
    if name.endswith(("茶", "奶昔", "豆浆", "咖啡", "果汁", "饮", "饮品", "牛奶", "鲜奶", "拿铁", "星冰乐", "五谷浆")) or "果饮" in name:
        categories.append("drink")
    if "汤" in name or "羹" in name or name == "腌笃鲜":
        categories.append("soup")
    if any(token in name for token in ("米饭", "炒饭", "盖饭", "焖饭", "烩饭", "面条", "意面", "蝴蝶面", "拉面", "炒面", "拌面", "乌冬", "馒头", "包子", "饺", "馄饨", "粥", "披萨", "比萨", "面包", "饭团", "米粉", "粉丝", "粉条", "年糕", "春卷", "菜卷", "煎饼", "烧饼", "杂粮", "红薯", "紫薯", "玉米", "土豆", "马铃薯", "吐司", "花卷", "法棍", "烧麦", "馍", "发糕")) or name.endswith(("饭", "面", "饼", "包", "粽", "芋艿")):
        categories.append("staple")
    if _has_protein_source(ingredients):
        categories.append("protein")
    if any(token in primary for token in ("白菜", "生菜", "青菜", "菠菜", "油麦", "西兰花", "花菜", "菜花", "芥蓝", "卷心菜", "包菜", "芹菜", "空心菜", "苋菜", "荠菜", "茄", "瓜", "萝卜", "芦笋", "竹笋", "冬笋", "蘑菇", "香菇", "木耳", "秋葵", "莴笋", "菜心", "韭菜", "莲藕", "豆芽", "西葫芦", "金针菇", "口蘑", "塔菜", "娃娃菜", "山药", "淮山", "藕", "海带", "苕尖", "春笋", "豇豆", "刀豆", "毛豆", "青椒", "尖椒")):
        categories.append("vegetable")
    methods = [method for method in ("蒸", "煮", "炖", "炒", "烤", "煎", "炸", "焖", "拌", "榨汁") if method in name + steps]
    meal_types = [meal for meal in ("早餐", "午餐", "晚餐", "下午茶", "夜宵") if meal in labels]
    # Missing metadata stays missing: the retriever can treat [] as unknown.
    return categories, methods, meal_types


def normalize_recipes(rows: Iterable[Mapping[str, str]]) -> dict[str, Recipe]:
    recipes: dict[str, Recipe] = {}
    occurrences: Counter[str] = Counter()
    for source_row, row in enumerate(rows, start=2):
        if not set(RECIPE_FIELDS).issubset(row):
            raise ValueError(f"Recipe CSV is missing required columns: {RECIPE_FIELDS}")
        source = {key: row[key] or "" for key in RECIPE_FIELDS}
        # ID depends on source content, never on row number or the ambiguous name.
        payload = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        occurrences[fingerprint] += 1
        recipe_id = f"recipe_{fingerprint[:24]}"
        if occurrences[fingerprint] > 1:
            recipe_id += f"_{occurrences[fingerprint]}"
        ingredients, flags = parse_ingredients(source["食材清单"])
        labels, label_flags = parse_labels(source["label"])
        flags.extend(label_flags)
        name, steps = source["名称"].strip(), source["烹饪步骤"]
        categories, methods, meal_types = classify_recipe(name, ingredients, steps, labels)
        if not steps.strip():
            flags.append("missing_steps")
        if not name:
            flags.append("missing_name")
        if "component" in categories:
            flags.append("processing_component")
        if occurrences[fingerprint] > 1:
            flags.append("duplicate_source_record")
        blocked = {"missing_steps", "missing_name", "missing_ingredients", "unparsed_ingredients", "processing_component"}
        recipes[recipe_id] = Recipe(
            recipe_id=recipe_id, name=name, raw_ingredients=source["食材清单"],
            steps=steps, raw_label=source["label"], ingredients=ingredients,
            labels=labels, categories=categories, methods=methods, meal_types=meal_types,
            source_row=source_row, fingerprint=fingerprint,
            eligible=not bool(blocked & set(flags)), quality_flags=_unique(flags),
        )
    return recipes


def normalize_profile(raw: dict[str, Any]) -> UserProfile:
    measurements: dict[str, Any] = {}
    for source_key, value in raw.get("体检指标", {}).items():
        if value is None:
            continue
        metric, _, unit = source_key.partition("_")
        if metric == "血压":
            match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", str(value))
            if match:
                measurements["blood_pressure"] = {
                    "systolic": int(match[1]), "diastolic": int(match[2]),
                    "unit": unit, "source_field": source_key,
                }
            else:
                measurements["blood_pressure"] = {"raw_value": value, "unit": unit, "source_field": source_key}
        else:
            measurements[metric] = {"value": value, "unit": unit, "source_field": source_key}
    pregnancy = re.fullmatch(r"(\d+)周", str(raw.get("孕周期", "")))
    preferences = [str(raw.get("口味偏好", ""))]
    return UserProfile(
        user_id=int(raw["id"]), age=int(raw["年龄"]), sex=raw["性别"],
        height_cm=raw["身高_cm"], weight_kg=raw["体重_kg"], bmi=raw["BMI"],
        activity=raw.get("劳动强度", ""), special_groups=raw.get("特殊人群", []),
        pregnancy_weeks=int(pregnancy[1]) if pregnancy else None,
        preferences=_unique(PREFERENCE_ALIASES.get(x, x) for x in preferences),
        allergies=_unique(raw.get("过敏食材", [])),
        health_goals=_unique(HEALTH_GOAL_ALIASES.get(x, x) for x in raw.get("健康需求", [])),
        measurements=measurements, raw=raw,
    )


def load_catalog(project_root: Path | None = None) -> DataCatalog:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    paths = {"recipes": root / RECIPE_PATH, "profiles": root / PROFILE_PATH, "dialogues": root / DIALOGUE_PATH}
    # Do not silently guess an encoding or discard invalid bytes.
    with paths["recipes"].open(encoding="gb18030", newline="") as stream:
        recipes = normalize_recipes(csv.DictReader(stream))
    source_profiles = json.loads(paths["profiles"].read_text(encoding="utf-8-sig"))
    profiles: dict[int, UserProfile] = {}
    for raw in source_profiles:
        profile = normalize_profile(raw)
        if profile.user_id in profiles:
            raise ValueError(f"Duplicate user profile ID: {profile.user_id}")
        profiles[profile.user_id] = profile
    dialogues = json.loads(paths["dialogues"].read_text(encoding="utf-8-sig"))
    if any(x["turn_count"] != len(x["user_messages"]) for x in dialogues):
        raise ValueError("Dialogue turn_count does not match user_messages")
    names = Counter(recipe.name for recipe in recipes.values())
    report = {
        "schema_version": 1,
        "recipe_count": len(recipes), "profile_count": len(profiles),
        "dialogue_count": len(dialogues),
        "dialogue_turn_count": sum(len(x["user_messages"]) for x in dialogues),
        "recipe_encoding": "gb18030",
        "eligible_recipe_count": sum(recipe.eligible for recipe in recipes.values()),
        "same_name_groups": sum(count > 1 for count in names.values()),
        "quality_flags": dict(sorted(Counter(flag for recipe in recipes.values() for flag in recipe.quality_flags).items())),
        "profiles_without_measurements": sum(not p.measurements for p in profiles.values()),
        "categories": dict(sorted(Counter(c for recipe in recipes.values() for c in recipe.categories).items())),
        "sources": {name: {"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in paths.items()},
        "limitations": [
            "食材名保留原文，别名与过敏映射由规则层统一管理。",
            "未知用量、份数、耗时和营养数值不估算；原始健康标签不是已验证结论。",
            "烹饪类别与方式是启发式检索元数据；缺失餐次保留为空。",
            "对话样例没有绑定用户档案，不可将对话 ID 当作用户 ID。",
        ],
    }
    return DataCatalog(profiles=profiles, recipes=recipes, quality_report=report, dialogues=dialogues)


def build_lexical_index(recipes: Iterable[Recipe]) -> dict[str, list[str]]:
    """Portable exact-term postings; matching strategy belongs to retrieval."""
    postings: dict[str, set[str]] = {}
    for recipe in recipes:
        terms = {recipe.name, *(ingredient.name for ingredient in recipe.ingredients), *recipe.labels, *recipe.categories}
        for term in terms:
            if term:
                postings.setdefault(term, set()).add(recipe.recipe_id)
    return {term: sorted(ids) for term, ids in sorted(postings.items())}
