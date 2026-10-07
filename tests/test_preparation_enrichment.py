"""Authored corpus-enrichment contrasts; no original private rows or AI calls."""

from dataclasses import replace

import pytest

from app.domain.preparation_enrichment import (
    StepAddition,
    audit_preparation,
    supplemented_steps,
    tag_enrichment,
)
from app.infrastructure.data import normalize_recipes


def sample(name: str, foods: str, steps: str):
    return next(
        iter(
            normalize_recipes(
                [{"名称": name, "食材清单": foods, "烹饪步骤": steps, "label": "晚餐"}]
            ).values()
        )
    )


def bowl():
    return sample(
        "蔬菜牛肉饭",
        "米饭200克；牛肉片100克；胡萝卜片20克；西兰花20克；水100克；生抽1克",
        "第1步：牛肉片和蔬菜焯水取出备用。；第2步：姜丝和蒜片煸炒。",
    )


@pytest.mark.parametrize("name", ["牛肉饭", "另一个名字的饭"])
def test_rice_draft_returns_main_food_and_finishes_declared_rice_assembly(name):
    recipe = bowl().model_copy(update={"name": name})
    before = recipe.model_dump_json()
    result = audit_preparation(recipe)
    assert result.status == "draft_supplement"
    assert result.findings == ("missing_rice_and_main_food_assembly",)
    assert len(result.additions) == 2
    text = supplemented_steps(recipe, result)
    assert "放回已备用的牛肉片" in text and "米饭盛入碗中" in text
    assert "不是原始菜谱" in text and "未改写" in text
    assert recipe.steps in text
    assert recipe.model_dump_json() == before
    assert result.human_review == "not_reviewed" and result.provider_calls == 0


@pytest.mark.parametrize(
    "tail",
    [
        "把牛肉和蔬菜放回锅中，米饭装碗，铺上牛肉和蔬菜。",
        "锅中倒入牛肉与蔬菜继续煮，出锅即可。",
        "米饭取出备用，继续准备配菜。",
    ],
)
def test_same_rice_title_does_not_force_automatic_additions(tail):
    recipe = bowl().model_copy(update={"steps": bowl().steps + "；第3步：" + tail})
    result = audit_preparation(recipe)
    assert result.additions == ()


def test_oil_in_aromatics_is_not_a_serving_action_that_hides_the_missing_assembly():
    recipe = sample(
        "牛肉饭",
        bowl().raw_ingredients + "；食用油2克",
        "第1步：牛肉片和蔬菜焯水，取出备用。；第2步：食用油、姜丝、蒜片煸炒。",
    )
    assert audit_preparation(recipe).status == "draft_supplement"


def test_complete_braising_program_is_not_misread_as_preparation_only():
    recipe = sample("香料牛肉", "牛肉；姜；水", "牛肉洗净，入锅设置酱香卤程序，取出切片。")
    assert audit_preparation(recipe).status == "keep_source"


@pytest.mark.parametrize("ingredient", ["海参", "黄芪", "未知酱料", "花生"])
def test_rice_draft_stays_manual_for_unsupported_extra_ingredients(ingredient):
    recipe = sample("蔬菜牛肉饭", bowl().raw_ingredients + "；" + ingredient, bowl().steps)
    assert audit_preparation(recipe).status == "review_required"
    assert audit_preparation(recipe).additions == ()


@pytest.mark.parametrize(
    "foods", ["红豆30克；薏米20克；水500克；红糖1克", "小米20克；水200克；白糖1克"]
)
def test_missing_porridge_cooking_is_added_before_not_after_final_mixing(foods):
    recipe = sample("豆谷粥", foods, "加入糖搅拌，取出食用。")
    result = audit_preparation(recipe)
    assert result.status == "draft_supplement"
    assert result.additions[0].placement == "before_source"
    assert "煮至豆谷软烂" in result.additions[0].text
    assert supplemented_steps(recipe, result).index("先淘洗") < supplemented_steps(
        recipe, result
    ).index(recipe.steps)


@pytest.mark.parametrize(
    "foods,steps",
    [
        ("熟红豆30克；熟薏米20克；水500克", "搅拌后取出。"),
        ("红豆30克；薏米20克；水500克", "红豆薏米熬煮后搅拌。"),
        ("红豆30克；薏米20克；水500克", "红豆薏米放入锅中，设置程序煮制。"),
    ],
)
def test_cooked_or_complete_porridge_is_preserved(foods, steps):
    assert audit_preparation(sample("豆谷粥", foods, steps)).status == "keep_source"


@pytest.mark.parametrize("foods", ["红豆30克；薏米20克", "红豆30克；水500克；党参2克"])
def test_water_or_drug_information_is_not_guessed(foods):
    result = audit_preparation(sample("豆谷粥", foods, "末段搅拌后取出。"))
    assert result.status == "review_required" and result.additions == ()


@pytest.mark.parametrize("name", ["白面包", "全麦欧包", "吐司"])
def test_plain_bread_has_own_shaping_proofing_and_baking_draft(name):
    recipe = sample(
        name,
        "高筋面粉100克；酵母1克；水70克；软化黄油5克",
        "第1步：高筋粉、酵母、水和黄油揉面至光滑，发酵至两倍大。",
    )
    result = audit_preparation(recipe)
    assert result.status == "draft_supplement"
    assert "整形" in result.additions[0].text
    assert "烘烤" in result.additions[0].text and "煮制" not in result.additions[0].text
    assert "温度、时间" in result.additions[0].text


@pytest.mark.parametrize(
    "ending", ["蒸熟。", "放入烤箱烘烤。", "煮熟。", "放入烤箱，180度20分钟，完成。"]
)
def test_already_completed_bread_is_not_duplicated(ending):
    recipe = sample("面包", "高筋面粉；酵母；水", "高筋面粉、酵母、水揉成面团。" + ending)
    assert audit_preparation(recipe).additions == ()


@pytest.mark.parametrize(
    "name,foods",
    [
        ("葡萄面包", "面粉；水；酵母；葡萄干"),
        ("酥皮点心", "面粉；水；黄油；豆沙"),
        ("药膳面包", "面粉；水；酵母；党参"),
    ],
)
def test_missing_fillings_complex_pastry_and_herbs_need_review(name, foods):
    result = audit_preparation(sample(name, foods, "面粉、水、酵母揉成面团。"))
    assert result.status == "review_required" and result.additions == ()


@pytest.mark.parametrize(
    "steps", ["", "第1步：1", "第2步：面团取出。", "第1步：面团取出。；第3步：继续揉面。"]
)
def test_missing_placeholder_or_numbered_sections_cannot_be_fluent_filled(steps):
    result = audit_preparation(sample("面包", "面粉；水；酵母", steps))
    assert result.status == "review_required" and result.additions == ()


@pytest.mark.parametrize(
    "name,foods,steps",
    [
        ("发酵面团", "面粉；水；酵母", "面粉、水、酵母揉成面团。"),
        ("凉拌黄瓜", "黄瓜；盐", "黄瓜洗净切块，盐拌匀装盘。"),
        ("果蔬饮", "苹果；水", "苹果洗净切块，加水打碎。"),
        ("坚果饮", "腰果；牛奶", "腰果洗净，加牛奶研磨。"),
        ("蒸米饭", "大米；水", "大米加水蒸熟。"),
    ],
)
def test_components_cold_food_and_blended_drinks_are_not_invented_hot_meals(name, foods, steps):
    recipe = sample(name, foods, steps)
    result = audit_preparation(recipe)
    assert result.additions == () and result.status == "keep_source"
    assert supplemented_steps(recipe, result) == steps


@pytest.mark.parametrize("change", ["id", "fingerprint", "steps", "ingredients", "review", "raw"])
def test_sidecar_rejects_stale_forged_or_undeclared_draft(change):
    recipe = bowl()
    review = audit_preparation(recipe)
    if change == "id":
        recipe = recipe.model_copy(update={"recipe_id": "different"})
    elif change == "fingerprint":
        recipe = recipe.model_copy(update={"fingerprint": "changed"})
    elif change == "steps":
        recipe = recipe.model_copy(update={"steps": recipe.steps + " changed"})
    elif change == "ingredients":
        review = replace(review, additions=(StepAddition("after_source", "添加花生", ("花生",)),))
    elif change == "raw":
        recipe = recipe.model_copy(update={"raw_ingredients": recipe.raw_ingredients + "；花生"})
    else:
        review = replace(review, revision_sha256="forged")
    with pytest.raises(ValueError):
        supplemented_steps(recipe, review)


def test_deterministic_identity_and_serializable_recipe_specific_sidecars():
    recipe = bowl()
    result = audit_preparation(recipe)
    assert result == audit_preparation(recipe.model_copy(deep=True))
    assert result.to_dict()["source_fingerprint"] == recipe.fingerprint
    assert len(result.revision_sha256) == 64
    assert (
        audit_preparation(recipe.model_copy(update={"steps": recipe.steps + "。"})).revision_sha256
        != result.revision_sha256
    )


@pytest.mark.parametrize(
    "name,foods,steps,expected",
    [
        ("蒸米饭", "大米；水", "大米放入锅中蒸熟。", "主食类"),
        ("蒸白菜", "白菜；盐", "白菜蒸熟装盘。", "蔬菜类成菜"),
        ("蒸鸡肉", "鸡胸肉；盐", "鸡胸肉蒸熟装盘。", "肉蛋豆类成菜"),
        ("蔬菜汤", "白菜；水；盐", "白菜加水煮制。", "汤羹类"),
    ],
)
def test_missing_tags_get_descriptive_not_clinical_candidates(name, foods, steps, expected):
    recipe = sample(name, foods, steps).model_copy(update={"raw_label": "", "labels": []})
    tags = tag_enrichment(recipe, audit_preparation(recipe))
    assert expected in tags["tags_to_add_to_review_view"]
    assert tags["source_raw_label"] == ""
    assert not tags["automatically_applied"] and tags["human_review"] == "not_reviewed"


def test_source_health_tags_are_preserved_but_not_promoted_as_verified_facts():
    recipe = sample("蒸白菜", "白菜；盐", "白菜蒸熟装盘。").model_copy(
        update={"raw_label": "晚餐、清淡、护心、降压、儿童、高蛋白", "labels": ["晚餐", "清淡"]}
    )
    tags = tag_enrichment(recipe, audit_preparation(recipe))
    assert tags["source_unverified_tokens"] == ["护心", "降压", "儿童", "高蛋白"]
    assert tags["source_reference_tags"] == ["晚餐", "清淡"]
    assert not {"护心", "降压", "儿童", "高蛋白"}.intersection(tags["tags_to_add_to_review_view"])
    assert recipe.raw_label == "晚餐、清淡、护心、降压、儿童、高蛋白"


def test_generated_baking_is_not_presented_as_an_observed_original_method_tag():
    recipe = sample("面包", "面粉；水；酵母", "面粉、水、酵母揉成面团。")
    review = audit_preparation(recipe)
    assert review.additions
    tags = tag_enrichment(recipe, review)
    assert tags["preparation_gap_blocks_verified_role_and_method_tags"]
    assert tags["proposed_descriptive_tags"] == []


def test_bread_draft_does_not_repeat_source_completed_first_proofing():
    recipe = sample("吐司", "面粉；水；酵母", "面粉、水、酵母揉成面团，发酵至两倍大。")
    text = audit_preparation(recipe).additions[0].text
    assert "先完成一次发酵" not in text and "末次醒发" in text and "吐司模具" in text


def test_beef_draft_does_not_invent_leftover_water_after_source_drain():
    result = audit_preparation(bowl())
    assert not any("留用的水" in addition.text for addition in result.additions)


@pytest.mark.parametrize("extra", ["党参片", "生猪肉", "未知品牌调味料"])
def test_a_mentioned_unusual_ingredient_does_not_get_plain_bread_finishing(extra):
    recipe = sample("面包", "面粉；水；酵母；" + extra, "面粉、水、酵母、" + extra + "揉成面团。")
    assert audit_preparation(recipe).status == "review_required"
    assert audit_preparation(recipe).additions == ()
