"""Keep old offline algorithm contrasts reproducible, not production defaults.

The five historical probe modules intentionally test the pre-migration proxy
policy. Current native/API/menu/safety tests continue to import the real engine.
This fixture is not used by application or evaluation entry points.
"""

import json
from pathlib import Path

from app.rules.engine import RuleEngine


class HistoricalHealthRuleEngine(RuleEngine):
    def __init__(self, config_path=None, *, experiment_category_scope=False):
        super().__init__(
            config_path, experiment_category_scope=experiment_category_scope
        )
        if config_path is None:
            fixture = Path(__file__).parent / "fixtures/historical_health_rules_v4.json"
            self.config["health_goals"] = json.loads(
                fixture.read_text(encoding="utf-8")
            )["health_goals"]
