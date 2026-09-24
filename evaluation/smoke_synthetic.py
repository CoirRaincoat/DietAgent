"""Live five-turn Demo using authored profiles/messages and the recipe CSV only."""

import asyncio
import json
import tempfile
from pathlib import Path
from time import perf_counter

import httpx

from app.api.main import create_app
from app.infrastructure.data import PROJECT_ROOT
from app.infrastructure.sessions import SessionStore
from app.infrastructure.settings import Settings
from app.infrastructure.synthetic import load_synthetic_catalog


async def run() -> dict:
    catalog = load_synthetic_catalog()
    recipes = catalog.recipes
    settings = Settings()
    report = {"data_scope": "synthetic profiles; authored messages; recipe CSV only",
              "model": settings.deepseek_model, "turns": []}
    with tempfile.TemporaryDirectory(prefix="diet-agent-synthetic-") as temporary:
        app = create_app(
            settings=settings, catalog=catalog,
            store=SessionStore(Path(temporary) / "sessions.sqlite3"),
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://synthetic.local"
            ) as client:
                session_id = None
                previous_ids = None
                messages = [
                    ("帮我安排一餐。", "clarification_required"),
                    ("2个人，晚餐。", "clarification_required"),
                    ("没有其他忌口，不吃辣椒，安排三道菜。", "ok"),
                    ("只替换第二道菜，其他保持不变。", "ok"),
                    ("解释刚才这份菜单。", "ok"),
                ]
                for number, (message, expected) in enumerate(messages, 1):
                    request = {"user_id": 900001, "message": message,
                               "request_id": f"synthetic-demo-{number}"}
                    if session_id:
                        request["session_id"] = session_id
                    started = perf_counter()
                    response = await client.post("/chat", json=request)
                    body = response.json()
                    elapsed = round(perf_counter() - started, 3)
                    report["turns"].append({"turn": number, "http_status": response.status_code,
                                            "elapsed_seconds": elapsed, "response": body})
                    if response.status_code != 200 or body.get("status") != expected:
                        report["passed"] = False
                        break
                    state = body["conversation_state"]
                    session_id = state["session_id"]
                    assert state["revision"] == number
                    if expected == "clarification_required":
                        assert body["menu"] == [] and body["tool_calls"] == []
                        assert body["nutrition_analysis"] is None
                        fields = [question["field"] for question in body["clarification_questions"]]
                        assert fields == (["people", "meal_type", "restrictions"] if number == 1 else ["restrictions"])
                    else:
                        ids = [item["recipe_id"] for item in body["menu"]]
                        assert len(ids) == 3 and len(set(ids)) == 3
                        assert all(key in recipes for key in ids)
                        assert state["constraints"]["people"] == 2
                        assert state["constraints"]["meal_type"] == "晚餐"
                        assert not any("辣椒" in recipes[key].raw_ingredients for key in ids)
                        assert body["nutrition_analysis"]["recipe_ids"] == ids
                        assert body["nutrition_analysis"]["analysis_type"] == "qualitative"
                        assert body["explanation_source"] == "deepseek_verified_facts"
                        for item in body["menu"]:
                            assert item["card"]["title"] == recipes[item["recipe_id"]].name
                            assert item["card"]["image_url"] is None
                            assert item["cooking_steps"] and item["ingredient_details"]
                            assert item["provenance"]["fingerprint"] == recipes[item["recipe_id"]].fingerprint
                        if number == 4:
                            assert ids[0] == previous_ids[0] and ids[2] == previous_ids[2]
                            assert ids[1] != previous_ids[1]
                        if number == 5:
                            assert ids == previous_ids
                            assert "menu_modify" not in [event["name"] for event in body["tool_calls"]]
                        previous_ids = ids
                    print(json.dumps({
                        "turn": number, "status": body["status"], "seconds": elapsed,
                        "menu": [item["name"] for item in body["menu"]],
                        "explanation_source": body["explanation_source"],
                    }, ensure_ascii=False), flush=True)
                else:
                    report["passed"] = True
    return report


def main() -> None:
    report = asyncio.run(run())
    output = PROJECT_ROOT / "artifacts/synthetic_live.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "turns": len(report["turns"])}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
