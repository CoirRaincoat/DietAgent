"""Frozen meal-role counterexamples, independent of production classification.

This intentionally audits only known counterexamples plus explicit dessert and
beverage evidence. An empty finding is not proof of universal meal suitability,
serving adequacy or nutritional balance. No RuleEngine or production role import.
"""

from collections.abc import Iterable

ORACLE_VERSION = "main-meal-counterexample-oracle-v11"
_KNOWN_NON_MEALS = {
    "果蔬汁": "drink",
    "黄瓜柠檬水": "drink",
    "柠檬蜂蜜水": "drink",
    "山楂糕": "dessert",
    "蓝莓山药小丸子": "dessert",
    "草莓山药球": "dessert",
    "柠檬百香果蜜": "component",
    "万能凉拌汁": "component",
    "蔬菜碎": "component",
    "黑芝麻丸": "dessert",
    "柠檬红茶冻撞奶": "dessert",
    "芒果牛油果沙冰": "dessert",
    "陈皮秋梨汤": "dessert",
    "玫瑰山药": "dessert",
    "甘蔗马蹄水": "drink",
    "黑芝麻糕": "dessert",
    "马蹄糕": "dessert",
    "妩媚妃子笑": "drink",
    "法式红酒炖梨&白梨挞同烹": "dessert",
    "宫廷酸梅汤": "drink",
}


def main_meal_findings(
    name: str, ingredients: str, labels: Iterable[str] = (), *, steps: str = ""
) -> list[str]:
    """Report known non-meal evidence from source, never from response verdicts."""
    name = "".join(name.split())
    findings: list[str] = []
    if name in _KNOWN_NON_MEALS:
        findings.append(f"known_{_KNOWN_NON_MEALS[name]}:{name}")
    # Separately reviewed source counterexamples; no production classifier.
    # Evidence checks allow a same-title, genuinely different savory recipe.
    if name == "椰子玛格丽特" and all(term in ingredients for term in ("糖粉", "黄油", "面粉")):
        if "按扁" in steps and "面团" in steps:
            findings.append("reviewed_cookie_preparation:椰子玛格丽特")
    if name == "山药酸奶盆栽" and "饼干" in ingredients and "酸奶" in ingredients:
        if "饼干" in steps and "撒" in steps:
            findings.append("reviewed_yogurt_cookie_dessert:山药酸奶盆栽")
    if name == "固元阿胶糕" and "阿胶" in ingredients and "冰糖" in ingredients:
        if "冷却" in steps and "切块" in steps:
            findings.append("reviewed_sweet_gel_block:固元阿胶糕")
    if name == "木瓜炖银耳" and all(term in ingredients for term in ("木瓜", "银耳", "冰糖")):
        if not any(term in ingredients for term in ("盐", "排骨", "鸡肉", "鸡胸肉", "生抽")):
            findings.append("reviewed_sweet_fruit_broth:木瓜炖银耳")
    if name == "红豆薏米粥" and "红豆" in ingredients and "薏米" in ingredients:
        if "混合" in steps and not any(term in steps for term in ("煮", "熬", "蒸", "开始烹饪")):
            if not any(term in ingredients for term in ("熟红豆", "熟薏米")):
                findings.append("reviewed_missing_grain_preparation:红豆薏米粥")
    if name == "果味山药" and all(term in ingredients for term in ("山药", "蜂蜜", "蓝莓酱")):
        if "山药泥" in steps and "裱花" in steps:
            findings.append("reviewed_piped_sweet_root:果味山药")
    if name == "老陈皮百合山药炖银耳" and all(term in ingredients for term in ("银耳", "冰糖", "山药")):
        if not any(term in ingredients for term in ("盐", "生抽", "肉", "虾")):
            findings.append("reviewed_sweet_root_broth:老陈皮百合山药炖银耳")
    if name == "红豆沙南瓜汤圆" and all(term in ingredients for term in ("糯米粉", "红豆沙")):
        if "包入" in steps and "汤圆" in steps:
            findings.append("reviewed_sweet_rice_balls:红豆沙南瓜汤圆")
    if name == "儿童肉松" and "肉" in ingredients and "撕成细条" in steps:
        if "肉松" in steps and "智能程序" in steps:
            findings.append("reviewed_meat_floss_component:儿童肉松")
    # New discoveries intentionally fail current replay outputs until fixed.
    # Do not weaken this oracle to copy production's still-incorrect verdicts.
    if name == "南瓜糯米球" and "白砂糖" in ingredients and "糯米" in ingredients:
        if "慢炸" in steps and "芝麻球" in steps:
            findings.append("reviewed_sweet_fried_rice_balls:南瓜糯米球")
    if name == "红枣百合蒸南瓜" and "冰糖" in ingredients and "南瓜" in ingredients:
        if "糖浆" in steps and "淋" in steps:
            findings.append("reviewed_syrup_dressed_dessert:红枣百合蒸南瓜")
    if name == "红枣蒸山药泥" and "白砂糖" in ingredients and "山药" in ingredients:
        if "搅打" in steps and "蒸制" in steps:
            findings.append("reviewed_sweet_mashed_root:红枣蒸山药泥")
    if name == "桂花蜂蜜烤南瓜" and all(term in ingredients for term in ("南瓜", "蜂蜜", "桂花酱")):
        if "混合" in steps and "刷" in steps and not any(term in ingredients for term in ("盐", "肉", "生抽")):
            findings.append("reviewed_floral_honey_glaze:桂花蜂蜜烤南瓜")
    if name == "蒸山药" and all(term in ingredients for term in ("山药", "红糖")):
        if "蘸红糖" in steps and not any(term in ingredients for term in ("盐", "肉", "酱油")):
            findings.append("reviewed_sugar_dipped_root:蒸山药")
    if name == "冰糖银耳西瓜盅" and all(term in ingredients for term in ("西瓜", "银耳", "冰糖")):
        if "蒸" in steps and not any(term in ingredients for term in ("盐", "肉", "生抽")):
            findings.append("reviewed_fruit_silver_ear_dessert:冰糖银耳西瓜盅")
    for label in labels:
        if label.strip() in {"甜品", "甜点", "甜品风味"}:
            findings.append(f"dessert_label:{label.strip()}")
    if name.endswith(("奶茶", "奶昔", "果汁", "气泡水")):
        findings.append(f"beverage_name:{name}")
    # An independently reviewed source counterexample, not a universal detector.
    # Completed variants must not fail just because they have the same title.
    if (
        name == "黄芪汽锅鸡"
        and "切块" in steps
        and "备好" in steps
        and not any(word in steps for word in ("蒸熟", "蒸制", "煮", "炖", "装盘", "开始烹饪"))
    ):
        findings.append("known_preparation_only_steps:黄芪汽锅鸡")
    if (
        name == "虾滑"
        and "依需求使用" in steps
        and not any(word in steps for word in ("煮熟", "蒸熟", "炒熟", "装盘"))
    ):
        findings.append("known_intermediate_component:虾滑")
    if name in {"绿豆汤", "红豆汤", "银耳莲子羹", "银耳蛋汤", "甜玉米羹", "南瓜甜汤", "小吊梨汤"}:
        # Explicit sweetener ingredients, not a '甜' taste label alone. Savory
        # versions with meat or salt must not be presumed dessert by this oracle.
        text = "".join(ingredients.split())
        if any(s in text for s in ("冰糖", "白砂糖", "白糖", "蜂蜜")) and not any(
            s in text for s in ("排骨", "鸡肉", "猪肉", "鱼肉", "虾", "盐", "生抽")
        ):
            findings.append(f"sweet_soup:{name}")
    return findings
