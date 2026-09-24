"""Exercise the HTTP demo using only fixed, hand-authored synthetic inputs.

This CLI never imports project profile loaders or reads datasets, .env, or API
keys. It talks to the configured HTTP service and writes a compact audit result.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

USER_ID = 900001
MESSAGES = (
    "帮我推荐一餐。",
    "两个人吃晚餐。",
    "没有其他忌口，不吃辣，一共三道菜，不要汤。",
    "只换第二道菜，其他菜保留。",
    "解释一下这份菜单，不要调整菜品。",
)
KNOWN_TOOLS = {"recipe_search", "health_check", "menu_modify", "nutrition_analysis"}


class DemoFailure(Exception):
    """A fixed, non-sensitive check label suitable for the audit report."""


def require(condition: bool, label: str) -> None:
    if not condition:
        raise DemoFailure(label)


def get_json(client: httpx.Client, path: str) -> dict:
    response = client.get(path)
    require(response.status_code == 200, f"GET {path} did not return HTTP 200")
    result = response.json()
    require(isinstance(result, dict), f"GET {path} did not return a JSON object")
    return result


def verify_dish(item: dict) -> None:
    recipe_id = item["recipe_id"]
    require(bool(recipe_id) and bool(item["name"]), "Dish identity is missing")
    require(item["card"]["title"] == item["name"], "Card title differs from the dish name")
    require(
        all(item["card"][field] is None for field in ("image_url", "cooking_minutes", "servings")),
        "An unsupported card image, time, or serving estimate was supplied",
    )
    provenance = item["provenance"]
    require(provenance["recipe_id"] == recipe_id, "Recipe provenance identity differs")
    require(provenance["source_row"] >= 2, "Recipe provenance row is invalid")
    require(bool(provenance["fingerprint"]), "Recipe provenance fingerprint is missing")
    require(
        [ingredient["name"] for ingredient in item["ingredient_details"]] == item["ingredients"],
        "Ingredient details disagree with the ingredient list",
    )
    source_lines = [line for line in item["steps"].splitlines() if line.strip()]
    require(bool(source_lines), "Cooking instructions are empty")
    require(
        [step["description"] for step in item["cooking_steps"]] == source_lines,
        "Cooking step presentation changed source text",
    )
    nutrition = item["nutrition"]
    require(nutrition["analysis_type"] == "qualitative", "Dish nutrition is not qualitative")
    require(nutrition["recipe_id"] == recipe_id, "Dish nutrition identity differs")
    require(
        all(
            contribution["ingredient_name"] in item["ingredients"]
            and contribution["recipe_id"] == recipe_id
            for contribution in nutrition["ingredient_contributions"]
        ),
        "Nutrition contributions cannot be traced to this dish's listed ingredients",
    )


def verify_planned_result(result: dict) -> list[str]:
    require(result["status"] == "ok", "Expected a completed menu")
    require(result["schema_version"] == "2.0", "Unexpected response schema version")
    menu = result["menu"]
    require(len(menu) == 3, "The synthetic request must produce three menu items")
    ids = [item["recipe_id"] for item in menu]
    require(len(set(ids)) == 3, "The menu contains duplicate recipe IDs")
    for item in menu:
        verify_dish(item)
    state = result["conversation_state"]
    require(state["menu_ids"] == ids and state["menu_valid"], "Menu and session disagree")
    constraints = state["constraints"]
    require(constraints["people"] == 2, "The two-person requirement was not retained")
    require(constraints["meal_type"] == "晚餐", "The dinner requirement was not retained")
    require(constraints["no_spicy"] is True, "The no-spicy requirement was not retained")
    require(
        constraints["dish_count"] == 3 and constraints["soup_count"] == 0,
        "The requested menu structure was not retained",
    )
    require(
        set(state["confirmed_fields"]) == {"people", "meal_type", "restrictions"},
        "Required context has not been confirmed",
    )
    require(not state["pending_fields"], "Completed menu still has pending questions")
    require(
        result["nutrition_analysis"]["recipe_ids"] == ids
        and result["nutrition_analysis"]["analysis_type"] == "qualitative",
        "Menu nutrition does not match the selected recipes",
    )
    for suggestion in result["replacement_suggestions"]:
        verify_dish(suggestion)
        require(suggestion["slot"] == 1, "Replacement suggestion target is unclear")
        require(bool(suggestion["replacement_reason"]), "Replacement suggestion has no reason")
        require(
            suggestion["recipe_id"] not in ids
            and suggestion["name"] not in {item["name"] for item in menu},
            "Replacement suggestion repeats a selected dish",
        )
    return ids


def run_demo(base_url: str, report: dict) -> None:
    run_id = uuid4().hex
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=90, trust_env=False) as client:
        health = get_json(client, "/health")
        require(health["status"] == "ok", "Health status is not ok")
        require(health["profile_count"] == 3, "Expected exactly three synthetic profiles")
        require(health["profile_data_scope"] == ["synthetic"], "Service exposes a non-synthetic scope")
        require(health["llm_configured"], "The service has no configured model")
        require(set(health["tools"]) == KNOWN_TOOLS, "Unexpected registered tools")
        profiles = get_json(client, "/demo/profiles")
        require(profiles["data_scope"] == "synthetic", "Demo profile scope is not synthetic")
        require(
            {profile["user_id"] for profile in profiles["profiles"]} == {900001, 900002, 900003},
            "Unexpected synthetic profile IDs",
        )
        api = get_json(client, "/openapi.json")
        require(api["info"]["version"] == "0.3.0", "Unexpected OpenAPI application version")
        report["service"] = {
            "healthy": True, "profile_data_scope": "synthetic",
            "profile_count": health["profile_count"], "recipe_count": health["recipe_count"],
            "llm_configured": True, "api_version": api["info"]["version"],
            "registered_tool_count": len(health["tools"]),
        }
        session_id = None
        previous_ids: list[str] = []
        for index, message in enumerate(MESSAGES, start=1):
            payload = {
                "user_id": USER_ID, "message": message,
                "request_id": f"demo-http-{run_id}-{index}",
            }
            if session_id:
                payload["session_id"] = session_id
            started = perf_counter()
            response = client.post("/chat", json=payload)
            elapsed = round(perf_counter() - started, 3)
            turn_report = {
                "turn": index, "http_status": response.status_code, "elapsed_seconds": elapsed,
            }
            report["turns"].append(turn_report)
            require(response.status_code == 200, f"Turn {index} did not return HTTP 200")
            result = response.json()
            require(isinstance(result, dict), f"Turn {index} did not return a JSON object")
            state = result["conversation_state"]
            if session_id is None:
                session_id = state["session_id"]
            require(state["session_id"] == session_id, "The conversation changed session")
            require(state["user_id"] == USER_ID, "The conversation changed synthetic user")
            require(state["revision"] == index, "Unexpected conversation revision")
            tools = [event["name"] for event in result["tool_calls"]]
            require(set(tools) <= KNOWN_TOOLS, "A response reported an unregistered tool")
            turn_report.update({
                "status": result["status"], "menu_count": len(result["menu"]),
                "clarification_count": len(result["clarification_questions"]),
                "tool_call_count": len(tools),
                "suggestion_count": len(result["replacement_suggestions"]),
                "explanation_source": result["explanation_source"],
            })
            if index <= 2:
                require(result["status"] == "clarification_required", "Missing context was not clarified")
                require(not result["menu"] and not tools, "Planning ran before context was complete")
                fields = {question["field"] for question in result["clarification_questions"]}
                expected = {"people", "meal_type", "restrictions"} if index == 1 else {"restrictions"}
                require(fields == expected, "The service asked unexpected context questions")
            else:
                ids = verify_planned_result(result)
                if index == 3:
                    require(
                        KNOWN_TOOLS <= set(tools), "Initial planning did not execute the expected tools",
                    )
                elif index == 4:
                    require(
                        ids[0] == previous_ids[0] and ids[2] == previous_ids[2]
                        and ids[1] != previous_ids[1],
                        "Replacing slot two changed other slots or did not replace slot two",
                    )
                    require("menu_modify" in tools, "Replacement did not invoke menu_modify")
                else:
                    require(ids == previous_ids, "Explanation changed the selected menu")
                    require(
                        not {"recipe_search", "menu_modify"}.intersection(tools),
                        "Explanation performed retrieval or replanning",
                    )
                    require("health_check" in tools, "Explanation skipped current-menu verification")
                previous_ids = ids
            turn_report["passed"] = True
            print(json.dumps(turn_report, ensure_ascii=False), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--output", type=Path, default=Path("artifacts/docker_demo_http.json"))
    args = parser.parse_args(argv)
    started = perf_counter()
    report = {
        "data_scope": "synthetic", "user_id": USER_ID,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "passed": False, "turns": [],
    }
    try:
        url = httpx.URL(args.base_url)
        require(url.scheme in {"http", "https"} and bool(url.host), "Invalid HTTP base URL")
        require(not url.username and not url.password, "Base URL must not contain credentials")
        run_demo(args.base_url, report)
        report["passed"] = True
    except DemoFailure as error:
        report["failure"] = str(error)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as error:
        report["failure"] = f"HTTP demo could not complete: {type(error).__name__}"
    report["elapsed_seconds"] = round(perf_counter() - started, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": report["passed"], "turn_count": len(report["turns"]),
        "elapsed_seconds": report["elapsed_seconds"],
        **({"failure": report["failure"]} if "failure" in report else {}),
    }, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
