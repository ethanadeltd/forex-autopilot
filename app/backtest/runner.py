from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.analysis.indicators import build_indicator_pack
from app.broker.paper import PaperBroker
from app.config import Settings
from app.models import AIDecision, Candle, MarketSnapshot, Side, SignalAction, TradeStatus
from app.risk.manager import RiskManager


@dataclass
class BacktestResult:
    trades: int
    wins: int
    losses: int
    pnl: float
    win_rate: float
    ending_equity: float
    max_drawdown_pct: float
    notes: str = ""
    source: str = "synthetic"


def _heuristic(snapshot: MarketSnapshot) -> AIDecision:
    ind = snapshot.indicators
    if ind.get("error"):
        return AIDecision(instrument=snapshot.instrument, action=SignalAction.HOLD, confidence=0.0)
    trend = ind.get("trend")
    rsi = ind.get("rsi14") or 50
    atr = ind.get("atr14") or 0.0
    close = float(ind["last_close"])
    macd_hist = ind.get("macd_hist") or 0.0
    if atr > 0 and trend == "bullish" and rsi < 65 and macd_hist > 0 and close >= ind["ema20"]:
        return AIDecision(
            instrument=snapshot.instrument,
            action=SignalAction.BUY,
            confidence=0.7,
            entry=close,
            stop_loss=close - 1.2 * atr,
            take_profit=close + 2.0 * atr,
            rationale="bt-long",
        )
    if atr > 0 and trend == "bearish" and rsi > 35 and macd_hist < 0 and close <= ind["ema20"]:
        return AIDecision(
            instrument=snapshot.instrument,
            action=SignalAction.SELL,
            confidence=0.7,
            entry=close,
            stop_loss=close + 1.2 * atr,
            take_profit=close - 2.0 * atr,
            rationale="bt-short",
        )
    return AIDecision(instrument=snapshot.instrument, action=SignalAction.HOLD, confidence=0.4)


def _load_mt5_candles(settings: Settings, instrument: str, bars: int) -> Optional[list[Candle]]:
    try:
        from app.broker.factory import make_broker, _parse_symbol_map
        from app.broker.mt5 import MT5Broker
    except Exception:
        return None

    if not settings.mt5_login:
        return None
    try:
        broker = MT5Broker(
            login=int(settings.mt5_login),
            password=settings.mt5_password,
            server=settings.mt5_server,
            path=settings.mt5_path,
            mode="practice",
            symbol_map=_parse_symbol_map(settings.mt5_symbol_map),
            magic=settings.mt5_magic,
            deviation=settings.mt5_deviation,
        )
        candles = broker.get_candles(instrument, settings.timeframe, bars)
        broker.shutdown()
        return candles
    except Exception:
        return None


def run_backtest(
    settings: Settings,
    bars: int = 1500,
    instrument: str = "EUR_USD",
    use_mt5: bool = True,
) -> BacktestResult:
    """Walk-forward backtest of the heuristic strategy.

    Prefers real Exness/MT5 historical candles when available.
    Falls back to synthetic paper candles.
    """
    source = "synthetic"
    candles: Optional[list[Candle]] = None
    if use_mt5:
        candles = _load_mt5_candles(settings, instrument, bars)
        if candles and len(candles) >= 100:
            source = "mt5"

    broker = PaperBroker(starting_equity=settings.starting_equity, seed=7)
    if source == "mt5" and candles is not None:
        # Seed paper broker with real series; advance bar-by-bar offline
        broker._candle_cache[(instrument, settings.timeframe)] = list(candles)
        broker._prices[instrument] = candles[-1].close
    else:
        candles = broker.get_candles(instrument, settings.timeframe, bars)

    assert candles is not None
    risk = RiskManager(settings)
    # Reset day tracking loosely: treat whole backtest as multi-day by rolling on candle date
    lookback = min(settings.lookback_candles, 120)
    closed: list[float] = []
    peak = settings.starting_equity
    max_dd = 0.0
    seen_closed_ids: set[str] = set()
    last_day = None

    for i in range(lookback, len(candles)):
        window = candles[: i + 1][-lookback:]
        bar = window[-1]
        day = bar.time.date().isoformat() if isinstance(bar.time, datetime) else None
        if day and day != last_day:
            # soft day roll for trade counters
            risk.day_key = day or risk.day_key
            risk.trades_today = 0
            risk.daily_realized_pnl = 0.0
            risk.halted = False
            risk.halt_reason = ""
            last_day = day

        broker._prices[instrument] = bar.close
        broker._candle_cache[(instrument, settings.timeframe)] = list(window)

        # Intrabar SL/TP approximation using high/low of current bar
        for trade in list(broker._open.values()):
            hit = False
            exit_px = bar.close
            if trade.side == Side.BUY:
                if bar.low <= trade.stop_loss:
                    hit, exit_px = True, trade.stop_loss
                elif bar.high >= trade.take_profit:
                    hit, exit_px = True, trade.take_profit
            else:
                if bar.high >= trade.stop_loss:
                    hit, exit_px = True, trade.stop_loss
                elif bar.low <= trade.take_profit:
                    hit, exit_px = True, trade.take_profit
            if hit:
                closed_t = broker.close_trade(trade, exit_px)
                if closed_t.id not in seen_closed_ids:
                    seen_closed_ids.add(closed_t.id)
                    closed.append(closed_t.pnl)
                    risk.register_close(closed_t)

        account = broker.get_account()
        peak = max(peak, account.equity)
        dd = (peak - account.equity) / peak * 100 if peak else 0.0
        max_dd = max(max_dd, dd)
        risk.update_equity(account.equity)
        if risk.halted:
            # skip new entries for rest of day / until reset
            continue

        open_trades = list(broker._open.values())
        if open_trades:
            continue

        snap = MarketSnapshot(
            instrument=instrument,
            timeframe=settings.timeframe,
            candles=window,
            indicators=build_indicator_pack(window),
            mid_price=bar.close,
        )
        decision = _heuristic(snap)
        verdict = risk.evaluate_entry(
            decision=decision,
            equity=account.equity,
            open_trades=open_trades,
        )
        if not verdict.allowed or verdict.size is None:
            continue
        side = Side.BUY if decision.action == SignalAction.BUY else Side.SELL
        trade = broker.open_trade(
            instrument=instrument,
            side=side,
            units=verdict.size.units,
            stop_loss=float(decision.stop_loss),
            take_profit=float(decision.take_profit),
            rationale=decision.rationale,
            confidence=decision.confidence,
        )
        # Force entry at bar close for realism in offline sim
        trade.entry_price = bar.close
        broker._open[trade.id] = trade
        risk.register_fill(trade)

    for t in list(broker._open.values()):
        c = broker.close_trade(t, candles[-1].close)
        if c.id not in seen_closed_ids:
            closed.append(c.pnl)
            risk.register_close(c)

    wins = sum(1 for x in closed if x > 0)
    losses = sum(1 for x in closed if x <= 0)
    pnl = float(sum(closed))
    ending = broker.get_account().equity
    wr = (wins / len(closed) * 100) if closed else 0.0
    note = (
        "Real Exness/MT5 historical candles + heuristic strategy."
        if source == "mt5"
        else "Synthetic candles + heuristic strategy (MT5 history unavailable)."
    )
    return BacktestResult(
        trades=len(closed),
        wins=wins,
        losses=losses,
        pnl=pnl,
        win_rate=wr,
        ending_equity=ending,
        max_drawdown_pct=max_dd,
        notes=note,
        source=source,
    )
