import csv
import json
from pathlib import Path

import pytest

from evaluation.judge_calibration import (
    _cohen_kappa,
    evaluate_calibration,
    prepare_review_packet,
)


def _write_regression_bundle(root: Path, *, reason: str) -> Path:
    root.mkdir(parents=True)
    cases = []
    responses = []
    for number in range(1, 6):
        case_id = f"case-{number}"
        cases.append(
            {
                "case_id": case_id,
                "rubric": "interaction",
                "tags": ["多轮"],
                "passed": True,
                "turns": [{"turn": 1, "message": "请安排晚餐。", "passed": True}],
            }
        )
        responses.append(
            {
                "case_id": case_id,
                "turn": 1,
                "response": {
                    "status": "ok",
                    "reason": f"{reason}-{number}",
                    "menu": [{"recipe_id": f"r{number}", "name": f"菜{number}"}],
                },
            }
        )
    report = {
        "dataset": {"sha256": "dataset-sha", "data_scope": "synthetic"},
        "summary": {"failed": 0},
        "cases": cases,
    }
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (root / "responses.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in responses),
        encoding="utf-8",
    )
    return report_path


def _write_judge_report(path: Path, winners: list[str]) -> Path:
    report = {
        "comparison": {"dataset_sha256": "dataset-sha"},
        "summary": {"result_kind": "independent_llm_pairwise_not_official"},
        "cases": [
            {"case_id": f"case-{index}", "winner": winner}
            for index, winner in enumerate(winners, start=1)
        ],
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _fill_labels(labels_path: Path, key_path: Path, system_winners: list[str]) -> None:
    key = json.loads(key_path.read_text(encoding="utf-8"))
    rows = []
    for item, system_winner in zip(key["reviews"], system_winners, strict=True):
        if system_winner == "tie":
            blind_winner = "tie"
        else:
            blind_winner = "A" if item["system_A"] == system_winner else "B"
        rows.append({"review_id": item["review_id"], "winner": blind_winner, "notes": ""})
    with labels_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["review_id", "winner", "notes"])
        writer.writeheader()
        writer.writerows(rows)


def test_prepare_review_packet_separates_blinded_items_from_key(tmp_path: Path) -> None:
    baseline = _write_regression_bundle(tmp_path / "baseline", reason="旧版")
    candidate = _write_regression_bundle(tmp_path / "candidate", reason="新版")

    paths = prepare_review_packet(
        baseline, candidate, tmp_path / "review", order_seed="secret-order"
    )

    packet = [json.loads(line) for line in paths["packet"].read_text(encoding="utf-8").splitlines()]
    key = json.loads(paths["key"].read_text(encoding="utf-8"))
    assert len(packet) == 5
    assert "case_id" not in packet[0]
    assert "system_A" not in packet[0]
    assert {key["reviews"][0]["system_A"], key["reviews"][0]["system_B"]} == {
        "baseline",
        "candidate",
    }
    assert paths["labels"].read_text(encoding="utf-8-sig").startswith(
        "review_id,winner,notes"
    )


def test_calibration_maps_blind_labels_and_measures_agreement(tmp_path: Path) -> None:
    baseline = _write_regression_bundle(tmp_path / "baseline", reason="旧版")
    candidate = _write_regression_bundle(tmp_path / "candidate", reason="新版")
    paths = prepare_review_packet(
        baseline, candidate, tmp_path / "review", order_seed="secret-order"
    )
    _fill_labels(
        paths["labels"], paths["key"], ["candidate", "baseline", "tie", "candidate", "tie"]
    )
    judge_report = _write_judge_report(
        tmp_path / "judge.json", ["candidate", "baseline", "tie", "candidate", "baseline"]
    )

    result = evaluate_calibration(judge_report, paths["key"], paths["labels"])

    assert result["status"] == "calibration_thresholds_met"
    assert result["exact_agreement"] == 0.8
    assert result["cohen_kappa"] == pytest.approx(0.7059)
    assert result["confusion_matrix"]["tie"]["baseline"] == 1
    assert len(result["disagreements"]) == 1


def test_calibration_treats_inconclusive_judge_as_abstention(tmp_path: Path) -> None:
    baseline = _write_regression_bundle(tmp_path / "baseline", reason="旧版")
    candidate = _write_regression_bundle(tmp_path / "candidate", reason="新版")
    paths = prepare_review_packet(
        baseline, candidate, tmp_path / "review", order_seed="secret-order"
    )
    _fill_labels(paths["labels"], paths["key"], ["candidate"] * 5)
    judge_report = _write_judge_report(
        tmp_path / "judge.json", ["inconclusive", "candidate", "candidate", "candidate", "candidate"]
    )

    result = evaluate_calibration(judge_report, paths["key"], paths["labels"])

    assert result["status"] == "excessive_judge_abstention"
    assert result["judge_abstentions"] == 1
    assert result["judge_comparable_cases"] == 4


def test_calibration_reports_insufficient_human_sample(tmp_path: Path) -> None:
    baseline = _write_regression_bundle(tmp_path / "baseline", reason="旧版")
    candidate = _write_regression_bundle(tmp_path / "candidate", reason="新版")
    paths = prepare_review_packet(
        baseline, candidate, tmp_path / "review", order_seed="secret-order"
    )
    _fill_labels(paths["labels"], paths["key"], ["candidate"] * 5)
    rows = paths["labels"].read_text(encoding="utf-8-sig").splitlines()
    paths["labels"].write_text("\n".join(rows[:3]) + "\n", encoding="utf-8-sig")
    judge_report = _write_judge_report(tmp_path / "judge.json", ["candidate"] * 5)

    result = evaluate_calibration(judge_report, paths["key"], paths["labels"])

    assert result["status"] == "insufficient_human_labels"
    assert result["human_reviewed_cases"] == 2


def test_cohen_kappa_is_unavailable_for_constant_identical_labels() -> None:
    assert _cohen_kappa([("candidate", "candidate")] * 5) is None
