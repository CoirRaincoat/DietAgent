"""Finite whole-preparation evidence; food ingredients alone do not define a dish."""

import re
from collections.abc import Iterable

PREPARATION_VERSION = "source-preparation-evidence-v6-thumb-shaped-sweet-dough"
_SUGAR = ("白糖", "白砂糖", "糖粉", "冰糖", "蜂蜜", "红糖")
_MEAT = (
    "猪肉",
    "鸡肉",
    "鸡胸肉",
    "鸡腿肉",
    "鸭肉",
    "牛肉",
    "羊肉",
    "排骨",
    "鱼肉",
    "虾肉",
)
_SAVORY = (
    *_MEAT,
    "盐",
    "生抽",
    "酱油",
    "蚝油",
)
_RAW_GRAIN = ("红豆", "绿豆", "薏米", "薏仁", "粳米", "大米", "糯米", "小米")


def _thumb_shaped_cookie(foods: str, text: str) -> bool:
    """A completed ordered sweet-dough assembly, not a pastry-name blacklist.

    The new construction requires explicit small units, thumb pressing with a
    flattened/cracked result, then actual baking. Merely using an oven, flour,
    eggs or a thumb-shaped garnish does not supply a finished cookie identity.
    Fermented, filled and declared meat preparations stay outside this rule.
    """
    if not (
        any(term in foods for term in _SUGAR)
        and "黄油" in foods
        and any(term in foods for term in ("面粉", "低筋粉"))
        and "面团" in text
    ) or any(term in foods for term in (*_MEAT, "酵母", "发酵粉", "泡打粉")):
        return False
    if any(term in text for term in ("发酵", "包馅", "包入", "裹入")):
        return False
    patterns = (
        r"小面团[^。；;]{0,25}(?:搓圆|搓成圆|揉圆)",
        r"(?:大拇指|拇指|指腹)[^。；;]{0,20}按压[^。；;]{0,20}(?:裂开|压扁)",
        r"(?:烘烤|烤至|烤熟)",
    )
    cursor = 0
    for pattern in patterns:
        found = False
        for match in re.finditer(pattern, text):
            if match.start() < cursor:
                continue
            clause_start = max(text.rfind(mark, 0, match.start()) for mark in "。；;") + 1
            prefix = text[clause_start : match.start()]
            clause_end = min(
                (position for mark in "。；;" if (position := text.find(mark, match.end())) >= 0),
                default=len(text),
            )
            clause = text[clause_start:clause_end]
            if (
                any(term in prefix for term in (
                    "不要", "不可", "不能", "禁止", "勿", "不需", "无需", "例如", "示例",
                ))
                or re.search(r"(?:不|未|尚未|无须)[^，,。；;]{0,12}$", prefix)
                or any(mark in clause for mark in ("“", "”", "\"", "「", "」"))
            ):
                continue
            cursor = match.end()
            found = True
            break
        if not found:
            return False
    return True


def _finished_piped_sweet_batter(foods: str, text: str) -> bool:
    """A narrow asserted sugar/butter/flour batter -> piping -> baked finish.

    Does not ban thin breads by title, certify cooking safety, infer sugar
    quantities, or accept an equipment noun as a baking action. Ambiguous,
    quoted/alternative preparations remain outside this finite rule.
    """
    if not (
        any(term in foods for term in _SUGAR)
        and "黄油" in foods
        and any(term in foods for term in ("面粉", "低筋粉"))
    ):
        return False
    if any(term in foods for term in (*_SAVORY, "葱", "蒜", "酵母", "发酵粉", "泡打粉")):
        return False
    # Source appliance controls are quoted labels, not quoted recipe actions.
    # This recognition-only view never replaces the stored source instructions.
    evidence_text = text.replace("“开始烹饪”", "开始烹饪").replace("“开启烹饪”", "开启烹饪")
    if re.search(
        r"如果|假如|假设|可选|也可|示例|举例|参考|另一个|有人说|可能|建议|[“”‘’\"『』]|发酵"
        r"|或者(?:将|把|挤|烤|煎|蒸|制作)",
        evidence_text,
    ):
        return False
    stages = (
        re.compile(r"面糊"),
        re.compile(r"裱花袋"),
        re.compile(r"挤(?:成|出|在)[^，,。；;]{0,28}(?:条状|条条|条形|圆片|面糊)"),
        re.compile(r"烤(?:到|至|成|熟)[^，,。；;]{0,20}(?:金黄|酥脆|熟)|烤熟"),
    )
    witnessed: list[list[int]] = [[] for _ in stages]
    for clause in re.finditer(r"[^，,。；;\n]+", evidence_text):
        value = clause.group()
        # Local qualification: source '不要打发' in an earlier clause does
        # not negate a later affirmative piping/baking instruction.
        if re.search(
            r"不要|不用|无需|不必|不能|尚未|没有|禁止|勿|并非|可以|也可|可用|可烤"
            r"|(?:不|未|非)[^，,。；;]{0,12}(?:裱花|挤|烤)"
            r"|可(?:将|把|选择|改|装|挤)",
            value,
        ):
            continue
        for index, pattern in enumerate(stages):
            witnessed[index].extend(clause.start() + m.start() for m in pattern.finditer(value))
    position = -1
    for spans in witnessed:
        later = [span for span in spans if span > position]
        if not later:
            return False
        position = min(later)
    return True


def crumb_coated_entree(ingredient_names: Iterable[str], steps: str) -> bool:
    """Distinguish explicit cracker-coated meat frying from dessert assembly."""
    foods = "、".join(ingredient_names)
    return (
        "饼干" in foods
        and "裹" in steps
        and any(term in foods for term in ("鸡肉", "鸡胸肉", "鸡腿肉", "猪肉", "鱼肉", "虾肉"))
        and any(action in steps for action in ("煎熟", "炸熟", "烤熟"))
    )


def preparation_dessert_evidence(ingredient_names: Iterable[str], steps: str) -> list[str]:
    """Identify a few explicit dessert assemblies without title blacklists.

    Args:
        ingredient_names: Declared parsed names, not nutrition quantities.
        steps: Unmodified source instructions.

    Returns:
        Finite explanations for cookie shaping, sweet batter piping/baking, cookie/yogurt assemblies,
        sweet gel blocks, fruit/silver-ear broth and explicit sweet-root
        assemblies. Savory seasoning
        protects broth and gel contrasts; a pinching salt in cookies does not
        magically turn their explicit confectionery assembly into an entrée.
        Other preparations remain outside this finite recognition claim.
    """
    ingredients = tuple(ingredient_names)
    foods = "、".join(ingredients)
    text = "".join(steps.split())
    sweet = any(term in foods for term in _SUGAR)
    savory = any(term in foods for term in _SAVORY)
    findings: list[str] = []
    cookie = (
        sweet
        and "黄油" in foods
        and any(term in foods for term in ("面粉", "低筋粉"))
        and "面团" in text
        and "按扁" in text
        and ("烤" in text or re.search(r"\d{3}(?:度|℃|°[Cc])", text))
        and not any(term in foods for term in ("酵母", "发酵粉", "泡打粉"))
    )
    yogurt_assembly = (
        "酸奶" in foods
        and any(term in foods for term in ("饼干", "奥利奥"))
        and any(term in text for term in ("碾碎", "饼干末", "饼干碎"))
        and any(term in text for term in ("撒在", "撒上", "混合", "搅打"))
        and not crumb_coated_entree(ingredients, text)
    )
    gel_blocks = (
        sweet
        and not savory
        and any(term in foods for term in ("阿胶", "吉利丁", "明胶"))
        and "冷却" in text
        and any(term in text for term in ("切块", "切片", "脱模"))
    )
    fruit_broth = (
        sweet
        and not savory
        and "银耳" in foods
        and ("水" in foods or any(term in text for term in ("加水", "加入清水", "加清水")))
        and any(term in foods for term in ("木瓜", "雪梨", "苹果", "红枣", "百合", "山药", "西瓜"))
        and any(term in text for term in ("煮", "炖", "开始烹饪", "开启烹饪"))
    )
    piped_root = (
        "山药" in foods
        and sweet
        and any(term in foods for term in ("果酱", "蓝莓酱", "草莓酱", "菠萝酱"))
        and any(term in text for term in ("碾碎", "山药泥"))
        and any(term in text for term in ("裱花", "挤出", "挤在"))
        and not any(term in foods for term in _MEAT)
    )
    sweet_filled_balls = (
        "糯米粉" in foods
        and any(term in foods for term in ("红豆沙", "豆沙馅"))
        and "包入" in text
        and any(term in text for term in ("汤圆", "制圆", "搓成圆"))
        and not any(term in foods for term in _MEAT)
    )
    # Require the whole sweet confectionery preparation, not a pumpkin title
    # or the mere presence of sugar. Some source records declare 糯米 while
    # their preparation explicitly uses 糯米粉: retain that discrepancy rather
    # than silently rewriting ingredients or treating it as a vegetable dish.
    fried_rice_balls = (
        sweet
        and not savory
        and ("糯米粉" in foods or ("糯米" in foods and "糯米粉" in text))
        and "面团" in text
        and "搓圆" in text
        and "芝麻" in foods
        and "炸" in text
        and any(term in text for term in ("芝麻球", "裹芝麻", "裹上白芝麻"))
    )
    root = any(term in foods for term in ("南瓜", "山药"))
    grain = any(term in foods for term in (*_RAW_GRAIN, "面粉", "米粉", "粘米粉", "燕麦"))
    syrup_dressed_root = (
        root
        and sweet
        and not savory
        and not grain
        and "糖浆" in text
        and "淋" in text
        and "蒸" in text
    )
    sweet_root_puree = (
        root
        and sweet
        and "红枣" in foods
        and not savory
        and not grain
        and "搅打" in text
        and "蒸制" in text
    )
    floral_honey_glaze = (
        root
        and "蜂蜜" in foods
        and "桂花酱" in foods
        and not savory
        and not grain
        and "蜂蜜" in text
        and "桂花酱" in text
        and "混合" in text
        and "刷" in text
    )
    sugar_dipped_root = (
        root
        and sweet
        and not savory
        and not grain
        and "蒸" in text
        and any("蘸" + sugar in text for sugar in _SUGAR)
    )
    for positive, reason in (
        (cookie, "黄油糖粉面粉小面团按扁烘烤的饼干式准备"),
        (_thumb_shaped_cookie(foods, text), "甜黄油面团分小份搓圆、指压裂开后实际烘烤的饼干式成品"),
        (
            _finished_piped_sweet_batter(foods, text),
            "黄油糖粉面粉面糊裱花挤条后烤至金黄或酥脆的甜薄脆式成品",
        ),
        (yogurt_assembly, "饼干碎与酸奶的甜点式组装"),
        (gel_blocks, "加糖凝胶冷却切块的甜点式准备"),
        (fruit_broth, "加糖水果银耳水煮的甜羹式准备"),
        (piped_root, "蜂蜜果酱山药泥裱花装饰的甜点式准备"),
        (sweet_filled_balls, "糯米粉包甜豆沙馅制圆的甜点式准备"),
        (fried_rice_balls, "加糖糯米粉揉圆裹芝麻油炸的甜点式准备"),
        (syrup_dressed_root, "蒸根茎淋糖浆的甜点式准备"),
        (sweet_root_puree, "根茎加糖搅打后配红枣蒸制的甜点式准备"),
        (floral_honey_glaze, "根茎刷蜂蜜桂花酱的甜点式组装"),
        (sugar_dipped_root, "蒸根茎直接蘸糖的甜点式组装"),
    ):
        if positive:
            findings.append(reason)
    return findings


def meat_floss_component(ingredient_names: Iterable[str], steps: str) -> bool:
    """Recognize an explicit meat-floss processing program, not a whole entrée."""
    foods = "、".join(ingredient_names)
    text = "".join(steps.split())
    return (
        any(term in foods for term in ("猪肉", "猪里脊肉", "鸡肉", "牛肉"))
        and "撕成细条" in text
        and "肉松" in text
        and any(term in text for term in ("智能程序", "肉松程序"))
        and not any(term in foods for term in ("面粉", "大米", "面条"))
    )


def grain_completion_issue(name: str, ingredient_names: Iterable[str], steps: str) -> str | None:
    """Detect a limited porridge record that only finishes unnamed raw grain.

    This is not a universal cooking-time or food-safety test. A lone mixing/
    serving step with no cooking instruction and no
    declared cooked-grain input cannot establish how that grain was prepared.
    Explicit cooked input, actual cooking or equipment-start evidence preserves
    recall. Clinical readiness and real appliance execution remain unverified.
    """
    foods = list(ingredient_names)
    text = "".join(steps.split())
    raw = [term for term in _RAW_GRAIN if term in foods]
    if not name.strip().endswith("粥") or not raw:
        return None
    if any(action in text for action in ("熬", "煮", "蒸", "炖", "焖", "开始烹饪", "开启烹饪")):
        return None
    # Source appliance instructions can omit the word "cook". Actual grain
    # loading plus an execution setting establishes a preparation path, not
    # a certified duration or physical safety guarantee. A sugar-only finishing
    # setting still cannot stand in for omitted grain preparation.
    if (
        all(term in text for term in raw)
        and "锅" in text
        and "设置" in text
        and any(action in text for action in ("加入", "放入"))
    ):
        return None
    if any(action in text for action in ("混合", "搅拌", "取出", "食用")):
        return "源粥记录仅有末段混合/取出，未交代所列生豆谷的烹饪过程或熟料来源。"
    return None
