"""Public curation contracts: exact-source edits are not human approval."""

import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.domain.preparation_enrichment import (
    AssistantCuration,
    StepAddition,
    audit_preparation,
    supplemented_steps,
    tag_enrichment,
)
from app.infrastructure.data import normalize_recipes


def source():
    return next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "豆馅面包",
                        "食材清单": "面粉；水；酵母；豆沙馅",
                        "label": "",
                        "烹饪步骤": "第1步：面粉、水、酵母揉成面团后发酵。",
                    }
                ]
            ).values()
        )
    )


def curation(recipe=None, **updates):
    recipe = recipe or source()
    base = audit_preparation(recipe)
    values = {
        "recipe_id": recipe.recipe_id,
        "source_name": recipe.name,
        "source_fingerprint": recipe.fingerprint,
        "source_content_sha256": base.source_content_sha256,
        "disposition": "supplement_draft",
        "reason": "已声明豆沙馅但缺包馅与最后烘烤；补参考做法，不是原文。",
        "additions": [
            StepAddition(
                "after_source", "排气整形，包入豆沙馅并收口，完成末次醒发后烘烤。", ("豆沙馅",)
            )
        ],
        "tag_suggestions": [
            {"tag": "含豆沙的面团制品（缺最后烘烤）", "dimension": "culinary_form"}
        ],
        "remaining_questions": ["设备参数需试做"],
        "reviewer_kind": "assistant",
    }
    values.update(updates)
    return AssistantCuration(**values)


def test_curated_filling_draft_keeps_source_findings_and_is_distinct_from_human_label():
    recipe = source()
    unchanged = recipe.model_dump_json()
    authored = curation(recipe)
    before = audit_preparation(recipe)
    after = audit_preparation(recipe, curation=authored)
    assert before.status == "review_required" and not before.additions
    assert after.status == "draft_supplement" and after.additions
    assert after.findings == before.findings
    assert after.assistant_curation["reviewer_kind"] == "assistant"
    assert after.human_review == "not_reviewed" and after.provider_calls == 0
    assert recipe.steps in supplemented_steps(recipe, after, curation=authored)
    assert recipe.model_dump_json() == unchanged
    assert after.revision_sha256 != before.revision_sha256


def test_curated_sidecar_is_not_accepted_without_the_selected_trusted_registry_record():
    recipe = source()
    result = audit_preparation(recipe, curation=curation(recipe))
    with pytest.raises(ValueError):
        supplemented_steps(recipe, result)


@pytest.mark.parametrize(
    "update",
    [
        {"recipe_id": "other"},
        {"source_name": "other"},
        {"source_fingerprint": "a" * 64},
        {"source_content_sha256": "b" * 64},
    ],
)
def test_wrong_or_stale_curation_is_rejected_not_silently_used(update):
    with pytest.raises(ValueError):
        audit_preparation(source(), curation=curation(**update))


def test_undeclared_addition_dependency_is_rejected():
    authored = curation(additions=[StepAddition("after_source", "花生加入。", ("花生",))])
    with pytest.raises(ValueError, match="undeclared"):
        audit_preparation(source(), curation=authored)


@pytest.mark.parametrize(
    "disposition", ["retain_component", "source_conflict", "needs_confirmation"]
)
def test_review_dispositions_do_not_invent_a_recipe_or_remove_the_source_gap(disposition):
    authored = curation(disposition=disposition, additions=[])
    result = audit_preparation(source(), curation=authored)
    assert result.status == "review_required"
    assert not result.additions and result.findings
    assert result.assistant_curation["disposition"] == disposition


def test_partial_draft_with_source_conflict_still_requires_review():
    authored = curation(disposition="source_conflict")
    result = audit_preparation(source(), curation=authored)
    assert result.additions and result.status == "review_required"
    assert "source_conflict" in result.assistant_curation["disposition"]


def test_clarification_is_labelled_separately_from_added_cooking_stages():
    authored = curation(
        additions=[
            StepAddition("clarification", "最后热处理建议采用烘烤，不是原文补回。", ("面粉",))
        ]
    )
    result = audit_preparation(source(), curation=authored)
    text = supplemented_steps(source(), result, curation=authored)
    assert "【助手补全草稿·做法澄清】" in text
    assert source().steps in text and "最后热处理" in text


def test_tag_curation_preserves_missing_completion_and_does_not_promote_generated_baking():
    authored = curation()
    recipe = source()
    result = audit_preparation(recipe, curation=authored)
    tags = tag_enrichment(recipe, result, curation=authored)
    assert "含豆沙的面团制品（缺最后烘烤）" in tags["tags_to_add_to_review_view"]
    assert tags["preparation_gap_blocks_verified_role_and_method_tags"]
    assert not any(t["dimension"] == "cooking_method" for t in tags["proposed_descriptive_tags"])
    assert not tags["automatically_applied"]
    assert tags["human_review"] == "not_reviewed"


def test_typo_and_claimed_human_approval_are_not_valid_registry_fields():
    with pytest.raises(ValidationError):
        curation(reviewer_kind="human")
    with pytest.raises(ValidationError):
        curation(human_approved=True)
    with pytest.raises(ValidationError):
        curation(disposition="approved_for_serving")


def test_forged_revision_and_modified_original_never_pass_bound_render():
    recipe = source()
    authored = curation(recipe)
    result = audit_preparation(recipe, curation=authored)
    with pytest.raises(ValueError):
        supplemented_steps(recipe, replace(result, revision_sha256="forged"), curation=authored)
    with pytest.raises(ValueError):
        audit_preparation(recipe.model_copy(update={"raw_label": "new"}), curation=authored)


def test_descriptive_only_curation_keeps_existing_conservative_bread_draft():
    recipe = next(
        iter(
            normalize_recipes(
                [
                    {
                        "名称": "白面包",
                        "食材清单": "面粉；水；酵母",
                        "label": "",
                        "烹饪步骤": "面粉、水、酵母揉成面团，发酵至两倍大。",
                    }
                ]
            ).values()
        )
    )
    before = audit_preparation(recipe)
    authored = curation(
        recipe,
        additions=[],
        tag_suggestions=[
            {
                "tag": "面团制品（原文缺烘烤）",
                "dimension": "culinary_form",
            }
        ],
    )
    after = audit_preparation(recipe, curation=authored)
    assert before.additions and after.additions == before.additions
    assert after.findings == before.findings and after.status == "draft_supplement"
    assert after.revision_sha256 != before.revision_sha256
    assert "烘烤" in supplemented_steps(recipe, after, curation=authored)


@pytest.mark.parametrize(
    "dimension", ["health_goal", "cooking_method", "culinary_role", "meal_type"]
)
def test_curation_cannot_claim_verified_method_meal_or_health_dimension(dimension):
    with pytest.raises(ValidationError):
        curation(tag_suggestions=[{"tag": "待核查", "dimension": dimension}])


@pytest.mark.parametrize(
    "update",
    [
        {"reason": " "},
        {"recipe_id": ""},
        {"source_name": " "},
        {"source_fingerprint": "x" * 64},
        {"policy_version": "other-v1"},
        {"tag_suggestions": [{"tag": " ", "dimension": "data_quality"}]},
        {"remaining_questions": [" "]},
        {"additions": [StepAddition("after_source", " ", ("面粉",))]},
        {"additions": [StepAddition("unknown", "整形。", ("面粉",))]},
        {"additions": [StepAddition("after_source", "整形。", (" ",))]},
    ],
)
def test_invalid_blank_or_unversioned_registry_values_fail_validation(update):
    with pytest.raises(ValidationError):
        curation(**update)


def test_addition_dict_schema_forbids_hidden_approval_field():
    with pytest.raises(ValidationError):
        curation(
            additions=[
                {
                    "placement": "after_source",
                    "text": "面团整形。",
                    "ingredient_names": ["面粉"],
                    "human_approved": True,
                }
            ]
        )


def test_json_registry_record_restores_frozen_typed_addition_and_exact_bound_view():
    recipe = source()
    authored = curation(recipe)
    restored = AssistantCuration.model_validate_json(authored.model_dump_json())
    assert restored == authored
    assert isinstance(restored.additions, tuple)
    assert isinstance(restored.additions[0], StepAddition)
    assert isinstance(restored.tag_suggestions, tuple)
    with pytest.raises(ValidationError):
        restored.reviewer_kind = "human"
    result = audit_preparation(recipe, curation=restored)
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False))["assistant_curation"] == (
        restored.model_dump(mode="json")
    )
    assert "包入豆沙馅" in supplemented_steps(recipe, result, curation=restored)


def test_tag_or_question_revision_requires_the_matching_selected_record():
    recipe = source()
    first = curation(recipe)
    second = curation(recipe, remaining_questions=["新增的复核疑问"])
    first_review = audit_preparation(recipe, curation=first)
    second_review = audit_preparation(recipe, curation=second)
    assert first_review.revision_sha256 != second_review.revision_sha256
    with pytest.raises(ValueError):
        supplemented_steps(recipe, first_review, curation=second)


def test_no_addition_curated_sidecar_and_forged_base_sidecar_are_still_canonically_checked():
    recipe = source()
    authored = curation(recipe, disposition="source_conflict", additions=[])
    result = audit_preparation(recipe, curation=authored)
    assert supplemented_steps(recipe, result, curation=authored) == recipe.steps
    with pytest.raises(ValueError):
        supplemented_steps(recipe, result)
    with pytest.raises(ValueError):
        supplemented_steps(recipe, replace(result, human_review="approved"), curation=authored)
    with pytest.raises(ValueError):
        supplemented_steps(recipe, replace(audit_preparation(recipe), findings=()))


def test_declared_source_conflict_blocks_role_and_method_claims_even_without_finite_gap():
    recipe = source().model_copy(update={"steps": "面团加入豆沙馅，蒸熟装盘。"})
    before = audit_preparation(recipe)
    assert not before.findings
    authored = curation(recipe, disposition="source_conflict", additions=[])
    review = audit_preparation(recipe, curation=authored)
    tags = tag_enrichment(recipe, review, curation=authored)
    assert review.findings == before.findings and review.status == "review_required"
    assert tags["preparation_gap_blocks_verified_role_and_method_tags"]
    assert all(
        tag["dimension"] not in {"cooking_method", "culinary_role"}
        for tag in tags["proposed_descriptive_tags"]
    )


def test_model_copy_shortcuts_cannot_bypass_registry_schema_at_audit_boundary():
    authored = curation().model_copy(update={"reviewer_kind": "human"})
    with pytest.raises(ValidationError):
        audit_preparation(source(), curation=authored)
