"""Replay supplied scenarios against a running API; requires real DeepSeek access.

This reports transport, source-identity and explicit state assertions. It is
not an official accuracy score or an independent clinical safety audit.
"""

import argparse
import asyncio
import json
import platform
from collections import Counter
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

import httpx

from app.infrastructure.data import PROJECT_ROOT, load_catalog


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


async def replay(args: argparse.Namespace) -> dict:
    config = json.loads(args.config.read_text(encoding="utf-8"))
    source = PROJECT_ROOT / config["source"]
    cases = json.loads(source.read_text(encoding="utf-8"))
    if args.limit:
        cases = cases[:args.limit]
    catalog = load_catalog()
    user_id = args.user_id or config["user_id"]
    run_id = uuid4().hex
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=90) as client:
        health_response = await client.get("/health")
        health_response.raise_for_status()
        health = health_response.json()

        async def run_case(case: dict) -> dict:
            async with semaphore:
                turns = []
                session_id = None
                failures = []
                for number, message in enumerate(case["user_messages"], start=1):
                    payload = {
                        "user_id": user_id, "message": message,
                        "request_id": f"{run_id}-{case['id']}-{number}",
                    }
                    if session_id:
                        payload["session_id"] = session_id
                    started = perf_counter()
                    try:
                        response = await client.post("/chat", json=payload)
                        body = response.json()
                        elapsed = perf_counter() - started
                        turns.append({
                            "turn": number, "message": message,
                            "http_status": response.status_code,
                            "elapsed_seconds": round(elapsed, 3), "response": body,
                        })
                        if response.status_code != 200:
                            failures.append(f"turn {number}: HTTP {response.status_code}")
                            break
                        session_id = body["conversation_state"]["session_id"]
                        for item in body["menu"]:
                            recipe = catalog.recipes.get(item["recipe_id"])
                            if recipe is None or recipe.name != item["name"]:
                                failures.append(f"turn {number}: source identity mismatch")
                        if body["status"] == "ok":
                            state = body["conversation_state"]
                            if not state["menu_valid"]:
                                failures.append(f"turn {number}: menu was not marked valid")
                            if len(body["menu"]) != state["constraints"]["dish_count"]:
                                failures.append(f"turn {number}: dish count mismatch")
                        elif body["menu"]:
                            failures.append(f"turn {number}: unresolved result exposed a menu")
                    except (httpx.HTTPError, ValueError, KeyError) as error:
                        failures.append(f"turn {number}: {type(error).__name__}")
                        break
                expected = config.get("expected_final", {}).get(str(case["id"]), {})
                if turns and turns[-1]["http_status"] == 200:
                    constraints = turns[-1]["response"]["conversation_state"]["constraints"]
                    for key, value in expected.items():
                        actual = constraints.get(key)
                        valid = set(value).issubset(actual or []) if isinstance(value, list) else actual == value
                        if not valid:
                            failures.append(f"final state {key}: expected {value!r}, got {actual!r}")
                result = {"case_id": case["id"], "turns": turns, "assertion_failures": failures}
                print(json.dumps({
                    "case_id": case["id"], "turns": len(turns), "failures": failures,
                    "statuses": [turn["response"].get("status", "error") for turn in turns],
                }, ensure_ascii=False), flush=True)
                return result

        results = await asyncio.gather(*(run_case(case) for case in cases))
    turns = [turn for result in results for turn in result["turns"]]
    latencies = [turn["elapsed_seconds"] for turn in turns]
    return {
        "scope": "Original scenario replay with explicit contract assertions; not an official score.",
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "concurrency": args.concurrency, "api_health": health, "user_id": user_id,
        },
        "summary": {
            "cases": len(results), "turns": len(turns),
            "statuses": dict(Counter(turn["response"].get("status", "error") for turn in turns)),
            "http_errors": sum(turn["http_status"] != 200 for turn in turns),
            "cases_with_assertion_failures": sum(bool(result["assertion_failures"]) for result in results),
            "latency_mean_seconds": round(mean(latencies), 3) if latencies else 0,
            "latency_p50_seconds": percentile(latencies, 0.5),
            "latency_p95_seconds": percentile(latencies, 0.95),
        },
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "evaluation/dialogues.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/replay.json")
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=3)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = asyncio.run(replay(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["http_errors"] or report["summary"]["cases_with_assertion_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
