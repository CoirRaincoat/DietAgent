"""Synthetic regressions for source-ordered cooking-method explanations."""

import json
import os
import subprocess
import sys
from textwrap import dedent

import pytest

from app.agent.menu_balance import analyze_menu_balance, balance_rank, balance_summary
from app.domain.models import Ingredient, Recipe
from app.infrastructure.data import PROJECT_ROOT


def recipe(identity: str, methods: list[str]) -> Recipe:
    action = methods[0] + ("匀" if methods[0] == "拌" else "熟") if methods else "处理"
    return Recipe(
        recipe_id=identity, name="合成配菜" + identity, source_row=1, fingerprint=identity,
        raw_ingredients="合成蔬菜", ingredients=[Ingredient(raw="合成蔬菜", name="合成蔬菜")],
        steps=action + "后装盘。", categories=["vegetable"], methods=methods,
    )


def test_method_summary_preserves_the_first_source_occurrence_without_changing_recipes():
    menu = [recipe("synthetic-" + str(index), [method])
            for index, method in enumerate(["蒸", "炒", "拌", "煮"])]
    original = [item.model_dump() for item in menu]

    balance = analyze_menu_balance(menu)

    assert list(balance.method_counts) == ["蒸", "炒", "拌", "煮"]
    assert balance_summary(balance) == "搭配上包含4 道蔬菜类菜，做法有蒸、炒、拌、煮。"
    assert [item.model_dump() for item in menu] == original


def test_method_counts_are_per_dish_and_duplicate_tags_do_not_change_ranking():
    menu = [recipe("synthetic-a", ["蒸", "蒸", "炒"]),
            recipe("synthetic-b", ["炒", "蒸", "拌"]),
            recipe("synthetic-c", ["拌", "煮", "蒸"])]
    without_duplicates = [recipe("synthetic-a", ["蒸", "炒"]), *menu[1:]]

    balance = analyze_menu_balance(menu)

    assert balance.method_counts == {"蒸": 1, "炒": 1, "拌": 1}
    assert list(balance.method_counts) == ["蒸", "炒", "拌"]
    assert balance.score == 70
    assert balance_rank(menu) == balance_rank(without_duplicates)
    assert balance_summary(balance) == "搭配上包含3 道蔬菜类菜，做法有蒸、炒、拌。"


def test_four_method_display_limit_keeps_the_remaining_source_metadata():
    methods = ["煮", "烤", "蒸", "炒", "拌", "炖"]
    menu = [recipe("synthetic-" + str(index), [method]) for index, method in enumerate(methods)]
    menu.append(recipe("synthetic-unknown", []))

    balance = analyze_menu_balance(menu)

    assert list(balance.method_counts) == methods
    assert balance_summary(balance) == "搭配上包含7 道蔬菜类菜，做法有煮、烤、蒸、炒。"
    assert [item.methods for item in menu] == [[method] for method in methods] + [[]]
    assert balance.method_counts["拌"] == balance.method_counts["炖"] == 1


PROCESS_SCENARIO = dedent("""
    import json
    import socket
    import sys
    from pathlib import Path
    import httpx

    counts = dict(http=0, socket=0, dns=0)
    def forbidden(kind):
        def block(*args, **kwargs):
            counts[kind] += 1
            raise AssertionError("No networking in a synthetic method-order regression")
        return block
    httpx.HTTPTransport.handle_request = forbidden("http")
    httpx.AsyncHTTPTransport.handle_async_request = forbidden("http")
    socket.socket.connect = forbidden("socket")
    socket.socket.connect_ex = forbidden("socket")
    socket.getaddrinfo = forbidden("dns")

    import app
    from app.agent.menu_balance import analyze_menu_balance, balance_rank, balance_summary
    from app.domain.models import Recipe

    assert Path(app.__file__).resolve().parent == Path.cwd() / "app"
    menu = [Recipe.model_validate(value) for value in json.load(sys.stdin)]
    original = [value.model_dump(mode="json") for value in menu]
    balance = analyze_menu_balance(menu)
    assert [value.model_dump(mode="json") for value in menu] == original
    print(json.dumps(dict(summary=balance_summary(balance),
                          ordered_methods=list(balance.method_counts),
                          counts=balance.method_counts, score=balance.score,
                          rank=balance_rank(menu), guard=counts), ensure_ascii=True))
""")


@pytest.mark.parametrize("hash_seed", ["0", "1", "2", "7", "23", "random"])
def test_method_explanation_is_identical_across_process_hash_seeds(hash_seed: str):
    menu = [recipe("synthetic-a", ["蒸", "蒸", "炒"]),
            recipe("synthetic-b", ["炒", "蒸", "拌"]),
            recipe("synthetic-c", ["拌", "煮", "蒸"])]
    environment = os.environ.copy()
    environment.update(PYTHONHASHSEED=hash_seed, PYTHONDONTWRITEBYTECODE="1",
                       PYTHONUTF8="1", DEEPSEEK_API_KEY="")
    process = subprocess.run(
        [sys.executable, "-B", "-X", "utf8", "-c", PROCESS_SCENARIO], cwd=PROJECT_ROOT,
        input=json.dumps([item.model_dump(mode="json") for item in menu], ensure_ascii=True),
        capture_output=True, text=True, encoding="utf-8", env=environment, timeout=30,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {
        "summary": "搭配上包含3 道蔬菜类菜，做法有蒸、炒、拌。",
        "ordered_methods": ["蒸", "炒", "拌"],
        "counts": {"蒸": 1, "炒": 1, "拌": 1}, "score": 70,
        "rank": [1, 0, 0, 0, 1, 0, 0, 3, 3, 1, 0, 0, 0],
        "guard": {"http": 0, "socket": 0, "dns": 0},
    }


def test_raw_method_tags_cannot_invent_cooking_actions_in_a_deterministic_summary():
    item = recipe("synthetic-conflicting-tags", ["蒸", "炒", "拌", "煮"])
    before = item.model_dump()
    assert analyze_menu_balance([item]).method_counts == {"蒸": 1}
    assert balance_summary(analyze_menu_balance([item])) == "搭配上包含1 道蔬菜类菜，做法有蒸。"
    assert item.model_dump() == before
