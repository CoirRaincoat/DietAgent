"""Privacy-preserving local acceptance of every supplied profile and dialogue.

The LLM is a finite, annotated Intent queue. No provider, key or HTTP client is
created. This evaluates downstream business behaviour, not language-model NLU.
Independent source-row and allergen oracles intentionally do not call the
production RuleEngine. The oracle is bounded, not a clinical safety certificate.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from app.agent.service import MealAgent
from app.infrastructure.data import PROJECT_ROOT, RECIPE_PATH, load_catalog
from app.infrastructure.sessions import SessionStore
from evaluation.offline import FixtureLLM
from evaluation.offline import replay as replay_dialogues

PASS = "PASS"
FAIL = "FAIL"
INSUFFICIENT = "数据不足"
SUPPORTED_GOALS = {"控糖", "增肌", "减脂", "降尿酸", "降压"}

# Independent acceptance vocabulary, versioned in this file. It covers the 12
# actual source allergy values and obvious derivatives. It is not imported from
# production aliases/config and cannot establish absence of cross-contact.
FISH = "鱼 鲫 鲤 鲈 鳕 鳝 鳗 鳟 鲑 鲷 鲳 沙丁 银鳕 木鱼花 柴鱼片 东星斑 石斑 多宝 龙利 巴沙 鲢 鳙 鲶 鲭 鲐 鲱 鲽 鲆 鲮 鲣 鲔 鲟 鳜".split()
CRUSTACEANS = "虾 蟹 海米".split()
MOLLUSCS = "贝 蛤 蚝 蛎 蚬 螺 蛏 蚶 鲍鱼 鱿鱼 墨鱼 章鱼 瑶柱 花甲 青口".split()
SOY = "大豆 黄豆 毛豆 豆腐 豆干 豆皮 豆浆 豆乳 腐竹 百叶 千张 豆芽 豆酱 豆瓣 豆豉 酱油 生抽 老抽 味极鲜 豆油 豆花 素鸡 素鸭 香干 腐乳 豆奶 豆渣 豆蛋白".split()
NUTS = "花生 落花生 核桃 杏仁 腰果 榛子 松子 开心果 碧根果 夏威夷果 巴旦木 榧子 坚果 松仁 胡桃 南杏 北杏".split()
ORACLE_ALLERGENS = {
    "海鲜": FISH + CRUSTACEANS + MOLLUSCS,
    "花生": ["花生", "落花生"],
    "牛奶": "牛奶 牛乳 鲜奶 奶粉 酸奶 酸乳 奶酪 乳酪 芝士 黄油 奶油 炼乳 炼奶 乳清 酪蛋白 乳粉".split(),
    "鸡蛋": "鸡蛋 鸭蛋 鹌鹑蛋 鹅蛋 蛋液 蛋白 蛋清 蛋黄 蛋粉 皮蛋 松花蛋 咸蛋 荷包蛋".split(),
    "芒果": ["芒果"],
    "虾": CRUSTACEANS,
    "豆制品": SOY,
    "坚果": NUTS,
    "螃蟹": CRUSTACEANS,
    "豆类": SOY + ["豌豆", "蚕豆", "绿豆", "红豆", "鹰嘴豆"],
    "蟹": CRUSTACEANS,
    "啤酒": ["啤酒"],
}
IGNORED_PHRASES = {
    "海鲜": ["鱼香", "鱼眼泡", "鱼腥草", "螺丝椒", "海螺面"],
    "牛奶": ["椰奶", "杏仁奶", "燕麦奶", "豆奶"],
    "鸡蛋": ["蛋白质", "蛋白酶", "植物蛋白"],
}
UNKNOWN_COMPOSITES = "调味料 调料包 酱料包 火锅底料 咖喱块 浓汤宝 复合调味料 沙拉酱 沙茶酱 蛋白粉 不详 未知".split()
SPICY = "辣椒 辣油 辣酱 小米椒 朝天椒 剁椒 泡椒 干红椒 红油 豆瓣酱 哈瓦那椒 辣粉 青尖椒 尖椒 杭椒 美人椒 二荆条 泡红椒 线椒".split()


def _source_rows() -> dict[int, dict[str, str]]:
    with (PROJECT_ROOT / RECIPE_PATH).open(encoding="gb18030", newline="") as stream:
        return dict(enumerate(csv.DictReader(stream), start=2))


def check_source(recipe, item, rows: dict[int, dict[str, str]]) -> bool:
    """Compare the response with original CSV bytes, not only the catalog map."""
    row = rows.get(recipe.source_row)
    if row is None:
        return False
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return bool(
        recipe.fingerprint == fingerprint
        and recipe.recipe_id.startswith("recipe_" + fingerprint[:24])
        and item.recipe_id == recipe.recipe_id
        and item.name == row["名称"].strip()
        and item.steps == row["烹饪步骤"]
        and recipe.raw_ingredients == row["食材清单"]
        and item.ingredients == [ingredient.name for ingredient in recipe.ingredients]
        and all(ingredient.raw in row["食材清单"] for ingredient in recipe.ingredients)
    )


def check_allergens(text: str, allergies: list[str], no_spicy: bool = False) -> dict:
    """Return only check codes; do not export an individual's allergy names."""
    failures = []
    unknown = 0
    text = re.sub(r"\s+", "", text)
    for allergy in allergies:
        terms = ORACLE_ALLERGENS.get(allergy)
        if terms is None:
            unknown += 1
            continue
        haystack = text
        for phrase in IGNORED_PHRASES.get(allergy, []):
            haystack = haystack.replace(phrase, "")
        if any(term in haystack for term in terms):
            failures.append("known_allergen_in_source")
    if allergies and any(term in text for term in UNKNOWN_COMPOSITES):
        failures.append("unknown_composite_with_allergy")
    if no_spicy and any(term in text for term in SPICY):
        failures.append("spicy_ingredient_with_no_spicy_constraint")
    return {
        "status": FAIL if failures else INSUFFICIENT if unknown else PASS,
        "failures": sorted(set(failures)), "unknown_allergy_values": unknown,
    }


def generated_cases(profiles) -> list[dict]:
    """Deterministic synthetic requests applied to *real* local profiles."""
    return [
        {
            "user_id": user_id,
            "turns": [
                {
                    "message": "请安排1人晚餐，3道菜，不要辣椒，沿用档案中的食材限制。",
                    "intent": {"people": 1, "meal_type": "晚餐", "dish_count": 3,
                               "excluded_ingredients": ["辣椒"]},
                },
                {"message": "不要辣，把第二道菜换一道，其他菜保持。",
                 "intent": {"action": "replace", "replace_slot": 2, "no_spicy": True}},
                {"message": "解释这一餐的选择依据，保留现有菜单。",
                 "intent": {"action": "explain"}},
            ],
        }
        for user_id in sorted(profiles)
    ]


def _status(failures: list[str], observations: int) -> str:
    return FAIL if failures else PASS if observations else INSUFFICIENT


async def _profile_case(case: dict, catalog, rows: dict, directory: Path) -> dict:
    user_id = case["user_id"]
    profile = catalog.profiles[user_id]
    store = SessionStore(directory / f"profile-{user_id}.sqlite3")
    llm = FixtureLLM([turn["intent"] for turn in case["turns"]])
    agent = MealAgent(catalog, store, llm)
    failures: dict[str, list[str]] = {key: [] for key in ("allergy", "health", "source", "state")}
    session_id = None
    previous_ids: list[str] = []
    previous_response = None
    turns = []
    source_checks = 0
    menu_observations = 0
    for number, turn in enumerate(case["turns"], start=1):
        request_id = f"real-profile-{user_id}-{number}"
        try:
            response = await agent.chat(
                user_id, turn["message"], session_id=session_id, request_id=request_id,
            )
        except Exception as error:
            failures["state"].append(f"turn_{number}_execution_{type(error).__name__}")
            break
        state = response.conversation_state
        session_id = state.session_id
        stored = store.get(session_id, user_id)
        if stored is None or stored.model_dump() != state.model_dump() or state.revision != number:
            failures["state"].append(f"turn_{number}_state_persistence")
        # Replaying the same completed request must not consume another fixture.
        cached = await agent.chat(
            user_id, turn["message"], session_id=session_id, request_id=request_id,
        )
        if cached.model_dump() != response.model_dump():
            failures["state"].append(f"turn_{number}_idempotency")
        if not set(profile.allergies).issubset(state.constraints.allergies):
            failures["allergy"].append(f"turn_{number}_lost_profile_allergy")
        if not set(profile.health_goals).issubset(state.constraints.health_goals):
            failures["health"].append(f"turn_{number}_lost_health_goal")
        if state.constraints.people != 1 or state.constraints.meal_type != "晚餐":
            failures["state"].append(f"turn_{number}_lost_meal_context")
        if "辣椒" not in state.constraints.excluded_ingredients:
            failures["state"].append(f"turn_{number}_lost_exclusion")
        if number > 1 and not state.constraints.no_spicy:
            failures["state"].append(f"turn_{number}_lost_no_spicy")
        ids = [item.recipe_id for item in response.menu]
        if response.status == "ok":
            menu_observations += 1
            if not state.menu_valid or ids != state.menu_ids or len(ids) != 3 or len(set(ids)) != 3:
                failures["state"].append(f"turn_{number}_menu_structure")
            if number == 2 and previous_response and previous_response.status == "ok":
                # A newly added no-spicy rule may invalidate other old slots;
                # preserve only slots that independently pass the new rule.
                for index in (0, 2):
                    old = catalog.recipes[previous_ids[index]]
                    text = old.raw_ingredients + old.steps
                    if check_allergens(text, profile.allergies, True)["status"] == PASS:
                        if ids[index] != previous_ids[index]:
                            failures["state"].append("replace_changed_unaffected_slot")
                if ids[1] == previous_ids[1]:
                    failures["state"].append("replace_did_not_change_target")
            if number == 3 and ids != previous_ids:
                failures["state"].append("explanation_changed_menu")
            if number == 3 and any(event.name in {"plan_menu", "menu_modify"} for event in response.tool_calls):
                failures["state"].append("explanation_replanned_menu")
            for item in [*response.menu, *response.replacement_suggestions]:
                recipe = catalog.recipes.get(item.recipe_id)
                source_checks += 1
                if recipe is None or not check_source(recipe, item, rows):
                    failures["source"].append(f"turn_{number}_source_mismatch")
                    continue
                source_text = rows[recipe.source_row]["食材清单"] + rows[recipe.source_row]["烹饪步骤"]
                oracle = check_allergens(source_text, profile.allergies, state.constraints.no_spicy)
                failures["allergy"].extend(oracle["failures"])
                if "辣椒" in source_text:
                    failures["allergy"].append("explicit_exclusion_in_source")
            narrative = "\n".join([
                response.reason, *response.warnings,
                *(note for item in response.menu for note in item.nutrition_notes),
            ])
            for goal in profile.health_goals:
                if goal not in narrative:
                    failures["health"].append("goal_missing_from_explanation_or_limitation")
            if re.search(r"\d+(?:\.\d+)?\s*(?:千卡|千焦|kcal|毫克|克蛋白质)", narrative, re.I):
                failures["health"].append("unsupported_precise_nutrition_claim")
        elif response.menu or state.menu_valid:
            failures["state"].append(f"turn_{number}_unresolved_exposed_menu")
        turns.append({"turn": number, "status": response.status, "menu_count": len(response.menu),
                      "revision": state.revision, "tool_names": [e.name for e in response.tool_calls]})
        previous_ids, previous_response = ids, response
    if llm.intents or len(turns) != len(case["turns"]):
        failures["state"].append("not_all_turns_consumed")
    known_allergies = all(value in ORACLE_ALLERGENS for value in profile.allergies)
    return {
        "case_id": f"profile-{user_id}", "user_id": user_id, "kind": "generated_profile",
        "turns": turns, "source_checks": source_checks,
        "checks": {
            "allergy": _status(failures["allergy"], menu_observations if known_allergies else 0),
            "health_logic": _status(failures["health"], menu_observations),
            "health_outcome": FAIL if failures["health"] else INSUFFICIENT,
            "source": _status(failures["source"], source_checks),
            "state": _status(failures["state"], len(turns)),
        },
        "failures": {key: sorted(set(value)) for key, value in failures.items() if value},
        "data_gaps": ["per_person_nutrition_and_health_outcome", "cross_contact_and_brand_formula"]
        + (["unsupported_health_goals"] if set(profile.health_goals) - SUPPORTED_GOALS else [])
        + (["special_population_suitability"] if profile.special_groups else []),
    }


def _dialogue_results(report: dict, catalog, rows: dict) -> list[dict]:
    results = []
    for case in report["cases"]:
        allergy_failures = []
        source_failures = []
        source_checks = 0
        observations = 0
        for turn in case["turns"]:
            for identity in turn["menu_source_identity"]:
                observations += 1
                recipe = catalog.recipes.get(identity["recipe_id"])
                source_checks += 1
                if recipe is None:
                    source_failures.append("missing_catalog_recipe")
                    continue
                row = rows[recipe.source_row]
                fingerprint = hashlib.sha256(json.dumps(
                    row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")).hexdigest()
                if not identity["passed"] or fingerprint != identity["fingerprint"]:
                    source_failures.append("source_fingerprint_mismatch")
                oracle = check_allergens(
                    row["食材清单"] + row["烹饪步骤"], turn["constraints"]["allergies"],
                    turn["constraints"]["no_spicy"],
                )
                allergy_failures.extend(oracle["failures"])
        failures = {
            "allergy": sorted(set(allergy_failures)), "source": sorted(set(source_failures)),
            "state": case["assertion_failures"],
        }
        results.append({
            "case_id": f"dialogue-{case['case_id']}", "kind": "original_dialogue",
            "user_id": report["source_identity"]["user_id"],
            "turns": [{"turn": turn["turn"], "status": turn["status"],
                       "revision": turn["revision"]} for turn in case["turns"]],
            "source_checks": source_checks,
            "checks": {
                "allergy": _status(allergy_failures, observations),
                "health_logic": INSUFFICIENT,
                "health_outcome": INSUFFICIENT,
                "source": _status(source_failures, source_checks),
                "state": _status(case["assertion_failures"], len(case["turns"])),
            },
            "failures": {key: value for key, value in failures.items() if value},
            "data_gaps": ["dialogue_has_no_profile_binding", "human_intent_annotation_not_nlu",
                          "per_person_nutrition_and_health_outcome"],
        })
    return results


async def run_acceptance() -> dict:
    started = perf_counter()
    catalog = load_catalog()
    rows = _source_rows()
    with TemporaryDirectory(prefix="diet-real-acceptance-") as directory:
        generated = [await _profile_case(case, catalog, rows, Path(directory))
                     for case in generated_cases(catalog.profiles)]
    dialogue_report = await replay_dialogues(prepare_meal=False)
    original = _dialogue_results(dialogue_report, catalog, rows)
    results = generated + original
    dimensions = ["allergy", "health_logic", "health_outcome", "source", "state"]
    return {
        "schema_version": "real-local-acceptance.v1",
        "scope": "Local original profiles and dialogues; finite human Intent fixtures; no external model.",
        "sources": catalog.quality_report["sources"],
        "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "annotations_sha256": hashlib.sha256(
            (PROJECT_ROOT / "evaluation/offline.py").read_bytes()
        ).hexdigest(),
        "summary": {
            "profiles": len(generated), "generated_turns": sum(len(c["turns"]) for c in generated),
            "original_dialogues": len(original),
            "original_turns": sum(len(c["turns"]) for c in original),
            "original_dialogue_user_id": dialogue_report["source_identity"]["user_id"],
            "allergy_values_covered": len({x for p in catalog.profiles.values() for x in p.allergies}),
            "health_goal_values": len({x for p in catalog.profiles.values() for x in p.health_goals}),
            "supported_health_goal_values": len(SUPPORTED_GOALS),
            "cases_with_failures": sum(bool(c["failures"]) for c in results),
            "source_checks": sum(c["source_checks"] for c in results),
            "statuses": dict(Counter(t["status"] for c in results for t in c["turns"])),
            "dimensions": {key: {status: sum(c["checks"][key] == status for c in results)
                                 for status in (PASS, FAIL, INSUFFICIENT)} for key in dimensions},
            "local_seconds": round(perf_counter() - started, 3),
        },
        "cases": results,
    }


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    labels = {"allergy": "过敏与明确忌口文字核验", "health_logic": "健康需求传递及说明边界",
              "health_outcome": "定量营养与健康效果", "source": "菜谱来源真实性", "state": "多轮状态一致性"}
    lines = [
        "# 真实数据验收报告", "", "题目：ZX-2026-0301｜方太个性化膳食规划 Agent", "",
        "本报告由 `python -m evaluation.real_data` 自动生成。真实档案和原始对话仅在本地处理；"
        "未创建外部模型客户端，未读取或发送 API 密钥。", "",
        "## 验收方法与边界", "",
        f"- 覆盖全部 {summary['profiles']} 个真实档案，自动生成推荐、指定换菜、保持菜单解释三轮案例，"
        f"共 {summary['generated_turns']} 轮；请求文本为固定测试文本，健康资料来自真实本地档案。",
        f"- 回放原始 {summary['original_dialogues']} 组、{summary['original_turns']} 轮对话。"
        f"原始对话没有绑定档案，固定使用用户 ID {summary['original_dialogue_user_id']}；对话 ID 不是用户 ID。",
        "- 原始对话回放关闭预置餐次上下文（prepare_meal=False），缺人数或餐次时由 Agent 主动澄清。"
        "未产生菜单的案例，其过敏与来源核验记为数据不足，不用默认值跳过澄清。",
        "- 意图输入来自人工标注 fixture；实际运行 Agent、检索、规则、规划和 SQLite。"
        "这不是 DeepSeek 自然语言理解、官方比赛准确率或临床效果验收。",
        "- 过敏检查使用验收脚本中的独立词表扫描原始食材和步骤；来源检查重新读取原始 CSV 并重算 SHA-256。"
        "不使用生产 RuleEngine.evaluate 的结论作为验收通过依据。",
        "- 状态检查包括约束继承、逐轮版本、持久化、完成请求幂等、换菜保留仍有效的位置、解释不重规划。",
        "- PASS 表示已执行且满足所列工程断言；FAIL 表示断言失败；数据不足表示没有菜单可查、"
        "缺少验证资料，或能力范围不足。澄清/无可行菜单不自动等于 FAIL。", "",
        "## 汇总", "",
        f"- 发生断言失败的案例：**{summary['cases_with_failures']}**。",
        f"- 原始菜谱身份检查次数（含生成案例的替换建议）：**{summary['source_checks']}**。",
        f"- 覆盖原始过敏值 {summary['allergy_values_covered']} 类；原始健康目标 {summary['health_goal_values']} 类，"
        f"其中 {summary['supported_health_goal_values']} 类有当前定性排序规则。",
        f"- 运行状态：`{json.dumps(summary['statuses'], ensure_ascii=False)}`。", "",
        "| 检查维度 | PASS | FAIL | 数据不足 |", "|---|---:|---:|---:|",
    ]
    for key, name in labels.items():
        counts = summary["dimensions"][key]
        lines.append(f"| {name} | {counts[PASS]} | {counts[FAIL]} | {counts[INSUFFICIENT]} |")
    lines.extend([
        "", "健康需求传递 PASS 仅表示目标未丢失、已支持目标有定性说明、其他目标有能力边界提示。"
        "健康目标属于排序偏好，并不是必须达到的定量阈值。全部案例缺少可靠份量、营养成分、"
        "个体响应和效果观测，因此不能判定“低糖/低钠/降尿酸”等健康效果通过。",
        "", "过敏 PASS 仅覆盖已知词表的菜谱文字；不能验证品牌配方、漏写配料或交叉接触。"
        "档案特殊人群字段不能单独证明孕期、哺乳期或慢病个体适用性，这些适用性仍为数据不足。",
        "", "## 逐案例结果", "",
        "表内只保留用户 ID 与工程结果，不列出个人健康目标、过敏详情、体检或对话文本。",
        "", "| 案例 | 用户 ID | 轮数 | 过敏/忌口 | 健康逻辑 | 健康效果 | 菜谱真实 | 状态 |",
        "|---|---:|---:|---|---|---|---|---|",
    ])
    for case in report["cases"]:
        checks = case["checks"]
        values = " | ".join(checks[key] for key in labels)
        lines.append(f"| {case['case_id']} | {case['user_id']} | {len(case['turns'])} | {values} |")
    failures = [case for case in report["cases"] if case["failures"]]
    lines.extend(["", "## 失败记录", ""])
    if failures:
        for case in failures:
            lines.append(f"- `{case['case_id']}`：`{json.dumps(case['failures'], ensure_ascii=False)}`")
    else:
        lines.append("未发现本报告覆盖的工程断言失败；这不等同于不存在未覆盖风险。")
    lines.extend(["", "## 数据版本与复现", "", "| 输入 | SHA-256 |", "|---|---|"])
    for source in report["sources"].values():
        lines.append(f"| `{source['path']}` | `{source['sha256']}` |")
    lines.extend([
        f"| 验收脚本 | `{report['evaluator_sha256']}` |",
        f"| 原始对话人工标注脚本 | `{report['annotations_sha256']}` |", "",
        "```powershell", ".\\.venv\\Scripts\\python.exe -m evaluation.real_data", "```", "",
        "默认汇总 JSON 写入被 Git 忽略的 `artifacts/real_data_report.json`，Markdown 写入本文件。"
        "生成案例按用户 ID 排序；当前源码与输入 SHA 相同可复现同一组工程结果。"
        "用时不作为验收阈值，离线 fixture 用时不代表真实模型服务性能。", "",
        "下一步应扩展独立人工标注的食材/过敏别名审查、特殊人群规则与份量营养数据。"
        "外部模型联调继续仅使用合成画像及自造对话。", "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/real_data_report.json")
    parser.add_argument("--report", type=Path, default=PROJECT_ROOT / "evaluation/REAL_DATA_REPORT.md")
    args = parser.parse_args()
    report = asyncio.run(run_acceptance())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["cases_with_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
