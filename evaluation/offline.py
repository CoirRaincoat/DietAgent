"""Local business-flow replay with manually annotated Intent fixtures.

This module NEVER creates a real LLM client. Annotations are test inputs, not
a parser or a natural-language understanding benchmark. Recipe choices are
made by the real agent, rules, retrieval and planner, never answer fixtures.
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter, deque
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from app.agent.service import MealAgent
from app.domain.models import Constraints, Intent, SessionState, UserProfile
from app.infrastructure.data import PROFILE_PATH, PROJECT_ROOT, RECIPE_PATH, load_catalog
from app.infrastructure.llm.base import BaseLLM
from app.infrastructure.sessions import SessionStore

ANNOTATED_SOURCE_SHA256 = "495201bfb121bc0e7389fe849a42ec183ff07c9e7ce71b0197e92532ba16c010"

# Human annotations for exactly the source version above. Every item is one
# turn. No menu, recipe ID, nutrition answer, or generated health fact is fixed.
ANNOTATIONS: dict[int, list[dict]] = {
    1: [{"meal_type": "晚餐"}],
    2: [{"meal_type": "早餐", "preferences": ["简单"]}],
    3: [{"meal_type": "午餐", "preferences": ["清爽", "夏季"]}],
    4: [{"meal_type": "晚餐", "people": 2, "preferences": ["清淡"]}],
    5: [{"meal_type": "午餐", "preferences": ["便当"]}],
    6: [{"meal_type": "晚餐", "max_minutes": 30}],
    7: [{"inventory": ["番茄", "鸡蛋", "土豆"], "query_terms": ["番茄", "鸡蛋", "土豆"]}],
    8: [{"meal_type": "晚餐", "dish_count": 2, "preferred_ingredients": ["面条"],
         "preferences": ["清淡"], "query_terms": ["面条"]}],
    9: [{"preferences": ["正式", "简单"]}],
    10: [{"meal_type": "夜宵", "preferences": ["热食"]}],
    11: [{"meal_type": "晚餐", "people": 4}],
    12: [{"dish_count": 5, "soup_count": 1, "preferences": ["多样搭配"]}],
    13: [{"preferences": ["热食", "清淡"]}],
    14: [{"dish_count": 5, "soup_count": 1, "no_spicy": True,
          "preferences": ["多样搭配", "软烂"]}],
    15: [{"meal_type": "晚餐"}, {"no_spicy": True, "preferences": ["清淡"]}],
    16: [{"meal_type": "午餐"}, {"people": 2, "health_goals": ["减脂"]}],
    # The current contract has a single time-limit field. This fixture follows
    # expected_final=10; it does not claim that “最好” is always a hard limit.
    17: [{"meal_type": "早餐"}, {"preferences": ["少甜"], "max_minutes": 10}],
    18: [{"action": "clarify", "clarification": "请说明这桌共几人用餐。"},
         {"people": 6, "preferences": ["正式", "简单"]}],
    19: [{"meal_type": "晚餐"},
         {"action": "clarify", "clarification": "当前数据不能验证补气血效果；可以按普通膳食搭配继续安排。"},
         {"preferences": ["家常"]}],
    20: [{"meal_type": "晚餐", "people": 2}, {"no_spicy": True},
         {"preferred_ingredients": ["鱼", "鸡翅"], "preferences": ["共享食材"]},
         {"max_minutes": 45}],
}


class FixtureLLM(BaseLLM):
    """A finite queue of human annotations; no network methods or fallback."""

    def __init__(self, annotations: list[dict]):
        self.intents = deque(Intent.model_validate(value) for value in annotations)

    async def parse(
        self, message: str, state: SessionState, profile: UserProfile
    ) -> Intent:
        if not self.intents:
            raise ValueError("No annotated Intent remains for this source turn")
        return self.intents.popleft().model_copy(deep=True)

    async def explain(self, facts: dict[str, str]) -> list[str]:
        return list(facts)

    async def aclose(self) -> None:
        pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def replay(config_path: Path | None = None, *, prepare_meal: bool = True) -> dict:
    """Replay unchanged source turns, optionally with declared harness context.

    prepare_meal=True preserves the downstream planner regression, explicitly
    preparing a zero-revision session before the first recorded source turn.
    False tests missing-context clarification using only the original inputs.
    """
    config_path = config_path or PROJECT_ROOT / "evaluation/dialogues.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source = PROJECT_ROOT / config["source"]
    source_hash = _sha256(source)
    if source_hash != ANNOTATED_SOURCE_SHA256:
        raise ValueError("Dialogue source changed; manually review and version Intent annotations")
    cases = json.loads(source.read_text(encoding="utf-8"))
    catalog = load_catalog()
    user_id = config["user_id"]
    meal_context = config.get("fixture_meal_context") if prepare_meal else None
    if prepare_meal and not meal_context:
        raise ValueError("Prepared replay requires explicit fixture_meal_context in config")
    results = []
    started = perf_counter()
    with TemporaryDirectory(prefix="diet-offline-replay-") as directory:
        for case in cases:
            case_id = case["id"]
            annotations = ANNOTATIONS[case_id]
            if len(annotations) != len(case["user_messages"]) or len(annotations) != case["turn_count"]:
                raise ValueError(f"Annotation count mismatch in case {case_id}")
            store = SessionStore(Path(directory) / f"case-{case_id}.sqlite3")
            llm = FixtureLLM(annotations)
            agent = MealAgent(catalog, store, llm)
            session_id = None
            if meal_context:
                profile = catalog.profiles[user_id]
                session_id = f"offline-prepared-{case_id}"
                prepared_state = SessionState(
                    session_id=session_id, user_id=user_id, revision=0,
                    constraints=Constraints(
                        people=meal_context["people"], meal_type=meal_context["meal_type"],
                        allergies=list(profile.allergies), preferences=list(profile.preferences),
                        health_goals=list(profile.health_goals),
                    ),
                    confirmed_fields=["people", "meal_type", "restrictions"],
                )
                store.save(prepared_state, None)
            turns = []
            failures = []
            state_checks = []
            final_state = None
            for number, message in enumerate(case["user_messages"], start=1):
                turn_started = perf_counter()
                try:
                    response = await agent.chat(
                        user_id, message, session_id=session_id,
                        request_id=f"offline-{case_id}-{number}",
                    )
                except Exception as error:
                    failures.append(f"turn {number}: execution failed ({type(error).__name__})")
                    break
                state = response.conversation_state
                final_state = state
                session_id = state.session_id
                if state.revision != number:
                    failures.append(f"turn {number}: revision did not advance exactly once")
                persisted = store.get(session_id, user_id)
                if persisted is None or persisted.model_dump() != state.model_dump():
                    failures.append(f"turn {number}: SQLite state differs from returned state")
                identity_checks = []
                for item in response.menu:
                    official = catalog.recipes.get(item.recipe_id)
                    valid = bool(
                        official is not None
                        and official.name == item.name
                        and official.steps == item.steps
                        and [ingredient.name for ingredient in official.ingredients] == item.ingredients
                    )
                    identity_checks.append({
                        "recipe_id": item.recipe_id, "name": item.name,
                        "source_row": official.source_row if official else None,
                        "fingerprint": official.fingerprint if official else None,
                        "passed": valid,
                    })
                    if not valid:
                        failures.append(f"turn {number}: recipe identity mismatch")
                if response.status == "ok":
                    if not state.menu_valid or len(response.menu) != state.constraints.dish_count:
                        failures.append(f"turn {number}: valid menu/count invariant failed")
                    if len({item.recipe_id for item in response.menu}) != len(response.menu):
                        failures.append(f"turn {number}: duplicate recipe identity")
                elif response.menu or state.menu_valid:
                    failures.append(f"turn {number}: unresolved result exposed a valid menu")
                turns.append({
                    "turn": number, "message": message,
                    "annotated_intent": annotations[number - 1],
                    "status": response.status, "reason": response.reason,
                    "warnings": response.warnings,
                    "constraints": state.constraints.model_dump(),
                    "revision": state.revision,
                    "confirmed_fields": state.confirmed_fields,
                    "pending_fields": state.pending_fields,
                    "menu_source_identity": identity_checks,
                    "tool_calls": [event.model_dump() for event in response.tool_calls],
                    "local_elapsed_seconds": round(perf_counter() - turn_started, 4),
                    "language_model": "local_human_intent_fixture",
                })
            expected = config.get("expected_final", {}).get(str(case_id), {})
            for key, value in expected.items():
                actual = getattr(final_state.constraints, key, None) if final_state else None
                passed = (
                    set(value).issubset(actual or [])
                    if isinstance(value, list) else actual == value
                )
                state_checks.append({"field": key, "expected": value, "actual": actual, "passed": passed})
                if not passed:
                    failures.append(f"final state assertion failed: {key}")
            if len(turns) != case["turn_count"] or llm.intents:
                failures.append("Not all source turns and annotated Intents were consumed")
            await llm.aclose()
            results.append({
                "case_id": case_id, "session_id": session_id, "turns": turns,
                "expected_state_assertions": state_checks, "assertion_failures": failures,
            })
    all_turns = [turn for result in results for turn in result["turns"]]
    identity_checks = [check for turn in all_turns for check in turn["menu_source_identity"]]
    return {
        "schema_version": "offline-business-replay.v1",
        "scope": "Human-annotated Intent inputs; local agent/rules/planner/SQLite regression only.",
        "limitations": [
            "Not an NLU evaluation, DeepSeek test, official accuracy score, or clinical audit.",
            "Menus are produced by the real planner; no answer menus are supplied.",
            "Qualitative preferences such as summer style, soft texture and portability are not fully assessed.",
            "Time limits request clarification; strict inventory includes seasonings.",
            "Original health profiles remain local; this module does not instantiate a network LLM.",
            "Prepared meal context is explicit test-harness input, not an original user statement.",
        ],
        "source_identity": {
            "config": str(config_path.relative_to(PROJECT_ROOT)),
            "config_sha256": _sha256(config_path),
            "dialogues": config["source"], "dialogues_sha256": source_hash,
            "profiles": str(PROFILE_PATH), "profiles_sha256": _sha256(PROJECT_ROOT / PROFILE_PATH),
            "recipes": str(RECIPE_PATH), "recipes_sha256": _sha256(PROJECT_ROOT / RECIPE_PATH),
            "user_id": user_id,
            "annotation_kind": "human_fixture",
            "prepared_meal_context": meal_context,
        },
        "summary": {
            "cases": len(results), "turns": len(all_turns),
            "expected_turns": sum(case["turn_count"] for case in cases),
            "prepared_meal_context": prepare_meal,
            "statuses": dict(Counter(turn["status"] for turn in all_turns)),
            "cases_with_assertion_failures": sum(bool(r["assertion_failures"]) for r in results),
            "expected_state_assertions": sum(len(r["expected_state_assertions"]) for r in results),
            "source_identity_checks": len(identity_checks),
            "source_identity_failures": sum(not check["passed"] for check in identity_checks),
            "time_limit_clarifications": sum(
                turn["status"] == "clarification_required" and turn["constraints"]["max_minutes"] is not None
                for turn in all_turns
            ),
            "strict_inventory_no_menu": sum(
                turn["status"] == "no_feasible_menu" and turn["constraints"]["inventory"] is not None
                for turn in all_turns
            ),
            "local_total_seconds": round(perf_counter() - started, 3),
        },
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "evaluation/dialogues.json")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "artifacts/offline_replay.json")
    parser.add_argument(
        "--unprepared", action="store_true",
        help="Do not prefill harness meal context; test clarification from original source inputs.",
    )
    args = parser.parse_args()
    report = asyncio.run(replay(args.config, prepare_meal=not args.unprepared))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if report["summary"]["cases_with_assertion_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()