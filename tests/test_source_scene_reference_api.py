"""Actual scripted HTTP uptake, disclosure and retry; not real model understanding."""

from app.domain.models import Intent, UserProfile
from app.infrastructure.data import DataCatalog
from tests.test_agent_api import ScriptedLLM, client_for, complete_intent
from tests.test_component_slots import catalog, dish


def test_append_scene_uses_source_reference_without_source_edit_and_exact_retry(tmp_path):
    old = dish("蒸白菜", "白菜200克；盐1克", "白菜蒸熟装盘。")
    reviewed = catalog()[356]
    data = DataCatalog(
        profiles={
            3: UserProfile(
                data_scope="synthetic",
                user_id=3,
                age=30,
                sex="女",
                height_cm=165,
                weight_kg=55,
                bmi=20.2,
            )
        },
        recipes={r.recipe_id: r for r in (old, reviewed)},
        quality_report={},
    )
    source_before = reviewed.model_dump_json()
    llm = ScriptedLLM(
        [
            complete_intent(dish_count=1, soup_count=0, meal_type="午餐", query_terms=["蒸白菜"]),
            Intent(action="plan"),
            Intent(),
        ]
    )
    with client_for(tmp_path, data, llm) as client:
        first = client.post(
            "/chat",
            json=dict(
                user_id=3,
                request_id="scene-first",
                message="1人午餐，1道菜，没有其他忌口，先安排蒸白菜。",
            ),
        )
        assert first.status_code == 200 and first.json()["menu"][0]["recipe_id"] == old.recipe_id
        body = dict(
            user_id=3,
            session_id=first.json()["conversation_state"]["session_id"],
            request_id="scene-second",
            message="这餐做便当，其他要求不变。",
        )
        second = client.post("/chat", json=body).json()
        assert second["status"] == "ok"
        assert [item["recipe_id"] for item in second["menu"]] == [reviewed.recipe_id]
        assert "助手补充" in second["reason"] and "未经独立复核" in second["reason"]
        assert "便当" not in reviewed.labels and "便当" not in reviewed.raw_label
        assert "便当" not in second["menu"][0]["card"]["badges"]
        assert client.post("/chat", json=body).json() == second
        continued = client.post(
            "/chat", json={**body, "request_id": "scene-third", "message": "继续。"}
        ).json()
        assert [item["recipe_id"] for item in continued["menu"]] == [reviewed.recipe_id]
    assert llm.parse_calls == 3
    assert reviewed.model_dump_json() == source_before
