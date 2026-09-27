"""Versioned synthetic acceptance suite and report contract."""

import json

import httpx
import pytest

from evaluation.regression_suite import (
    DEFAULT_SUITE_PATH,
    CaseDefinition,
    SuiteDefinition,
    TurnDefinition,
    evaluate_turn,
    load_suite,
    run_functional_cases,
    summarize_run,
    write_report_bundle,
)


def _dish(recipe_id: str) -> dict:
    return {
        "slot": 1,
        "recipe_id": recipe_id,
        "name": f"菜品 {recipe_id}",
        "card": {"title": f"菜品 {recipe_id}"},
        "provenance": {
            "recipe_id": recipe_id,
            "source_row": 2,
            "fingerprint": f"sha-{recipe_id}",
        },
        "nutrition": {"recipe_id": recipe_id, "analysis_type": "qualitative"},
    }


def _result(ids: list[str]) -> dict:
    menu = [_dish(recipe_id) | {"slot": index} for index, recipe_id in enumerate(ids, 1)]
    return {
        "status": "ok",
        "menu": menu,
        "replacement_suggestions": [],
        "clarification_questions": [],
        "tool_calls": [
            {"name": "recipe_search"},
            {"name": "health_check"},
            {"name": "nutrition_analysis"},
        ],
        "conversation_state": {
            "constraints": {"people": 2, "meal_type": "晚餐", "dish_count": 3},
            "diners": [{"diner_id": "owner"}, {"diner_id": "guest"}],
            "menu_ids": ids,
            "menu_valid": True,
            "rejected_recipe_ids": [],
        },
        "nutrition_analysis": {"recipe_ids": ids, "analysis_type": "qualitative"},
        "diner_suitability": [
            {
                "diner_id": "owner",
                "hard_constraints_satisfied": True,
                "violations": [],
            },
            {
                "diner_id": "guest",
                "hard_constraints_satisfied": True,
                "violations": [],
            },
        ],
        "timings_ms": {"total": 100.0},
    }


def test_public_suite_is_versioned_and_uses_only_synthetic_profiles() -> None:
    suite = load_suite(DEFAULT_SUITE_PATH)

    assert suite.schema_version == "2.0"
    assert suite.dataset_version == "synthetic-regression-v2"
    assert len(suite.cases) >= 8
    assert {case.user_id for case in suite.cases} <= {900001, 900002, 900003}
    assert {case.rubric for case in suite.cases} == {"basic", "complex", "interaction"}


def test_loader_rejects_non_synthetic_profile_ids(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "dataset_version": "bad",
                "data_scope": "synthetic",
                "cases": [
                    {
                        "case_id": "private-user",
                        "rubric": "basic",
                        "user_id": 1,
                        "turns": [{"message": "test", "expect": {"status": "ok"}}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="synthetic profile"):
        load_suite(path)


def test_turn_checks_traceability_hard_constraints_and_minimal_change() -> None:
    result = _result(["keep-1", "new-2", "keep-3"])
    outcomes = evaluate_turn(
        result,
        {
            "status": "ok",
            "menu_count": 3,
            "constraints": {"people": 2, "meal_type": "晚餐"},
            "recipe_traceability": True,
            "unique_recipe_ids": True,
            "nutrition_alignment": True,
            "hard_constraints_satisfied": True,
            "diner_count": 2,
            "menu_relation": "only_slots_changed",
            "changed_slots": [2],
            "required_tools": ["health_check"],
        },
        previous_menu_ids=["keep-1", "old-2", "keep-3"],
    )

    assert outcomes
    assert all(outcome["passed"] for outcome in outcomes)


def test_turn_checks_expose_provenance_and_suitability_failures() -> None:
    result = _result(["r1", "r2", "r3"])
    result["menu"][0]["provenance"]["recipe_id"] = "wrong"
    result["diner_suitability"][1]["hard_constraints_satisfied"] = False
    result["diner_suitability"][1]["violations"] = ["花生"]

    outcomes = evaluate_turn(
        result,
        {"recipe_traceability": True, "hard_constraints_satisfied": True},
        previous_menu_ids=None,
    )
    by_name = {outcome["check"]: outcome for outcome in outcomes}

    assert by_name["recipe_traceability"]["passed"] is False
    assert by_name["hard_constraints_satisfied"]["passed"] is False
    assert "花生" in json.dumps(by_name["hard_constraints_satisfied"]["actual"], ensure_ascii=False)


def test_functional_runner_records_invalid_json_instead_of_losing_report() -> None:
    suite = SuiteDefinition(
        schema_version="1.0",
        dataset_version="test",
        data_scope="synthetic",
        description="test",
        cases=(
            CaseDefinition(
                case_id="invalid-json",
                rubric="basic",
                user_id=900001,
                tags=(),
                measure_performance=False,
                turns=(TurnDefinition(message="test", expect={"status": "ok"}),),
            ),
        ),
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not-json", request=request)
    )

    cases, raw = run_functional_cases(
        suite,
        base_url="http://test",
        timeout_seconds=1,
        transport=transport,
    )

    assert cases[0]["passed"] is False
    assert cases[0]["turns"][0]["checks"][0]["check"] == "response_json_object"
    assert raw[0]["response"] == "not-json"


def test_summary_is_internal_and_uses_declared_rubric_weights() -> None:
    cases = [
        {"rubric": "basic", "passed": True},
        {"rubric": "basic", "passed": False},
        {"rubric": "complex", "passed": True},
        {"rubric": "interaction", "passed": True},
    ]
    summary = summarize_run(
        cases,
        performance={
            "threshold_result_valid": True,
            "ttft": {"status": "excellent"},
            "single_e2e": {"status": "qualified"},
            "multi_average": {"status": "exceeded"},
        },
    )

    assert summary["score_kind"] == "internal_diagnostic_not_official"
    assert summary["rubric_scores"] == {
        "basic": 10.0,
        "complex": 20.0,
        "interaction": 30.0,
        "performance": 15.0,
    }
    assert summary["diagnostic_score"] == 75.0


def test_report_bundle_writes_machine_human_and_raw_outputs(tmp_path) -> None:
    report = {
        "dataset": {"version": "synthetic-regression-v1", "sha256": "abc"},
        "summary": {
            "cases": 1,
            "passed": 1,
            "failed": 0,
            "diagnostic_score": 100.0,
            "score_kind": "internal_diagnostic_not_official",
            "rubric_scores": {
                "basic": 20.0,
                "complex": 20.0,
                "interaction": 30.0,
                "performance": 30.0,
            },
        },
        "cases": [{"case_id": "basic", "rubric": "basic", "passed": True, "turns": []}],
    }
    paths = write_report_bundle(tmp_path, report, [{"case_id": "basic", "response": {}}])

    assert json.loads(paths["json"].read_text(encoding="utf-8"))["summary"]["passed"] == 1
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "仅供内部诊断" in markdown
    assert "basic" in markdown
    assert paths["responses"].read_text(encoding="utf-8").count("\n") == 1


@pytest.fixture(scope="module")
def recipe_catalog():
    from app.infrastructure.synthetic import load_synthetic_catalog

    return load_synthetic_catalog().recipes


def _source_dish(recipe, slot=1):
    item = _dish(recipe.recipe_id)
    item.update(
        slot=slot, name=recipe.name, ingredients=[i.name for i in recipe.ingredients],
        steps=recipe.steps, card={"title": recipe.name},
        provenance={"recipe_id": recipe.recipe_id, "source_row": recipe.source_row,
                    "fingerprint": recipe.fingerprint},
    )
    return item


def _multi_person_result(recipe_catalog):
    # Four explicitly selected source recipes without the declared restrictions.
    rows = (37, 154, 299, 1809)
    selected = [next(r for r in recipe_catalog.values() if r.source_row == row) for row in rows]
    result = _result([recipe.recipe_id for recipe in selected])
    result["menu"] = [_source_dish(recipe, index) for index, recipe in enumerate(selected, 1)]
    result["conversation_state"]["constraints"] = {
        "people": 3, "meal_type": "晚餐", "dish_count": 4, "soup_count": 0,
        "allergies": ["海鲜"], "excluded_ingredients": ["花生"], "no_spicy": True,
    }
    result["conversation_state"]["diners"] = [
        {"diner_id": "owner", "profile_owner": True, "display_name": "用户", "attendance": True,
         "allergies": ["海鲜"], "excluded_ingredients": [], "no_spicy": False},
        {"diner_id": "wang", "profile_owner": False, "display_name": "小王", "attendance": True,
         "allergies": [], "excluded_ingredients": ["花生"], "no_spicy": False},
        {"diner_id": "li", "profile_owner": False, "display_name": "小李", "attendance": True,
         "allergies": [], "excluded_ingredients": [], "no_spicy": True},
    ]
    result["diner_suitability"] = [
        {"diner_id": diner["diner_id"], "hard_constraints_satisfied": True, "violations": []}
        for diner in result["conversation_state"]["diners"]
    ]
    return result


def _multi_expectation():
    return next(
        case.turns[0].expect for case in load_suite().cases
        if case.case_id == "complex_multi_person_constraints"
    )


def test_v1_is_preserved_and_remains_loadable():
    legacy = load_suite(DEFAULT_SUITE_PATH.with_name("regression_v1.json"))
    assert legacy.schema_version == "1.0"
    assert legacy.dataset_version == "synthetic-regression-v1"
    assert "expected_diners" not in next(
        case.turns[0].expect for case in legacy.cases
        if case.case_id == "complex_multi_person_constraints"
    )


def test_independent_oracle_accepts_source_menu_with_correct_personal_facts(recipe_catalog):
    result = _multi_person_result(recipe_catalog)
    checks = evaluate_turn(result, _multi_expectation(), previous_menu_ids=None,
                           recipes=recipe_catalog)
    assert all(check["passed"] for check in checks), checks


@pytest.mark.parametrize("mutation", ["delete_all", "swap_people", "drop_aggregate"])
def test_independent_oracle_rejects_missing_or_misattributed_facts(recipe_catalog, mutation):
    result = _multi_person_result(recipe_catalog)
    state = result["conversation_state"]
    if mutation == "delete_all":
        for diner in state["diners"]:
            diner.update(allergies=[], excluded_ingredients=[], no_spicy=False)
        state["constraints"].update(allergies=[], excluded_ingredients=[], no_spicy=False)
    elif mutation == "swap_people":
        state["diners"][0]["allergies"] = []
        state["diners"][1]["allergies"] = ["海鲜"]
    else:
        state["constraints"]["excluded_ingredients"] = []
    checks = evaluate_turn(result, _multi_expectation(), previous_menu_ids=None,
                           recipes=recipe_catalog)
    assert not all(check["passed"] for check in checks)
    # Self-reported pass flags remain true and cannot mask the independent failure.
    assert next(check for check in checks if check["check"] == "hard_constraints_satisfied")["passed"]
    failed = [check["check"] for check in checks if not check["passed"]]
    assert any(name.startswith("diner_facts") or name == "constraints" for name in failed)


@pytest.mark.parametrize("location", ["menu", "replacement_suggestions"])
def test_independent_oracle_rejects_real_shrimp_even_if_returned_ingredients_hide_it(
    recipe_catalog, location,
):
    result = _multi_person_result(recipe_catalog)
    shrimp = next(r for r in recipe_catalog.values() if r.source_row == 17)
    item = _source_dish(shrimp)
    item.update(ingredients=["鸡蛋", "水"], steps="蒸熟即可")
    if location == "menu":
        result["menu"][0] = item
        result["nutrition_analysis"]["recipe_ids"][0] = shrimp.recipe_id
    else:
        result[location] = [item]
    checks = evaluate_turn(result, _multi_expectation(), previous_menu_ids=None,
                           recipes=recipe_catalog)
    independent = next(c for c in checks if c["check"] == "independent_food_constraints")
    assert not independent["passed"]
    assert any(f["location"] == location and f["origin"] == "catalog"
               and "虾" in f["matched"] for f in independent["actual"])


@pytest.mark.parametrize("mutation", ["unknown_id", "wrong_fingerprint", "visible_ingredient"])
def test_catalog_oracle_rejects_untrusted_response_content(recipe_catalog, mutation):
    result = _multi_person_result(recipe_catalog)
    item = result["menu"][0]
    if mutation == "unknown_id":
        item["recipe_id"] = "invented"
    elif mutation == "wrong_fingerprint":
        item["provenance"]["fingerprint"] = "forged"
    else:
        item["steps"] += "加入花生油炒香。"
    checks = evaluate_turn(result, _multi_expectation(), previous_menu_ids=None,
                           recipes=recipe_catalog)
    assert any(not c["passed"] for c in checks if c["check"] in {
        "catalog_traceability", "independent_food_constraints"
    })


def test_independent_oracle_fails_closed_without_source_catalog():
    checks = evaluate_turn(_result(["unknown"]), _multi_expectation(), previous_menu_ids=None)
    assert not next(c for c in checks if c["check"] == "catalog_traceability")["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("failed_request", "expected_counts"), [
    (4, {"scheduled": 4, "requests": 4, "successful": 3, "failed": 1, "not_executed": 0}),
    (3, {"scheduled": 4, "requests": 3, "successful": 2, "failed": 1, "not_executed": 1}),
])
async def test_failed_performance_is_unscored_and_counts_skipped_turns(
    monkeypatch, tmp_path, failed_request, expected_counts,
):
    from evaluation import regression_suite as regression
    from evaluation.stream_performance import StreamObservation

    requests = 0

    async def measure(_client, **kwargs):
        nonlocal requests
        requests += 1
        fail = requests == failed_request
        return StreamObservation(
            scenario_id=kwargs["scenario_id"], turn=kwargs["turn"],
            request_id=kwargs["request_id"], status_code=503 if fail else 200,
            ttft_ms=None if fail else 100.0, e2e_ms=90000.0 if fail else 200.0,
            content_chars=0 if fail else 10, completed=not fail,
            session_id="test-session", error="http_503" if fail else None,
        )

    monkeypatch.setattr(regression, "measure_stream_turn", measure)
    performance = await regression.run_performance_cases(
        load_suite(), base_url="http://test", timeout_seconds=1,
    )
    assert performance["counts"] == expected_counts
    assert performance["threshold_result_valid"] is False
    assert performance["ttft"]["status"] == "invalid"
    assert performance["ttft"]["successful_only_status"] == "excellent"
    cases = [{"case_id": key, "rubric": key, "passed": True, "turns": []}
             for key in ("basic", "complex", "interaction")]
    summary = summarize_run(cases, performance=performance)
    assert summary["functional_score"] == 70
    assert summary["rubric_scores"]["performance"] is None
    assert summary["diagnostic_score"] is None
    assert summary["performance_status"] == "invalid"
    report = {"dataset": {"version": "v2", "sha256": "test"}, "cases": cases,
              "summary": summary, "performance": performance}
    paths = write_report_bundle(tmp_path, report, [])
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "阈值结果有效：False" in markdown
    assert "计划 / 已执行 / 成功 / 失败 / 未执行" in markdown
    assert "内部诊断分：未生成" in markdown
    assert "100.0/100" not in markdown


def test_skipped_performance_produces_no_total_score():
    summary = summarize_run([{"rubric": "basic", "passed": True}], performance=None)
    assert summary["performance_status"] == "not_run"
    assert summary["diagnostic_score"] is None
    assert summary["rubric_scores"]["performance"] is None
