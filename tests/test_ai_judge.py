import json
from pathlib import Path
from typing import Any

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


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [1800, 8192])
async def test_judge_client_sends_configured_completion_budget(budget: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["max_tokens"] == budget
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _judgment("tie").model_dump_json()},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = JudgeClient(
            api_key="secret",
            base_url="https://judge.example/v1",
            model="judge-model",
            max_tokens=budget,
            client=transport,
        )
        assert (await client.judge({"case_id": "case-1"})).winner == "tie"


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_judge_client_rejects_invalid_completion_budget(budget: int) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        JudgeClient(
            api_key="secret",
            base_url="https://judge.example/v1",
            model="judge-model",
            max_tokens=budget,
        )


@pytest.mark.asyncio
async def test_judge_client_rejects_truncation_even_when_json_is_valid() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": _judgment("tie").model_dump_json()},
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
        with pytest.raises(JudgeError, match="token budget"):
            await client.judge({"case_id": "case-1"})


@pytest.mark.parametrize("cli_budget,expected", [(None, 8192), (4096, 4096)])
def test_judge_cli_uses_budget_and_writes_complete_mocked_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cli_budget: int | None, expected: int
) -> None:
    from evaluation import ai_judge

    baseline = _write_bundle(tmp_path / "baseline")
    candidate = _write_bundle(tmp_path / "candidate")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["max_tokens"] == expected
        requests.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": _judgment("tie").model_dump_json()},
                    }
                ]
            },
        )

    original_client = httpx.AsyncClient

    def mocked_client() -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ai_judge.httpx, "AsyncClient", mocked_client)
    monkeypatch.setenv("JUDGE_API_KEY", "secret")
    monkeypatch.setenv("JUDGE_BASE_URL", "https://judge.example/v1")
    monkeypatch.setenv("JUDGE_MODEL", "judge-model")
    monkeypatch.setenv("JUDGE_MAX_TOKENS", "8192")
    output = tmp_path / "judge"
    arguments = [
        "--baseline-report",
        str(baseline),
        "--candidate-report",
        str(candidate),
        "--output-dir",
        str(output),
    ]
    if cli_budget is not None:
        arguments += ["--max-tokens", str(cli_budget)]
    assert ai_judge.main(arguments) == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["judge"]["max_tokens"] == expected
    assert report["summary"]["wins"]["tie"] == 1
    assert len(requests) == 2
    assert (output / "report.md").is_file()
    assert (output / "judgments.jsonl").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure,expected",
    [
        ("timeout", "timed out"),
        ("network", "network error"),
        ("http", "HTTP 429"),
        ("incomplete", "invalid structured output"),
    ],
)
async def test_judge_client_safe_failure_paths(failure: str, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if failure == "network":
            raise httpx.ConnectError("unreachable", request=request)
        if failure == "http":
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "content_filter",
                        "message": {"content": "{}"},
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
        with pytest.raises(JudgeError, match=expected):
            await client.judge({"case_id": "case-1"})


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"api_key": ""}, "required"),
        ({"model": ""}, "required"),
        ({"timeout_seconds": 0}, "positive"),
        ({"base_url": "ftp://judge.example"}, "HTTP"),
    ],
)
def test_judge_client_rejects_invalid_settings(changes: dict[str, Any], expected: str) -> None:
    settings = {"api_key": "secret", "base_url": "https://judge.example", "model": "judge-model"}
    with pytest.raises(ValueError, match=expected):
        JudgeClient(**(settings | changes))


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("failed", "deterministic failures"),
        ("no_cases", "does not contain cases"),
        ("invalid_case", "invalid case"),
        ("duplicate_case", "Duplicate case"),
        ("turn_count", "Turn count differs"),
        ("turn_id", "Turn identity differs"),
        ("message", "User message differs"),
        ("missing_response", "Missing raw response"),
        ("non_object_response", "Response must be an object"),
        ("duplicate_response", "Duplicate response"),
        ("invalid_response", "Invalid response record"),
        ("missing_file", "Cannot read responses"),
    ],
)
def test_load_comparison_blocks_damaged_evidence(
    tmp_path: Path, damage: str, expected: str
) -> None:
    baseline = _write_bundle(tmp_path / "baseline")
    candidate = _write_bundle(tmp_path / "candidate")
    report = json.loads(candidate.read_text(encoding="utf-8"))
    responses = candidate.parent / "responses.jsonl"
    if damage == "failed":
        report["summary"]["failed"] = 1
    elif damage == "no_cases":
        report["cases"] = None
    elif damage == "invalid_case":
        report["cases"][0]["case_id"] = ""
    elif damage == "duplicate_case":
        report["cases"].append(report["cases"][0])
    elif damage == "turn_count":
        report["cases"][0]["turns"].append(report["cases"][0]["turns"][0])
    elif damage == "turn_id":
        report["cases"][0]["turns"][0]["turn"] = 2
    elif damage == "message":
        report["cases"][0]["turns"][0]["message"] = "不同输入"
    elif damage == "missing_response":
        responses.write_text("", encoding="utf-8")
    elif damage == "non_object_response":
        responses.write_text(
            json.dumps({"case_id": "case-1", "turn": 1, "response": []}), encoding="utf-8"
        )
    elif damage == "duplicate_response":
        responses.write_text(responses.read_text(encoding="utf-8") * 2, encoding="utf-8")
    elif damage == "invalid_response":
        responses.write_text("not JSON\n", encoding="utf-8")
    elif damage == "missing_file":
        responses.unlink()
    candidate.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        load_comparison(baseline, candidate)


@pytest.mark.parametrize("text", ['{"same":1,"same":2}', '{"value":NaN}', "[]"])
def test_judge_report_parser_rejects_ambiguous_json(tmp_path: Path, text: str) -> None:
    from evaluation.ai_judge import _load_json

    path = tmp_path / "report.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        _load_json(path)


def test_judge_summary_rejects_no_judgments() -> None:
    with pytest.raises(ValueError, match="At least one"):
        summarize([])
