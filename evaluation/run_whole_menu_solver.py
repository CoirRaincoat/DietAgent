"""Replay frozen development seeds locally and emit a human-review bundle.

CLI input schemas are the earlier health-pair/source/API snapshot artifacts.
This is not a new independent holdout, live NLU or an external model review.
"""

import argparse
import csv
import hashlib
import html
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from app.agent.menu_balance import analyze_menu_balance
from app.domain.method_preferences import method_spread
from app.domain.models import Constraints, Recipe
from app.infrastructure.data import normalize_recipes
from app.retrieval.keyword import KeywordRetriever
from app.rules.engine import RuleEngine
from evaluation.method_guard_policy import MethodGuardPolicy
from evaluation.preparation_review import preparation_review
from evaluation.source_culinary_identity import (
    FoodIdentityPolicy,
    identity_culinary_focus,
    identity_food_families,
)
from evaluation.whole_menu_guard import (
    peer_health_frontier,
    prepare_menu_problem,
    validate_menu_proposal,
)
from evaluation.whole_menu_solver import diagnose_no_improvement, solve_whole_menu


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def identity_observation(recipe: Recipe, policy: FoodIdentityPolicy) -> dict[str, object]:
    focus = identity_culinary_focus(recipe, policy)
    return {
        "focus": sorted(focus.families),
        "focus_reason": focus.reason,
        "declared": sorted(identity_food_families(recipe, policy)),
    }


def observe(menu: list[Recipe], constraints: Constraints, rules: RuleEngine) -> dict[str, Any]:
    return {
        "menu": [recipe.model_dump(mode="json") for recipe in menu],
        "method_spread": method_spread(menu),
        "balance_NOT_quality_score": asdict(analyze_menu_balance(menu, constraints)),
        "health_evidence": {
            recipe.recipe_id: {
                goal: (
                    asdict(evidence)
                    if (evidence := rules.goal_evidence(recipe, goal)) is not None
                    else None
                )
                for goal in constraints.health_goals
            }
            for recipe in menu
        },
        "preparation_manual_review_NOT_rank_or_certification": {
            recipe.recipe_id: asdict(preparation_review(recipe)) for recipe in menu
        },
    }


def run(args: argparse.Namespace) -> Path:
    source = Path(args.source).resolve()
    direct_path, api_path, conditions_path = (
        Path(value).resolve()
        for value in (args.source_snapshot, args.api_snapshot, args.conditions)
    )
    output = Path(args.output).resolve()
    if output.exists():
        raise FileExistsError("review directory already exists; use a fresh output path")
    direct, api, conditions = (read_json(path) for path in (direct_path, api_path, conditions_path))
    with source.open(encoding="gb18030", newline="") as handle:
        live = normalize_recipes(csv.DictReader(handle))
    if [recipe.model_dump(mode="json") for recipe in live.values()] != direct["catalog"]:
        raise ValueError("live normalized source differs from the frozen direct catalog")
    if {key: recipe.model_dump(mode="json") for key, recipe in live.items()} != api[
        "source_catalog"
    ]:
        raise ValueError("live normalized source differs from the frozen API catalog")
    if (
        not digest(source)
        == direct["source_sha256"]
        == api["source_sha256"]
        == conditions["source_sha256"]
    ):
        raise ValueError("source SHA differs from frozen comparison inputs")
    public = {key: Recipe.model_validate(value) for key, value in api["public_catalog"].items()}
    rules, rows, calls, diagnostics = (
        RuleEngine(experiment_category_scope=args.health_category_scope),
        [],
        0,
        0,
    )
    for condition in conditions["cases"]:
        scope = condition["scope"]
        if scope == "source2000_LOCAL_ONLY":
            catalog = live
        elif scope == "PUBLIC_AUTHORED_ONLY":
            catalog = public
        else:
            raise ValueError("unknown fixture scope; private/official uploads are not supported")
        constraints = Constraints.model_validate(condition["constraints"])
        seed = [catalog[key] for key in condition["menu_ids"]]
        if [recipe.model_dump(mode="json") for recipe in seed] != condition["seed"]["menu"]:
            raise ValueError("seed source differs from frozen pair report")
        candidates = KeywordRetriever(catalog.values()).search(
            condition["query_terms"], constraints
        )
        modes: list[tuple[str, dict[str, Any]]] = [
            ("fixed_baseline_feasibility", {"verify_baseline": True}),
            ("full_authorized_meal", {}),
        ]
        strict_problem, _ = prepare_menu_problem(
            seed, candidates, constraints, rules, query_terms=condition["query_terms"]
        )
        identity_peer_changes = []
        identity_frontier = None
        if args.food_identity_ablation:
            shared_problem, _ = prepare_menu_problem(
                seed,
                candidates,
                constraints,
                rules,
                query_terms=condition["query_terms"],
                food_identity_policy="shared_source_v1",
            )
            if shared_problem is not None and strict_problem is not None:
                for index, (old_peers, new_peers) in enumerate(
                    zip(strict_problem.peers, shared_problem.peers), 1
                ):
                    changed_ids = sorted(set(old_peers) ^ set(new_peers))
                    identity_peer_changes.append(
                        {
                            "slot": index,
                            "seed_name": seed[index - 1].name,
                            "legacy_count": len(old_peers),
                            "shared_count": len(new_peers),
                            "added_ids": sorted(set(new_peers) - set(old_peers)),
                            "removed_ids": sorted(set(old_peers) - set(new_peers)),
                            "records_NOT_recommended_or_quality_validated": [
                                {
                                    "recipe": shared_problem.records[key].model_dump(mode="json"),
                                    "legacy_identity": identity_observation(
                                        shared_problem.records[key], "legacy"
                                    ),
                                    "shared_identity": identity_observation(
                                        shared_problem.records[key], "shared_source_v1"
                                    ),
                                }
                                for key in changed_ids
                            ],
                        }
                    )
                if constraints.health_goals:
                    identity_frontier = peer_health_frontier(shared_problem)
        if constraints.health_goals:
            modes.extend(
                [
                    ("one_changed_slot", {"max_changed_slots": 1}),
                    ("two_changed_slots", {"max_changed_slots": 2}),
                    ("local_first_slot_only", {"replace_slot": 1}),
                    ("full_identical_input_replay", {}),
                ]
            )
            if args.method_policy_ablation:
                modes.extend(
                    [
                        (
                            "capped_fixed_baseline_feasibility",
                            {"verify_baseline": True, "method_guard_policy": "capped_balance"},
                        ),
                        ("capped_full_authorized_meal", {"method_guard_policy": "capped_balance"}),
                        (
                            "capped_one_changed_slot",
                            {"max_changed_slots": 1, "method_guard_policy": "capped_balance"},
                        ),
                        (
                            "capped_two_changed_slots",
                            {"max_changed_slots": 2, "method_guard_policy": "capped_balance"},
                        ),
                        (
                            "capped_local_first_slot_only",
                            {"replace_slot": 1, "method_guard_policy": "capped_balance"},
                        ),
                        (
                            "capped_full_identical_input_replay",
                            {"method_guard_policy": "capped_balance"},
                        ),
                    ]
                )
        if args.food_identity_ablation:
            identity_modes: list[tuple[str, dict[str, Any]]] = [
                ("shared_identity_fixed_baseline", {"verify_baseline": True}),
                ("shared_identity_full_meal", {}),
            ]
            if constraints.health_goals:
                identity_modes.extend(
                    [
                        ("shared_identity_one_slot", {"max_changed_slots": 1}),
                        ("shared_identity_two_slots", {"max_changed_slots": 2}),
                        ("shared_identity_local_first_slot", {"replace_slot": 1}),
                        ("shared_identity_replay", {}),
                        (
                            "shared_identity_capped_full_meal",
                            {"method_guard_policy": "capped_balance"},
                        ),
                        (
                            "shared_identity_capped_replay",
                            {"method_guard_policy": "capped_balance"},
                        ),
                    ]
                )
            modes.extend(
                (name, {**options, "food_identity_policy": "shared_source_v1"})
                for name, options in identity_modes
            )
        results = []
        for name, options in modes:
            result = solve_whole_menu(
                seed,
                candidates,
                constraints,
                rules,
                query_terms=condition["query_terms"],
                time_limit_seconds=args.seconds,
                **options,
            )
            calls += 1
            results.append(
                {
                    "mode": name,
                    **asdict(result),
                    "recipes": [recipe.model_dump(mode="json") for recipe in result.recipes],
                    "observation": observe(result.recipes, constraints, rules),
                    "strict_baseline_gate_failures_NOT_new_safety_failures": (
                        validate_menu_proposal(strict_problem, result.recipes)
                        if strict_problem is not None
                        else None
                    ),
                }
            )
            print(
                condition["case_id"],
                name,
                result.status,
                result.solver_status,
                result.changed_slots,
                round(result.elapsed_seconds, 3),
                flush=True,
            )
        explanation = None
        frontier = None
        capped_explanation = None
        identity_explanations = {}
        full_result = next(result for result in results if result["mode"] == "full_authorized_meal")
        if full_result["status"] == "no_improvement_in_guarded_model_kept_seed":
            explanation = diagnose_no_improvement(
                seed,
                candidates,
                constraints,
                rules,
                query_terms=condition["query_terms"],
                time_limit_seconds=args.seconds,
            )
            diagnostics += 1
            print(condition["case_id"], "assumptions_NOT_minimal", explanation, flush=True)
            problem, _ = prepare_menu_problem(
                seed, candidates, constraints, rules, query_terms=condition["query_terms"]
            )
            if problem is not None:
                frontier = peer_health_frontier(problem)
        if args.method_policy_ablation and constraints.health_goals:
            capped_result = next(
                result for result in results if result["mode"] == "capped_full_authorized_meal"
            )
            if capped_result["status"] == "no_improvement_in_guarded_model_kept_seed":
                capped_explanation = diagnose_no_improvement(
                    seed,
                    candidates,
                    constraints,
                    rules,
                    query_terms=condition["query_terms"],
                    time_limit_seconds=args.seconds,
                    method_guard_policy="capped_balance",
                )
                diagnostics += 1
                print(
                    condition["case_id"],
                    "capped_assumptions_NOT_minimal",
                    capped_explanation,
                    flush=True,
                )
        if args.food_identity_ablation and constraints.health_goals:
            for mode, method_policy in (
                ("shared_identity_full_meal", "strict_baseline"),
                ("shared_identity_capped_full_meal", "capped_balance"),
            ):
                if next(r for r in results if r["mode"] == mode)["status"] == (
                    "no_improvement_in_guarded_model_kept_seed"
                ):
                    identity_explanations[mode] = diagnose_no_improvement(
                        seed,
                        candidates,
                        constraints,
                        rules,
                        query_terms=condition["query_terms"],
                        time_limit_seconds=args.seconds,
                        method_guard_policy=cast(MethodGuardPolicy, method_policy),
                        food_identity_policy="shared_source_v1",
                    )
                    diagnostics += 1
        rows.append(
            {
                "case_id": condition["case_id"],
                "message": condition["message"],
                "constraints": condition["constraints"],
                "query_terms": condition["query_terms"],
                "scope": scope,
                "retrieved_candidate_count": len(candidates),
                "seed": observe(seed, constraints, rules),
                "results": results,
                "prior_pair_modes": condition["modes"],
                "feasibility_explanation_NO_relaxation": explanation,
                "peer_frontier_NOT_feasible_menu_or_quality": frontier,
                "capped_feasibility_explanation_NO_relaxation": capped_explanation,
                "shared_identity_explanations_NO_relaxation": identity_explanations,
                "identity_peer_changes_NOT_menu_quality": identity_peer_changes,
                "shared_identity_frontier_NOT_feasible_menu": identity_frontier,
            }
        )
    counts = Counter(result["status"] for row in rows for result in row["results"])
    report = {
        "scope": "LOCAL_REUSED_DEVELOPMENT_NOT_INDEPENDENT_QUALITY",
        "solver_calls": calls,
        "separate_feasibility_diagnostic_calls": diagnostics,
        "cases": rows,
        "status_counts": dict(counts),
        "source_sha256": digest(source),
        "method_policy_ablation": args.method_policy_ablation,
        "food_identity_ablation": args.food_identity_ablation,
        "health_category_scope": args.health_category_scope,
        "complete_normalized_source_equal": True,
        "seconds_per_call_including_build_NOT_TTFT": args.seconds,
        "new_HTTP_calls": 0,
        "external_model_calls": 0,
        "human_labels_completed": 0,
        "serving_policy_changed": False,
        "official_data_used": False,
        "warning": "INFEASIBLE only concerns strict improvement in this conservative guarded model; source health references are not nutrition/medical benefit or human quality.",
    }
    output.mkdir(parents=True)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (output / "human_labels.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "case_id": row["case_id"],
                    "preference": None,
                    "requirement_omissions": None,
                    "practicality": None,
                    "source_caution_review": None,
                    "reviewer": None,
                    "comment": None,
                },
                ensure_ascii=False,
            )
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    lines = [
        "# 整餐约束求解：人工复核入口",
        "",
        "仅本地：含原2000菜谱，不外发整份报告。",
        "复用开发条件，不是独立留出／官方20。所有真人意见为空；目标点数非质量／营养分。",
        "shared_identity仅显式离线声明与主体实验；旧食材表示、shared_v2历史观察和服务默认不变。表示覆盖增加不是质量改善。旧严格门禁差异也可能来自旧表示缺项，不能隐瞒。",
        f"本报告类别参考作用范围实验：{args.health_category_scope}。参考分仅能在相同策略内比较，不把不同策略前后点数当质量涨跌。",
        "先看同输入整餐结果是否实用，再看规则点数；保留失败、未变化和局部权限对照。capped模式仅实验既有最多3种做法目标与集中上限，允许内部方法频次取舍，不称质量提高。",
        "",
        f"调用：{calls}；条件：{len(rows)}；状态：{dict(counts)}。",
        "",
        "字段：降压／护心等仅定性来源，数量、份量、盐油与疗效未认证。",
        "限时包括建模但不抢占Python特征计算；实际秒数不是TTFT。INFEASIBLE不表示全库没有好菜单。",
        "",
    ]
    for row in rows:
        lines.extend(
            [
                "## " + row["case_id"],
                "",
                "用户：" + row["message"],
                "",
                "种子：" + "、".join(recipe["name"] for recipe in row["seed"]["menu"]),
                "",
            ]
        )
        for change in row["identity_peer_changes_NOT_menu_quality"]:
            lines.extend(
                [
                    f"菜位{change['slot']} {change['seed_name']}：身份策略候选 {change['legacy_count']} → {change['shared_count']}；新增{len(change['added_ids'])}、移出{len(change['removed_ids'])}。不是菜单改善，完整源证据见JSON。",
                    "",
                ]
            )
        for result in row["results"]:
            lines.extend(
                [
                    "### " + result["mode"],
                    "",
                    f"{result['status']} / {result['solver_status']}；换位 {result['changed_slots']}；实际 {result['elapsed_seconds']:.3f}s。",
                    f"参考向量：{result['goal_sums_before']} → {result['goal_sums_after']}；独立门禁违反：{result['validation_failures']}。",
                    "旧严格方法门禁取舍（非安全放宽）："
                    + str(result["strict_baseline_gate_failures_NOT_new_safety_failures"]),
                    "方法上限／明确多样要求："
                    + str(result["model_scope"].get("method_limits"))
                    + " / "
                    + str(result["model_scope"].get("explicit_method_diversity")),
                    "食材主体策略／逐槽候选数："
                    + str(result["model_scope"].get("food_identity_policy"))
                    + " / "
                    + str(result["model_scope"].get("peer_counts")),
                    "",
                ]
            )
            for index, recipe in enumerate(result["recipes"], 1):
                lines.extend(
                    [
                        f"{index}. {recipe['name']}（{recipe['recipe_id']}）",
                        "",
                        "   原料：" + recipe["raw_ingredients"],
                        "",
                        "   原步骤：" + recipe["steps"],
                        "",
                        "   来源人工复核线索（不改变分数、不证明不健康或不可做）："
                        + str(
                            result["observation"][
                                "preparation_manual_review_NOT_rank_or_certification"
                            ][recipe["recipe_id"]]
                        ),
                        "",
                    ]
                )
        if row["feasibility_explanation_NO_relaxation"] is not None:
            lines.extend(
                [
                    "约束解释（足够子集，非最小／因果排名；没有移除任何守护）：",
                    "",
                    json.dumps(row["feasibility_explanation_NO_relaxation"], ensure_ascii=False),
                    "",
                ]
            )
        if row["peer_frontier_NOT_feasible_menu_or_quality"] is not None:
            frontier = row["peer_frontier_NOT_feasible_menu_or_quality"]
            lines.extend(
                [
                    f"固定候选域参考上界（忽略跨槽限制）：{frontier['seed_reference_sum']} → {frontier['additive_upper_bound_ignoring_cross_slot_guards']}。不是可行菜单或营养／质量改善。",
                    "",
                ]
            )
            for slot in frontier["slots"]:
                lines.extend(
                    [
                        f"菜位{slot['slot']}：{slot['seed_name']}，{slot['peer_count']}个受护候选，参考上限{slot['peer_reference_maximum']}。",
                        "",
                    ]
                )
                for witness in slot["higher_reference_witnesses_NOT_recommendations"]:
                    recipe = witness["candidate"]
                    lines.extend(
                        [
                            "拒绝／待查的单菜参考候选（非推荐）：" + recipe["name"],
                            "",
                            "原料：" + recipe["raw_ingredients"],
                            "",
                            "原步骤：" + recipe["steps"],
                            "",
                            "原方法："
                            + str(witness["source_methods"])
                            + "；核验项："
                            + str(witness["single_proposal_violations_NOT_conflict_core"]),
                            "",
                        ]
                    )
        if row["capped_feasibility_explanation_NO_relaxation"] is not None:
            lines.extend(
                [
                    "capped版本仍未改善的足够子集（非最小）：",
                    "",
                    json.dumps(
                        row["capped_feasibility_explanation_NO_relaxation"], ensure_ascii=False
                    ),
                    "",
                ]
            )
        lines.extend(
            [
                "共享身份仍无改善的约束诊断（无放宽）："
                + json.dumps(row["shared_identity_explanations_NO_relaxation"], ensure_ascii=False),
                "",
                "人工：需求遗漏／食材同质／荤素汤水／做法餐次／实际可做性／健康措辞，未填，不自动给分。",
                "",
            ]
        )
    markdown = "\n".join(lines)
    (output / "report.md").write_text(markdown, encoding="utf-8")
    # Escaped text preserves the exact report and cannot execute source HTML.
    (output / "report.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>整餐复核</title><style>body{max-width:1100px;margin:32px auto;font:16px/1.6 sans-serif}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><pre>'
        + html.escape(markdown)
        + "</pre>",
        encoding="utf-8",
    )
    materials = [
        source,
        direct_path,
        api_path,
        conditions_path,
        Path(__file__).resolve(),
        Path(__file__).with_name("whole_menu_guard.py"),
        Path(__file__).with_name("whole_menu_solver.py"),
        Path(__file__).with_name("method_guard_policy.py"),
        Path(__file__).with_name("source_culinary_identity.py"),
        Path(__file__).with_name("preparation_review.py"),
        Path(__file__).with_name("requirements_solver_win_py312.txt"),
        *sorted(path for path in output.iterdir() if path.is_file()),
    ]
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "materials": [{"path": str(path), "sha256": digest(path)} for path in materials],
                "warning": "Original recipes stay local. Manifest does not certify quality or independent review.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("source", "source-snapshot", "api-snapshot", "conditions", "output"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--method-policy-ablation", action="store_true")
    parser.add_argument("--food-identity-ablation", action="store_true")
    parser.add_argument("--health-category-scope", action="store_true")
    print(run(parser.parse_args()))


if __name__ == "__main__":
    main()
