"""Explicit local official-ID loading; no dialogue input or demo fallback."""

import hashlib
import json
from pathlib import Path

from app.infrastructure.data import PROJECT_ROOT, DataCatalog, normalize_profile
from app.infrastructure.synthetic import load_synthetic_catalog


def load_runtime_catalog(local_profile_path: Path | None = None) -> DataCatalog:
    """Keep demo default; an explicit local file must contain IDs 1 through 50.

    Original profiles remain original, including redacted source profiles.
    Loading never authorizes sending profile contents upstream. All errors are startup errors,
    never silent mapping to demo users. No source contents appear in errors.
    """
    if local_profile_path is None:
        return load_synthetic_catalog()
    path = Path(local_profile_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        raw_bytes = path.read_bytes()
        rows = json.loads(raw_bytes.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError(
            "Local profile input cannot be read as UTF-8 JSON; no demo fallback"
        ) from None
    if not isinstance(rows, list) or len(rows) != 50:
        raise ValueError("Local profiles must contain exactly 50 source records")
    ids = [row.get("id") if isinstance(row, dict) else None for row in rows]
    if any(type(value) is not int for value in ids) or set(ids) != set(range(1, 51)):
        raise ValueError(
            "Local profiles must have unique integer source IDs 1 through 50"
        )
    try:
        profiles = {row["id"]: normalize_profile(row) for row in rows}
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "Local profiles failed source schema validation; no demo fallback"
        ) from None
    if any(profile.data_scope != "original" for profile in profiles.values()):
        raise ValueError("Local profiles must retain original data scope")
    catalog = load_synthetic_catalog()  # Recipe-only reader; never original dialogues.
    catalog.profiles = (
        profiles  # Exact source ID lookup, NOT list position or demo ID offset.
    )
    catalog.quality_report.update(
        data_scope="original",
        profile_count=50,
        profile_provenance="explicit_local_source_profiles",
        original_profiles_loaded=True,
        original_dialogues_loaded=False,
        limitations=[
            "真实或脱敏档案仅本地使用；data_scope仍为original，解析仅外发用户消息与白名单结构字段，说明本地生成。",
            "按源id精确查档，不自动匹配、按数组位置猜人或回退到演示画像。",
            "未读取官方对话；加载成功不表示真实模型解析、平台接口或推荐质量已通过。",
        ],
    )
    catalog.quality_report["sources"]["profiles"] = {
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "record_count": 50,
    }
    return catalog
