from __future__ import annotations

from typing import Any

import numpy as np

from app.models import Candle


def _closes(candles: list[Candle]) -> np.ndarray:
    return np.array([c.close for c in candles], dtype=float)


def find_swing_levels(
    candles: list[Candle],
    *,
    lookback: int = 80,
    swing: int = 2,
    max_levels: int = 6,
    merge_atr_frac: float = 0.35,
) -> dict[str, Any]:
    """Detect simple human-style support/resistance from swing highs/lows."""
    if len(candles) < max(20, swing * 4):
        return {"supports": [], "resistances": [], "bias": "range", "atr": 0.0}

    window = candles[-lookback:] if len(candles) > lookback else candles
    highs = np.array([c.high for c in window], dtype=float)
    lows = np.array([c.low for c in window], dtype=float)
    closes = np.array([c.close for c in window], dtype=float)

    # ATR proxy
    trs = []
    for i in range(1, len(window)):
        tr = max(
            window[i].high - window[i].low,
            abs(window[i].high - window[i - 1].close),
            abs(window[i].low - window[i - 1].close),
        )
        trs.append(tr)
    atr = float(np.mean(trs[-14:])) if trs else float(np.mean(highs - lows))

    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(swing, len(window) - swing):
        h = highs[i]
        l = lows[i]
        if h >= np.max(highs[i - swing : i + swing + 1]):
            swing_highs.append(float(h))
        if l <= np.min(lows[i - swing : i + swing + 1]):
            swing_lows.append(float(l))

    def merge(levels: list[float]) -> list[float]:
        if not levels:
            return []
        levels = sorted(levels)
        merged = [levels[0]]
        thr = max(atr * merge_atr_frac, 1e-8)
        for lv in levels[1:]:
            if abs(lv - merged[-1]) <= thr:
                merged[-1] = (merged[-1] + lv) / 2.0
            else:
                merged.append(lv)
        return merged

    supports = merge(swing_lows)
    resistances = merge(swing_highs)
    price = float(closes[-1])

    # Keep nearest relevant levels around price
    supports = sorted([s for s in supports if s <= price + atr * 0.15], reverse=True)[:max_levels]
    resistances = sorted([r for r in resistances if r >= price - atr * 0.15])[:max_levels]

    # Bias from location in recent range
    recent_high = float(np.max(highs[-20:]))
    recent_low = float(np.min(lows[-20:]))
    mid = (recent_high + recent_low) / 2.0
    if price > mid + atr * 0.2:
        bias = "bullish"
    elif price < mid - atr * 0.2:
        bias = "bearish"
    else:
        bias = "range"

    nearest_support = supports[0] if supports else recent_low
    nearest_resistance = resistances[0] if resistances else recent_high

    return {
        "supports": [round(x, 5) for x in supports],
        "resistances": [round(x, 5) for x in resistances],
        "nearest_support": float(nearest_support),
        "nearest_resistance": float(nearest_resistance),
        "bias": bias,
        "atr": atr,
        "recent_high": recent_high,
        "recent_low": recent_low,
        "price": price,
    }


def level_distance_pct(price: float, level: float) -> float:
    if price <= 0:
        return 999.0
    return abs(price - level) / price * 100.0


def mtf_consensus(
    h1_levels: dict[str, Any],
    h4_levels: dict[str, Any],
    daily_levels: dict[str, Any],
) -> dict[str, Any]:
    """Combine H1, 4H, and Daily S/R into a multi-timeframe consensus.

    Returns:
        Dict with:
        - consensus_bias: "bullish" | "bearish" | "range" | "mixed"
        - alignment: 0-3 (how many TFs agree)
        - key_support: strongest support across all TFs
        - key_resistance: strongest resistance across all TFs
        - supports: merged support levels (near price)
        - resistances: merged resistance levels
        - macro_bias: Daily bias (for directional filter)
        - detail: per-TF breakdown
    """
    detail = {
        "H1": {"bias": h1_levels.get("bias", "range"), "sup": h1_levels.get("nearest_support"), "res": h1_levels.get("nearest_resistance")},
        "H4": {"bias": h4_levels.get("bias", "range"), "sup": h4_levels.get("nearest_support"), "res": h4_levels.get("nearest_resistance")},
        "D":  {"bias": daily_levels.get("bias", "range"), "sup": daily_levels.get("nearest_support"), "res": daily_levels.get("nearest_resistance")},
    }

    biases = [v["bias"] for v in detail.values()]
    macro_bias = daily_levels.get("bias", "range")

    # How many TFs agree on direction
    bullish_count = sum(1 for b in biases if b == "bullish")
    bearish_count = sum(1 for b in biases if b == "bearish")

    if bullish_count >= 2 and bearish_count < 2:
        consensus_bias = "bullish"
    elif bearish_count >= 2 and bullish_count < 2:
        consensus_bias = "bearish"
    elif bullish_count >= 2 and bearish_count >= 2:
        consensus_bias = "mixed"
    else:
        consensus_bias = "range"

    alignment = max(bullish_count, bearish_count)

    # Merge all support/resistance levels
    all_supports = []
    all_resistances = []
    for lvl_key in ["H1", "H4", "D"]:
        sup = detail[lvl_key]["sup"]
        res = detail[lvl_key]["res"]
        if sup and isinstance(sup, (int, float)):
            all_supports.append(sup)
        if res and isinstance(res, (int, float)):
            all_resistances.append(res)

    key_support = min(all_supports) if all_supports else None
    key_resistance = max(all_resistances) if all_resistances else None

    return {
        "consensus_bias": consensus_bias,
        "alignment": alignment,  # 0-3 TFs aligned
        "macro_bias": macro_bias,
        "key_support": key_support,
        "key_resistance": key_resistance,
        "supports": sorted(set(round(s, 5) for s in all_supports), reverse=True),
        "resistances": sorted(set(round(r, 5) for r in all_resistances)),
        "detail": detail,
    }
