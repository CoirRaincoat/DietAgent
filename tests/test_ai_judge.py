import json
from pathlib import Path

import httpx
import pytest

from evaluation.ai_judge import (
    DimensionScores,
    JudgeClient,
    JudgeError,
    OrderedJudgment,
    compact_response,
    load_comparison,
    reconcile_case,
    summarize,
)


def _scores(value: int) -> DimensionScores:
    return DimensionScores(
        requirement_fulfillment=value,
        menu_coherence=value,
        interaction_quality=value,
        minimal_change=value,
        explanation_quality=value,
    )


def _judgment(winner: str, a: int = 3, b: int = 4) -> OrderedJudgment:
    return OrderedJudgment(
        winner=winner,
        confidence=0.8,
        scores_a=_scores(a),
        scores_b=_scores(b),
        evidence=["B 的解释更具体。"],
        limitations=[],
    )


def _write_bundle(root: Path, *, dataset_hash: str = "same", scope: str = "synthetic") -> Path:
    root.mkdir(parents=True)
    report = {
        "dataset": {"sha256": dataset_hash, "data_scope": scope},
        "summary": {"failed": 0},
        "cases": [
            {
                "case_id": "case-1",
                "rubric": "basic",
                "tags": ["单轮"],
                "passed": True,
                "turns": [
                    {
                        "turn": 1,
                        "message": "安排三道菜。",
                        "passed": True,
                        "menu_quality": {"status": "available"},
                    }
                ],
            }
        ],
    }
    path = root / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    record = {
        "case_id": "case-1",
        "turn": 1,
        "response": {
            "status": "ok",
            "reason": "已按要求安排。",
            "menu": [{"slot": 1, "recipe_id": "r1", "name": "菜一", "steps": "秘密步骤"}],
        },
    }
    (root / "responses.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return path


def test_compact_response_keeps_judge_facts_but_drops_verbose_steps() -> None:
    compact = compact_response(
        {
            "status": "ok",
            "reason": "理由",
            "menu": [
                {
                    "slot": 1,
                    "recipe_id": "r1",
                    "name": "菜一",
                    "ingredients": ["鸡肉"],
                    "steps": "不应发送",
                    "card": {"badges": ["蒸"]},
                    "nutrition": {"protein_sources": ["鸡肉"]},
                }
            ],
        }
    )
    assert compact["menu"][0]["protein_sources"] == ["鸡肉"]
    assert "steps" not in compact["menu"][0]


def test_load_comparison_requires_matching_synthetic_runs(tmp_path: Path) -> None:
    baseline = _write_bundle(tmp_path / "baseline")
    candidate = _write_bundle(tmp_path / "candidate")
    cases, metadata = load_comparison(baseline, candidate)
    assert cases[0]["turns"][0]["user_message"] == "安排三道菜。"
    assert metadata["dataset_sha256"] == "same"


@pytest.mark.parametrize(
    ("candidate_hash", "candidate_scope", "message"),
    [("different", "synthetic", "hashes"), ("same", "private", "not synthetic")],
)
def test_load_comparison_blocks_incompatible_or_private_runs(
    tmp_path: Path, candidate_hash: str, candidate_scope: str, message: str
) -> None:
    baseline = _write_bundle(tmp_path / "baseline")
    candidate = _write_bundle(
        tmp_path / "candidate", dataset_hash=candidate_hash, scope=candidate_scope
    )
    with pytest.raises(ValueError, match=message):
        load_comparison(baseline, candidate)


def test_reconcile_maps_reversed_labels_to_candidate() -> None:
    result = reconcile_case(
        {"case_id": "case-1", "rubric": "basic", "tags": []},
        _judgment("B"),
        _judgment("A"),
    )
    assert result["winner"] == "candidate"
    assert result["order_consistent"] is True
    assert result["candidate_scores"]["menu_coherence"] == 3.5


def test_reconcile_does_not_hide_order_disagreement() -> None:
    result = reconcile_case(
        {"case_id": "case-1", "rubric": "basic", "tags": []},
        _judgment("B"),
        _judgment("B"),
    )
    assert result["winner"] == "inconclusive"
    assert result["order_consistent"] is False


def test_summary_is_pairwise_evidence_not_a_percentage_score() -> None:
    candidate = reconcile_case(
        {"case_id": "one", "rubric": "basic", "tags": []},
        _judgment("B"),
        _judgment("A"),
    )
    baseline = reconcile_case(
        {"case_id": "two", "rubric": "basic", "tags": []},
        _judgment("A"),
        _judgment("B"),
    )
    result = summarize([candidate, baseline])
    assert result["preference"] == "no_clear_preference"
    assert result["result_kind"] == "independent_llm_pairwise_not_official"
    assert "score" not in result


@pytest.mark.asyncio
async def test_judge_client_validates_structured_json() -> None:
    body = _judgment("tie").model_dump()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(body, ensure_ascii=False)},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = JudgeClient(
            api_key="secret",
            base_url="https://judge.example/v1",
            model="judge-model",
            client=transport,
        )
        result = await client.judge({"case_id": "case-1"})
    assert result.winner == "tie"


@pytest.mark.asyncio
async def test_judge_client_rejects_invalid_output() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = JudgeClient(
            api_key="secret",
            base_url="https://judge.example/v1",
            model="judge-model",
            client=transport,
        )
        with pytest.raises(JudgeError, match="invalid structured output"):
            await client.judge({"case_id": "case-1"})
