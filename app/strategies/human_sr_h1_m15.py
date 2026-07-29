from __future__ import annotations

from app.models import SignalAction
from app.strategies.base import Strategy, StrategyContext, StrategyDecision
from app.strategies.levels import find_swing_levels, level_distance_pct, mtf_consensus


class HumanSupportResistanceH1M15(Strategy):
    """Human-style S/R strategy with multi-timeframe structure.

    - Macro bias from Daily (never trade against it)
    - Medium structure from 4H (adds confidence)
    - Key levels from H1 (swing highs/lows)
    - Entries timed on M15 (reaction candles at levels)
    - Buys near support with bullish reaction; sells near resistance with bearish reaction
    - All TFs must align or trade is skipped / confidence reduced
    """

    id = "human_sr_h1_m15"
    name = "Human S/R (Daily bias + 4H structure + H1 levels + M15 entry)"
    description = (
        "Multi-timeframe S/R: Daily bias filters direction, 4H confirms structure, "
        "H1 provides key support/resistance, M15 times the entry. "
        "Best results on EUR/USD, GBP/USD. "
        "WARNING: NOT RECOMMENDED for XAU/USD (Gold) - high drawdown risk."
    )

    entry_timeframe = "M15"
    higher_timeframe = "H1"
    medium_timeframe = "H4"
    macro_timeframe = "D"
    min_higher_candles = 5
    min_medium_candles = 10
    min_macro_candles = 8
    lookback_entry = 200
    lookback_higher = 120
    lookback_medium = 80
    lookback_macro = 60

    def evaluate(self, ctx: StrategyContext) -> StrategyDecision:
        if not ctx.session_ok:
            return StrategyDecision(
                action=SignalAction.HOLD,
                confidence=0.0,
                rationale="Outside trading sessions",
            )

        if len(ctx.higher_candles) < self.min_higher_candles:
            return StrategyDecision(
                action=SignalAction.HOLD,
                confidence=0.0,
                rationale=f"Need at least {self.min_higher_candles} H1 candles (have {len(ctx.higher_candles)})",
            )

        if len(ctx.entry_candles) < 30:
            return StrategyDecision(
                action=SignalAction.HOLD,
                confidence=0.0,
                rationale="Not enough M15 candles yet",
            )

        h1 = find_swing_levels(ctx.higher_candles, lookback=max(40, self.min_higher_candles * 8))
        m15_ind = ctx.entry_indicators or {}
        price = float(ctx.mid_price or h1.get("price") or ctx.entry_candles[-1].close)
        atr_h1 = float(h1.get("atr") or 0.0)
        atr_m15 = float(m15_ind.get("atr14") or atr_h1 / 2 or 0.0)
        if atr_m15 <= 0:
            return StrategyDecision(action=SignalAction.HOLD, rationale="ATR unavailable")

        # --- Multi-timeframe consensus ---
        h4_ok = len(ctx.medium_candles) >= self.min_medium_candles
        d_ok = len(ctx.macro_candles) >= self.min_macro_candles

        h4 = find_swing_levels(ctx.medium_candles, lookback=self.lookback_medium) if h4_ok else {}
        daily = find_swing_levels(ctx.macro_candles, lookback=self.lookback_macro) if d_ok else {}

        tf_consensus = mtf_consensus(
            h1_levels=h1,
            h4_levels=h4 if h4_ok else {"bias": "range", "nearest_support": None, "nearest_resistance": None},
            daily_levels=daily if d_ok else {"bias": "range", "nearest_support": None, "nearest_resistance": None},
        )

        macro_bias = tf_consensus["macro_bias"]  # Daily bias
        consensus_bias = tf_consensus["consensus_bias"]
        alignment = tf_consensus["alignment"]  # 0-3

        support = float(h1["nearest_support"])
        resistance = float(h1["nearest_resistance"])
        bias = h1.get("bias", "range")

        last = ctx.entry_candles[-1]
        prev = ctx.entry_candles[-2]
        rsi = m15_ind.get("rsi14")
        ema20 = m15_ind.get("ema20")
        macd_hist = m15_ind.get("macd_hist") or 0.0

        # Proximity thresholds
        near_pct = 0.08
        near_atr = 0.35 * atr_m15
        near_support = abs(price - support) <= max(near_atr, price * near_pct / 100.0)
        near_resistance = abs(price - resistance) <= max(near_atr, price * near_pct / 100.0)

        # Candle reaction
        bullish_react = last.close > last.open and last.close > prev.close and last.low <= support + atr_m15 * 0.15
        bearish_react = last.close < last.open and last.close < prev.close and last.high >= resistance - atr_m15 * 0.15

        # --- GOLD WARNING ---
        is_gold = "xau" in ctx.instrument.lower()
        if is_gold:
            risk_notes_prefix = "WARNING: XAU/USD not recommended for this strategy (backtest PF=1.02, DD=115%). "
        else:
            risk_notes_prefix = ""

        # --- MACRO BIAS FILTER (Daily) ---
        # Never trade against the Daily trend
        if d_ok and macro_bias in ("bullish", "bearish"):
            if macro_bias == "bearish" and (near_support and bullish_react):
                return StrategyDecision(
                    action=SignalAction.HOLD,
                    confidence=0.4,
                    rationale=f"Daily bias is bearish -- skipping BUY at H1 support {support:.5f}",
                    meta={"h1_levels": h1, "mtf": tf_consensus},
                )
            if macro_bias == "bullish" and (near_resistance and bearish_react):
                return StrategyDecision(
                    action=SignalAction.HOLD,
                    confidence=0.4,
                    rationale=f"Daily bias is bullish -- skipping SELL at H1 resistance {resistance:.5f}",
                    meta={"h1_levels": h1, "mtf": tf_consensus},
                )

        # Avoid chasing mid-range
        midspan = abs(resistance - support)
        if midspan > 0 and abs(price - (support + resistance) / 2) < midspan * 0.15 and not (near_support or near_resistance):
            return StrategyDecision(
                action=SignalAction.HOLD,
                confidence=0.35,
                rationale="Price mid-range; waiting for level",
                meta={"h1_levels": h1, "mtf": tf_consensus},
            )

        # --- BUY setup ---
        if near_support and bullish_react and (rsi is None or rsi < 62):
            if bias == "bearish" and ema20 and price < ema20 and macd_hist < 0:
                return StrategyDecision(
                    action=SignalAction.HOLD,
                    confidence=0.45,
                    rationale="Near support but H1/M15 still strongly bearish -- skip knife catch",
                    meta={"h1_levels": h1, "mtf": tf_consensus},
                )
            entry = price
            sl = min(support, last.low) - 0.4 * atr_m15
            tp_level = resistance
            tp_rr = entry + 2.0 * (entry - sl)
            tp = min(tp_level, tp_rr) if tp_level > entry else tp_rr
            if tp <= entry or sl >= entry:
                return StrategyDecision(action=SignalAction.HOLD, rationale="Invalid BUY geometry")
            conf = 0.72
            if bias == "bullish":
                conf += 0.06
            if macd_hist > 0:
                conf += 0.03
            if alignment >= 2 and consensus_bias == "bullish":
                conf += 0.04
            return StrategyDecision(
                action=SignalAction.BUY,
                confidence=min(conf, 0.9),
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                rationale=(
                    f"BUY: M15 bounce at H1 support {support:.5f} "
                    f"(Daily={macro_bias}, consensus={consensus_bias}, align={alignment}/3)"
                ),
                invalid_if=f"M15 close back below support {support:.5f}",
                risk_notes=f"{risk_notes_prefix}Support bounce with MTF structure",
                meta={"h1_levels": h1, "mtf": tf_consensus,
                      "dist_support_pct": level_distance_pct(price, support)},
            )

        # --- SELL setup ---
        if near_resistance and bearish_react and (rsi is None or rsi > 38):
            if bias == "bullish" and ema20 and price > ema20 and macd_hist > 0:
                return StrategyDecision(
                    action=SignalAction.HOLD,
                    confidence=0.45,
                    rationale="Near resistance but H1/M15 still strongly bullish -- skip fade",
                    meta={"h1_levels": h1, "mtf": tf_consensus},
                )
            entry = price
            sl = max(resistance, last.high) + 0.4 * atr_m15
            tp_level = support
            tp_rr = entry - 2.0 * (sl - entry)
            tp = max(tp_level, tp_rr) if tp_level < entry else tp_rr
            if tp >= entry or sl <= entry:
                return StrategyDecision(action=SignalAction.HOLD, rationale="Invalid SELL geometry")
            conf = 0.72
            if bias == "bearish":
                conf += 0.06
            if macd_hist < 0:
                conf += 0.03
            if alignment >= 2 and consensus_bias == "bearish":
                conf += 0.04
            return StrategyDecision(
                action=SignalAction.SELL,
                confidence=min(conf, 0.9),
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                rationale=(
                    f"SELL: M15 rejection at H1 resistance {resistance:.5f} "
                    f"(Daily={macro_bias}, consensus={consensus_bias}, align={alignment}/3)"
                ),
                invalid_if=f"M15 close back above resistance {resistance:.5f}",
                risk_notes=f"{risk_notes_prefix}Resistance rejection with MTF structure",
                meta={"h1_levels": h1, "mtf": tf_consensus,
                      "dist_resistance_pct": level_distance_pct(price, resistance)},
            )

        return StrategyDecision(
            action=SignalAction.HOLD,
            confidence=0.4,
            rationale=(
                f"No setup. H1 S={support:.5f} R={resistance:.5f} "
                f"Daily={macro_bias} consensus={consensus_bias} "
                f"near_sup={near_support} near_res={near_resistance}"
            ),
            meta={"h1_levels": h1, "mtf": tf_consensus},
        )
