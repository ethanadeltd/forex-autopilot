from __future__ import annotations

from typing import Dict, List, Type

from app.strategies.base import Strategy
from app.strategies.human_sr_h1_m15 import HumanSupportResistanceH1M15
from app.strategies.trend_m15 import TrendContinuationM15

_REGISTRY: Dict[str, Type[Strategy]] = {
    HumanSupportResistanceH1M15.id: HumanSupportResistanceH1M15,
    TrendContinuationM15.id: TrendContinuationM15,
}

# ordered presets for UI dropdown
STRATEGY_PRESETS: List[dict] = [
    {
        "id": HumanSupportResistanceH1M15.id,
        "name": HumanSupportResistanceH1M15.name,
        "description": HumanSupportResistanceH1M15.description,
    },
    {
        "id": TrendContinuationM15.id,
        "name": TrendContinuationM15.name,
        "description": TrendContinuationM15.description,
    },
]


def list_strategies() -> list[dict]:
    return list(STRATEGY_PRESETS)


def get_strategy(strategy_id: str) -> Strategy:
    key = (strategy_id or "").strip() or HumanSupportResistanceH1M15.id
    cls = _REGISTRY.get(key)
    if cls is None:
        # fallback to human S/R
        cls = HumanSupportResistanceH1M15
    return cls()
