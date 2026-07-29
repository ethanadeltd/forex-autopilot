from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from app.strategies.registry import STRATEGY_PRESETS, get_strategy


DEFAULT_STRATEGY = "human_sr_h1_m15"
STATE_PATH = Path("data/strategy_selected.json")


def _valid_ids() -> set[str]:
    return {p["id"] for p in STRATEGY_PRESETS}


def get_selected_strategy_id(fallback: Optional[str] = None) -> str:
    fb = fallback or DEFAULT_STRATEGY
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            sid = str(data.get("strategy_id") or fb)
            if sid in _valid_ids():
                return sid
    except Exception:
        pass
    return fb if fb in _valid_ids() else DEFAULT_STRATEGY


def set_selected_strategy_id(strategy_id: str) -> str:
    sid = strategy_id if strategy_id in _valid_ids() else DEFAULT_STRATEGY
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"strategy_id": sid}, indent=2), encoding="utf-8")
    return sid


def selected_strategy(fallback: Optional[str] = None):
    return get_strategy(get_selected_strategy_id(fallback))
