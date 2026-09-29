"""Prepare blinded human reviews and measure agreement with the AI judge.

The resulting agreement metrics are calibration evidence only. They are not an
official score and never replace deterministic safety or traceability checks.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from evaluation.ai_judge import _load_json, load_comparison
from evaluation.reporting import write_bundle

HUMAN_WINNERS = {"A", "B", "tie"}
SYSTEM_WINNERS = ("baseline", "candidate", "tie")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _blind_candidate_on_a(order_seed: str, dataset_sha256: str, case_id: str) -> bool:
    digest = hashlib.sha256(
        f"{order_seed}\0{dataset_sha256}\0{case_id}".encode("utf-8")
    ).digest()
    return bool(digest[0] & 1)


def prepare_review_packet(
    baseline_report: Path,
    candidate_report: Path,
    output_dir: Path,
    *,
    order_seed: str,
) -> dict[str, Path]:
    """Create a reviewer-safe A/B packet and a separately held blinding key."""

    if not order_seed:
        raise ValueError("A non-empty order seed is required")
    cases, metadata = load_comparison(baseline_report, candidate_report)
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_path = output_dir / "human_review_packet.jsonl"
    labels_path = output_dir / "human_labels.csv"
    key_path = output_dir / "human_blinding_key.json"
    guide_path = output_dir / "HUMAN_REVIEW.md"

    packet_records: list[dict[str, Any]] = []
    key_records: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        review_id = f"review-{index:03d}"
        candidate_on_a = _blind_candidate_on_a(
            order_seed, metadata["dataset_sha256"], case["case_id"]
        )
        system_a = "candidate" if candidate_on_a else "baseline"
        system_b = "baseline" if candidate_on_a else "candidate"
        turns = []
        for turn in case["turns"]:
            response_a_key = f"{system_a}_response"
            response_b_key = f"{system_b}_response"
            turns.append(
                {
                    "turn": turn["turn"],
                    "user_message": turn["user_message"],
                    "response_A": turn[response_a_key],
                    "response_B": turn[response_b_key],
                }
            )
        packet_records.append(
            {
                "schema_version": "human-review-item-v1",
                "review_id": review_id,
                "rubric": case.get("rubric"),
                "tags": case.get("tags", []),
                "turns": turns,
            }
        )
        key_records.append(
            {
                "review_id": review_id,
                "case_id": case["case_id"],
                "system_A": system_a,
                "system_B": system_b,
            }
        )

    packet_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in packet_records),
        encoding="utf-8",
    )
    with labels_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["review_id", "winner", "notes"])
        writer.writeheader()
        for record in packet_records:
            writer.writerow({"review_id": record["review_id"], "winner": "", "notes": ""})
    _write_json(
        key_path,
        {
            "schema_version": "human-review-key-v1",
            "comparison": metadata,
            "order_seed_sha256": hashlib.sha256(order_seed.encode("utf-8")).hexdigest(),
            "reviews": key_records,
        },
    )
    guide_path.write_text(
        """# 人工盲评说明

1. 评审员只查看 `human_review_packet.jsonl` 和 `human_labels.csv`。
2. 不要向评审员提供 `human_blinding_key.json`，以免暴露版本身份。
3. 每个场景按需求满足、套餐搭配、交互、最小修改和解释质量综合判断。
4. `winner` 只能填写 `A`、`B` 或 `tie`；无法判断时保留为空并在 notes 说明。
5. 不因回答更长而加分，不重新裁决已经通过的确定性硬约束。

完成人工标签后，再运行校准命令。结果仅表示本批样本上的人机一致性，不是官方成绩。
""",
        encoding="utf-8",
    )
    return {
        "packet": packet_path,
        "labels": labels_path,
        "key": key_path,
        "guide": guide_path,
    }


def _load_human_labels(path: Path) -> dict[str, Literal["A", "B", "tie"]]:
    labels: dict[str, Literal["A", "B", "tie"]] = {}
    seen: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not {"review_id", "winner"}.issubset(reader.fieldnames):
                raise ValueError("Human labels must contain review_id and winner columns")
            for line, row in enumerate(reader, start=2):
                review_id = str(row.get("review_id", "")).strip()
                winner = str(row.get("winner", "")).strip()
                if not review_id:
                    raise ValueError(f"Missing review_id at label row {line}")
                if review_id in seen:
                    raise ValueError(f"Duplicate human label for {review_id}")
                seen.add(review_id)
                if not winner:
                    continue
                if winner not in HUMAN_WINNERS:
                    raise ValueError(f"Invalid human winner for {review_id}: {winner}")
                labels[review_id] = winner  # type: ignore[assignment]
    except OSError as error:
        raise ValueError(f"Cannot read human labels from {path}: {error}") from error
    return labels


def _cohen_kappa(
    pairs: list[tuple[str, str]], categories: tuple[str, ...] = SYSTEM_WINNERS
) -> float | None:
    if not pairs:
        return None
    observed = sum(human == judge for human, judge in pairs) / len(pairs)
    expected = 0.0
    for category in categories:
        human_share = sum(human == category for human, _ in pairs) / len(pairs)
        judge_share = sum(judge == category for _, judge in pairs) / len(pairs)
        expected += human_share * judge_share
    if abs(1.0 - expected) < 1e-12:
        return None
    return round((observed - expected) / (1.0 - expected), 4)


def evaluate_calibration(
    judge_report_path: Path,
    key_path: Path,
    labels_path: Path,
    *,
    min_cases: int = 5,
    min_agreement: float = 0.7,
    min_kappa: float = 0.4,
) -> dict[str, Any]:
    """Compare normalized AI winners with blinded human labels."""

    if min_cases <= 0:
        raise ValueError("min_cases must be positive")
    if not 0 <= min_agreement <= 1 or not -1 <= min_kappa <= 1:
        raise ValueError("Calibration thresholds are out of range")
    judge_report = _load_json(judge_report_path)
    if judge_report.get("summary", {}).get("result_kind") != (
        "independent_llm_pairwise_not_official"
    ):
        raise ValueError("Input is not an independent pairwise AI judge report")
    key = _load_json(key_path)
    if key.get("schema_version") != "human-review-key-v1":
        raise ValueError("Unsupported human review key")
    expected_hash = key.get("comparison", {}).get("dataset_sha256")
    actual_hash = judge_report.get("comparison", {}).get("dataset_sha256")
    if not expected_hash or expected_hash != actual_hash:
        raise ValueError("Human review and AI judge dataset hashes do not match")

    judge_cases = {
        str(item["case_id"]): item
        for item in judge_report.get("cases", [])
        if isinstance(item, dict) and item.get("case_id")
    }
    review_keys = {
        str(item["review_id"]): item
        for item in key.get("reviews", [])
        if isinstance(item, dict) and item.get("review_id")
    }
    if len(review_keys) != len(key.get("reviews", [])):
        raise ValueError("Human review key contains duplicate or invalid entries")
    labels = _load_human_labels(labels_path)
    unknown = sorted(set(labels) - set(review_keys))
    if unknown:
        raise ValueError(f"Human labels contain unknown review IDs: {', '.join(unknown)}")

    confusion = {
        human: {judge: 0 for judge in SYSTEM_WINNERS} for human in SYSTEM_WINNERS
    }
    pairs: list[tuple[str, str]] = []
    disagreements: list[dict[str, str]] = []
    abstentions: list[dict[str, str]] = []
    for review_id, blind_winner in labels.items():
        review_key = review_keys[review_id]
        case_id = str(review_key["case_id"])
        if case_id not in judge_cases:
            raise ValueError(f"AI judge report is missing case {case_id}")
        human_winner = (
            "tie" if blind_winner == "tie" else str(review_key[f"system_{blind_winner}"])
        )
        judge_winner = str(judge_cases[case_id].get("winner"))
        if judge_winner == "inconclusive":
            abstentions.append(
                {"review_id": review_id, "case_id": case_id, "human_winner": human_winner}
            )
            continue
        if judge_winner not in SYSTEM_WINNERS:
            raise ValueError(f"Invalid AI judge winner for case {case_id}")
        confusion[human_winner][judge_winner] += 1
        pairs.append((human_winner, judge_winner))
        if human_winner != judge_winner:
            disagreements.append(
                {
                    "review_id": review_id,
                    "case_id": case_id,
                    "human_winner": human_winner,
                    "judge_winner": judge_winner,
                }
            )

    agreement = (
        round(sum(human == judge for human, judge in pairs) / len(pairs), 4)
        if pairs
        else None
    )
    kappa = _cohen_kappa(pairs)
    reviewed = len(labels)
    comparable = len(pairs)
    if reviewed < min_cases:
        status = "insufficient_human_labels"
    elif comparable < min_cases:
        status = "excessive_judge_abstention"
    elif kappa is None:
        status = "kappa_unavailable"
    elif agreement is not None and agreement >= min_agreement and kappa >= min_kappa:
        status = "calibration_thresholds_met"
    else:
        status = "calibration_below_threshold"
    return {
        "result_kind": "human_judge_agreement_not_official",
        "status": status,
        "dataset_sha256": actual_hash,
        "total_cases": len(review_keys),
        "human_reviewed_cases": reviewed,
        "judge_comparable_cases": comparable,
        "judge_abstentions": len(abstentions),
        "human_coverage": round(reviewed / len(review_keys), 4) if review_keys else 0.0,
        "judge_comparable_rate": round(comparable / reviewed, 4) if reviewed else None,
        "exact_agreement": agreement,
        "cohen_kappa": kappa,
        "thresholds": {
            "min_cases": min_cases,
            "min_agreement": min_agreement,
            "min_kappa": min_kappa,
        },
        "confusion_matrix": confusion,
        "disagreements": disagreements,
        "abstentions": abstentions,
    }


def render_markdown(report: dict[str, Any]) -> str:
    agreement = report["exact_agreement"]
    kappa = report["cohen_kappa"]
    lines = [
        "# AI 裁判人工校准报告",
        "",
        "> 本报告只描述本批合成样本上的人机一致性，不是官方成绩。",
        "",
        f"- 状态：`{report['status']}`",
        f"- 人工已评：{report['human_reviewed_cases']} / {report['total_cases']}",
        f"- 可比较：{report['judge_comparable_cases']}",
        f"- AI 弃权（顺序不一致）：{report['judge_abstentions']}",
        f"- 完全一致率：{'未计算' if agreement is None else f'{agreement:.1%}'}",
        f"- Cohen’s κ：{'未计算' if kappa is None else kappa}",
        "",
        "## 混淆矩阵",
        "",
        "行是人工结论，列是 AI 结论。",
        "",
        "| 人工 \\ AI | baseline | candidate | tie |",
        "|---|---:|---:|---:|",
    ]
    for human in SYSTEM_WINNERS:
        row = report["confusion_matrix"][human]
        lines.append(
            f"| {human} | {row['baseline']} | {row['candidate']} | {row['tie']} |"
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 样本不足、κ 无法计算或低于阈值时，不应把 AI 裁判用于质量结论。",
            "- 达到阈值也只说明本批数据上的一致性，不能替代专家评审。",
            "- AI 顺序不一致的场景按弃权处理，不用均分掩盖不稳定性。",
            "- 分歧与弃权明细保存在 `report.json`，应逐条复核。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="create a blinded human review packet")
    prepare.add_argument("--baseline-report", type=Path, required=True)
    prepare.add_argument("--candidate-report", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--order-seed", required=True)

    evaluate = subparsers.add_parser("evaluate", help="measure AI/human agreement")
    evaluate.add_argument("--judge-report", type=Path, required=True)
    evaluate.add_argument("--review-key", type=Path, required=True)
    evaluate.add_argument("--human-labels", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--min-cases", type=int, default=5)
    evaluate.add_argument("--min-agreement", type=float, default=0.7)
    evaluate.add_argument("--min-kappa", type=float, default=0.4)
    args = parser.parse_args(argv)

    if args.command == "prepare":
        paths = prepare_review_packet(
            args.baseline_report,
            args.candidate_report,
            args.output_dir,
            order_seed=args.order_seed,
        )
        for name, path in paths.items():
            print(f"{name}: {path}")
        return 0

    report = evaluate_calibration(
        args.judge_report,
        args.review_key,
        args.human_labels,
        min_cases=args.min_cases,
        min_agreement=args.min_agreement,
        min_kappa=args.min_kappa,
    )
    records = [
        {"record_type": "disagreement", **item} for item in report["disagreements"]
    ] + [{"record_type": "abstention", **item} for item in report["abstentions"]]
    paths = write_bundle(
        args.output_dir,
        report,
        render_markdown(report),
        records=records,
        records_name="calibration_cases.jsonl",
    )
    print(f"Calibration JSON: {paths['json']}")
    print(f"Calibration Markdown: {paths['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
