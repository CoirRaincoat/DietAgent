"""Render user-facing meal explanations from verified application facts only."""

import re

from app.domain.models import Intent, MenuItem

NUTRITION_DISCLAIMER = (
    "营养信息仅基于现有食材与做法作定性参考；当前缺少份量和完整营养数据，"
    "不能据此计算个人摄入量或判断健康效果。"
)

_HEADERS = {
    "initial": (
        "已按你的要求搭配好本餐，共 {count} 道菜。",
        "本餐菜单已安排完成，共 {count} 道菜。",
    ),
    "replace": (
        "已按你的要求局部换菜，其他可保留的菜品没有改动。",
        "这次只调整了需要替换的位置，其余菜单保持不变。",
    ),
    "reject": (
        "已避开你否定的菜品，并重新补齐本餐菜单。",
        "已根据你的反馈重新安排菜单，不再使用刚才否定的菜品。",
    ),
    "explain": (
        "下面说明这份菜单的搭配依据。",
        "这份菜单主要依据以下已核验信息进行搭配。",
    ),
    "update": (
        "已应用你刚补充的要求，并尽量保留仍然合适的菜品。",
        "已按本轮新增条件更新菜单，未受影响的部分尽量保留。",
    ),
}


def verified_facts(menu: list[MenuItem]) -> dict[str, str]:
    """Build selectable facts without exposing internal recipe identifiers."""
    facts = {
        "catalog": "菜品均来自当前菜谱库，食材和步骤可在菜品卡片中查看。",
        "constraints": "已根据档案和本轮明确的过敏、忌口要求完成规则检查。",
    }
    for item in menu:
        detail = _dish_detail(item, detailed=False)
        facts[f"dish_{item.slot}"] = f"{item.name}：{detail}"
    return facts


def render_reason(
    intent: Intent,
    menu: list[MenuItem],
    previous_ids: list[str],
    changes: list[dict],
    selected_fact_ids: list[str],
    revision: int,
) -> str:
    """Render concise scenario-aware copy from verified menu data."""
    scenario = _scenario(intent, previous_ids)
    header_family = _HEADERS[scenario]
    lines = [header_family[revision % len(header_family)].format(count=len(menu))]
    lines.extend([
        "菜品均来自当前菜谱库，食材和步骤可在菜品卡片中查看。",
        "已根据档案和本轮明确的过敏、忌口要求完成规则检查。",
    ])

    changed_slots = {change["slot"] for change in changes}
    if previous_ids:
        kept = sum(old == item.recipe_id for old, item in zip(previous_ids, menu))
        lines.append(f"与上一版相比，保留了原位置上的 {kept} 道菜。")

    selected_slots = [
        int(fact_id.removeprefix("dish_"))
        for fact_id in selected_fact_ids
        if re.fullmatch(r"dish_\d+", fact_id)
    ]
    if scenario in {"replace", "update"} and changed_slots:
        slots = sorted(changed_slots)
    else:
        all_slots = [item.slot for item in menu]
        slots = list(dict.fromkeys(selected_slots + all_slots))

    by_slot = {item.slot: item for item in menu}
    for slot in slots:
        item = by_slot.get(slot)
        if item is None:
            continue
        lines.append(f"- {item.name}：{_dish_detail(item, intent.action == 'explain')}")

    lines.append(NUTRITION_DISCLAIMER)
    return "\n".join(lines)


def _scenario(intent: Intent, previous_ids: list[str]) -> str:
    if intent.action in {"replace", "reject", "explain"}:
        return intent.action
    return "update" if previous_ids else "initial"


def _dish_detail(item: MenuItem, detailed: bool) -> str:
    notes = [_clean_note(note) for note in item.nutrition_notes]
    notes = list(dict.fromkeys(note for note in notes if note))
    if not notes:
        return "已按当前过敏与忌口要求完成食材核对。"
    limit = 2 if detailed else 1
    return "；".join(note.rstrip("。") for note in notes[:limit]) + "。"


def _clean_note(note: str) -> str:
    """Remove repetitive limitations while retaining ingredient-grounded facts."""
    if note.startswith("配料含蛋白质来源："):
        sources = note.removeprefix("配料含蛋白质来源：").split("；", 1)[0]
        return f"主要蛋白质来源包括{sources}"
    if note.startswith("含蔬菜类食材"):
        return "含蔬菜类食材，可增加本餐食材种类"
    if note.startswith("包含主食类食材"):
        return "包含主食类食材"
    if note.startswith("记录的烹饪方式："):
        method = note.removeprefix("记录的烹饪方式：").split("；", 1)[0]
        return f"菜谱记录的烹饪方式为{method}"
    if note.startswith("配料含添加糖来源"):
        return "配料中含添加糖来源，用量需结合菜谱记录进一步核对"
    if note.startswith("配料含盐或含钠调味品"):
        return "配料中含盐或含钠调味品，用量需结合菜谱记录进一步核对"
    if note.startswith("本分析为") or "未计算" in note or "不能判断" in note:
        return ""
    return note.split("；", 1)[0].rstrip("。")
