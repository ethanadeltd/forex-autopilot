from __future__ import annotations

from app.models import SignalAction
from app.strategies.base import Strategy, StrategyContext, StrategyDecision


class TrendContinuationM15(Strategy):
    """Legacy M15 EMA/MACD trend continuation preset."""

    id = "trend_m15"
    name = "Trend Continuation (M15)"
    description = "EMA stack + MACD histogram continuation on M15 only."

    entry_timeframe = "M15"
    higher_timeframe = "M15"
    min_higher_candles = 50
    lookback_entry = 200
    lookback_higher = 200

    def evaluate(self, ctx: StrategyContext) -> StrategyDecision:
        if not ctx.session_ok:
            return StrategyDecision(action=SignalAction.HOLD, rationale="Outside sessions")
        ind = ctx.entry_indicators or {}
        if ind.get("error"):
            return StrategyDecision(action=SignalAction.HOLD, rationale="Insufficient data")

        trend = ind.get("trend")
        rsi = ind.get("rsi14") or 50
        atr = ind.get("atr14") or 0.0
        close = float(ind.get("last_close") or ctx.mid_price)
        macd_hist = ind.get("macd_hist") or 0.0
        ema20 = ind.get("ema20")

        if atr > 0 and trend == "bullish" and rsi < 65 and macd_hist > 0 and ema20 and close >= ema20:
            return StrategyDecision(
                action=SignalAction.BUY,
                confidence=0.66,
                entry=close,
                stop_loss=close - 1.2 * atr,
                take_profit=close + 2.0 * atr,
                rationale="M15 bullish trend continuation",
            )
        if atr > 0 and trend == "bearish" and rsi > 35 and macd_hist < 0 and ema20 and close <= ema20:
            return StrategyDecision(
                action=SignalAction.SELL,
                confidence=0.66,
                entry=close,
                stop_loss=close + 1.2 * atr,
                take_profit=close - 2.0 * atr,
                rationale="M15 bearish trend continuation",
            )
        return StrategyDecision(action=SignalAction.HOLD, confidence=0.4, rationale="No M15 trend setup")
