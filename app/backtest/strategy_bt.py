"""Proper backtest for the human_sr_h1_m15 strategy using real MT5 data."""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from uuid import uuid4

import numpy as np
from rich.console import Console
from rich.table import Table

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.broker.factory import _parse_symbol_map
from app.broker.mt5 import MT5Broker
from app.config import Settings, get_settings
from app.models import Candle, Side, Trade, TradeStatus, SignalAction, PositionSize, MarketSnapshot
from app.strategies.human_sr_h1_m15 import HumanSupportResistanceH1M15
from app.strategies.base import StrategyContext
from app.analysis.indicators import build_indicator_pack
from app.analysis.ai_engine import AIEngine
from app.risk.manager import RiskManager
from app.sessions import in_trading_session

console = Console()
logger = logging.getLogger("strategy_bt")

PIP_SIZES = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "XAU_USD": 0.01}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix=""):
    return f"{prefix}{uuid4().hex[:12]}"


def pip_size(instrument: str) -> float:
    return PIP_SIZES.get(instrument, 0.0001)


def fetch_mt5_candles(
    settings: Settings,
    instrument: str,
    timeframe: str,
    count: int,
) -> list[Candle]:
    symbol_map = _parse_symbol_map(settings.mt5_symbol_map)
    broker = MT5Broker(
        login=int(settings.mt5_login),
        password=settings.mt5_password,
        server=settings.mt5_server,
        path=settings.mt5_path,
        mode="practice",
        symbol_map=symbol_map,
        magic=settings.mt5_magic,
        deviation=settings.mt5_deviation,
    )
    try:
        candles = broker.get_candles(instrument, timeframe, count)
        return candles
    finally:
        broker.shutdown()


def find_h1_index(m15_time: datetime, h1_candles: list[Candle]) -> int:
    for i in range(len(h1_candles) - 1, -1, -1):
        if h1_candles[i].time <= m15_time:
            return i
    return 0


def compute_position_size(
    equity: float,
    risk_pct: float,
    entry: float,
    stop_loss: float,
    instrument: str,
) -> PositionSize:
    """Compute position size based on account risk."""
    risk_amount = equity * risk_pct / 100.0
    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        return PositionSize(units=0, risk_amount=0, stop_distance=0, reward_distance=0, rr_ratio=0)
    
    pip_v = pip_size(instrument)
    pip_distance = stop_distance / pip_v
    pip_value = 10.0  # standard lot $10/pip for FX
    
    lots = risk_amount / (pip_distance * pip_value)
    lots = max(0.001, min(1.0, lots))
    units = int(lots * 100_000)
    
    reward_distance = abs(entry - (entry + 2 * (entry - stop_loss) if entry > stop_loss else entry - 2 * (stop_loss - entry)))
    rr = reward_distance / stop_distance if stop_distance > 0 else 0
    
    return PositionSize(
        units=units,
        risk_amount=round(risk_amount, 2),
        stop_distance=round(stop_distance, 5),
        reward_distance=round(reward_distance, 5),
        rr_ratio=round(rr, 2),
    )


def run_strategy_backtest(
    settings: Settings,
    instrument: str = "EUR_USD",
    months: int = 6,
    use_ai: bool = False,
) -> dict:
    """Run human_sr_h1_m15 backtest on real MT5 data w/ direct trade management.

    use_ai=True calls the AI engine for each signal (same as live: confidence
    adjustments + 0.62 gate). Costs API tokens; use_ai=False is strategy-only.
    """
    
    strategy = HumanSupportResistanceH1M15()
    ai = AIEngine(settings) if use_ai else None
    
    mins_per_month = months * 30 * 24 * 60
    m15_bars = mins_per_month // 15 + 1000
    h1_bars = mins_per_month // 60 + 200
    h4_bars = mins_per_month // 240 + 100
    d_bars = mins_per_month // 1440 + 60
    
    console.print(f"[bold]Fetching {months} month(s) of data for {instrument} (MTF)...[/]")
    console.print(f"  M15: ~{m15_bars} bars")
    console.print(f"  H1:  ~{h1_bars} bars")
    console.print(f"  H4:  ~{h4_bars} bars")
    console.print(f"  D:   ~{d_bars} bars")
    
    m15_all = fetch_mt5_candles(settings, instrument, "M15", m15_bars)
    h1_all = fetch_mt5_candles(settings, instrument, "H1", h1_bars)
    h4_all = fetch_mt5_candles(settings, instrument, "H4", h4_bars)
    d_all = fetch_mt5_candles(settings, instrument, "D", d_bars)
    
    if len(m15_all) < 200 or len(h1_all) < 50:
        console.print(f"[red]Not enough data. M15={len(m15_all)}, H1={len(h1_all)}[/]")
        return {"error": "insufficient_data"}
    
    console.print(f"[green]Got {len(m15_all)} M15, {len(h1_all)} H1, {len(h4_all)} H4, {len(d_all)} D[/]")
    
    # Trim to common range
    start_dt = max(m15_all[0].time, h1_all[0].time, h4_all[0].time, d_all[0].time)
    end_dt = min(m15_all[-1].time, h1_all[-1].time, h4_all[-1].time, d_all[-1].time)
    
    m15_all = [c for c in m15_all if start_dt <= c.time <= end_dt]
    h1_all = [c for c in h1_all if start_dt <= c.time <= end_dt]
    h4_all = [c for c in h4_all if start_dt <= c.time <= end_dt]
    d_all = [c for c in d_all if start_dt <= c.time <= end_dt]
    console.print(f"  Range: {start_dt.date()} to {end_dt.date()}")
    console.print(f"  Trimmed: M15={len(m15_all)}, H1={len(h1_all)}, H4={len(h4_all)}, D={len(d_all)}")
    
    # --- Direct trade management ---
    balance = settings.starting_equity
    equity = settings.starting_equity
    open_trades: dict[str, dict] = {}  # trade_id -> trade info dict
    closed_pnls: list[float] = []
    trades_log: list[dict] = []
    peak = settings.starting_equity
    max_dd = 0.0
    
    # Risk tracking
    day_key = ""
    trades_today = 0
    daily_realized_pnl = 0.0
    consecutive_losses = 0
    cooldown_until: Optional[datetime] = None
    halted = False
    halt_reason = ""
    
    lookback_m15 = strategy.lookback_entry
    lookback_h1 = strategy.lookback_higher
    total_bars = len(m15_all)
    
    print(f"Walking {total_bars} M15 bars...")
    last_progress = 0
    
    for i in range(lookback_m15, total_bars):
        # Progress
        pct = i * 100 // total_bars
        if pct > last_progress and pct % 5 == 0:
            last_progress = pct
            print(f"  {pct}% ({i}/{total_bars}) trades={len(closed_pnls)}", flush=True)
        
        m15_window = m15_all[:i+1][-lookback_m15:]
        current_bar = m15_window[-1]
        
        # Find matching candles on all timeframes
        h1_idx = find_h1_index(current_bar.time, h1_all)
        h1_start = max(0, h1_idx - lookback_h1 + 1)
        h1_window = h1_all[h1_start:h1_idx+1]
        
        h4_idx = find_h1_index(current_bar.time, h4_all)  # same logic works for H4
        h4_start = max(0, h4_idx - strategy.lookback_medium + 1)
        h4_window = h4_all[h4_start:h4_idx+1]
        
        d_idx = find_h1_index(current_bar.time, d_all)
        d_start = max(0, d_idx - strategy.lookback_macro + 1)
        d_window = d_all[d_start:d_idx+1]
        
        if len(h1_window) < strategy.min_higher_candles:
            continue
        
        price = current_bar.close
        
        # Day roll
        new_day = current_bar.time.date().isoformat()
        if new_day != day_key:
            day_key = new_day
            trades_today = 0
            daily_realized_pnl = 0.0
            halted = False
            halt_reason = ""
        
        # Cooldown check — block NEW entries while cooldown is active (trades still close normally)
        in_cooldown = cooldown_until is not None and current_bar.time < cooldown_until
        if in_cooldown:
            pass  # entries blocked below; SL/TP checks still run
        
        # --- Check SL/TP on open trades ---
        for tid in list(open_trades.keys()):
            t = open_trades[tid]
            hit = False
            exit_px = price
            
            if t["side"] == "buy":
                if current_bar.low <= t["sl"]:
                    hit, exit_px = True, t["sl"]
                elif current_bar.high >= t["tp"]:
                    hit, exit_px = True, t["tp"]
            else:
                if current_bar.high >= t["sl"]:
                    hit, exit_px = True, t["sl"]
                elif current_bar.low <= t["tp"]:
                    hit, exit_px = True, t["tp"]
            
            if hit:
                direction = 1 if t["side"] == "buy" else -1
                pnl = direction * (exit_px - t["entry"]) * abs(t["units"])
                balance += pnl
                closed_pnls.append(pnl)
                daily_realized_pnl += pnl
                trades_today += 1
                
                result = "win" if pnl > 0 else "loss"
                if pnl > 0:
                    consecutive_losses = 0
                else:
                    consecutive_losses += 1
                    
                    # Daily loss limit
                    if daily_realized_pnl <= -settings.daily_loss_limit_pct / 100 * peak:
                        halted = True
                        halt_reason = "daily_loss_limit"
                    
                    # Drawdown check
                    dd = (peak - equity) / peak * 100 if peak > 0 else 0
                    if dd >= settings.max_drawdown_pct:
                        halted = True
                        halt_reason = "max_drawdown"
                    
                    # Cooldown after consecutive losses
                    if consecutive_losses >= settings.cooldown_losses:
                        cooldown_until = current_bar.time + timedelta(minutes=settings.cooldown_minutes)
                
                trades_log.append({
                    "time": current_bar.time.isoformat(),
                    "side": t["side"].upper(),
                    "entry": t["entry"],
                    "exit": exit_px,
                    "sl": t["sl"],
                    "tp": t["tp"],
                    "pnl": round(pnl, 2),
                    "result": result,
                    "bars_held": i - t["open_bar"],
                    "confidence": t["confidence"],
                    "rationale": t.get("rationale", ""),
                })
                del open_trades[tid]
        
        # Update equity
        unrealized = 0.0
        for t in open_trades.values():
            direction = 1 if t["side"] == "buy" else -1
            unrealized += direction * (price - t["entry"]) * abs(t["units"])
        equity = balance + unrealized
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        
        if halted:
            continue
        
        # Block new entries during cooldown (fixed: was a no-op before)
        if in_cooldown:
            continue
        
        # Skip if already have open trade on this instrument
        if any(t["instrument"] == instrument for t in open_trades.values()):
            continue
        
        # Max trades per day
        if trades_today >= settings.max_trades_per_day:
            continue
        
        # --- Build strategy context with MTF ---
        entry_ind = build_indicator_pack(m15_window)
        higher_ind = build_indicator_pack(h1_window)
        medium_ind = build_indicator_pack(h4_window) if len(h4_window) >= strategy.min_medium_candles else {}
        macro_ind = build_indicator_pack(d_window) if len(d_window) >= strategy.min_macro_candles else {}
        
        if entry_ind.get("error") or higher_ind.get("error"):
            continue
        
        ctx = StrategyContext(
            instrument=instrument,
            entry_timeframe="M15",
            entry_candles=m15_window,
            higher_timeframe="H1",
            higher_candles=h1_window,
            medium_timeframe="H4",
            medium_candles=h4_window,
            macro_timeframe="D",
            macro_candles=d_window,
            mid_price=price,
            entry_indicators=entry_ind,
            higher_indicators=higher_ind,
            medium_indicators=medium_ind,
            macro_indicators=macro_ind,
            open_trades=[],
            session_ok=True,
        )
        
        decision = strategy.evaluate(ctx)
        
        if decision.action not in (SignalAction.BUY, SignalAction.SELL):
            continue
        
        # Optional AI second opinion — same logic as live engine
        if ai is not None:
            try:
                snap = MarketSnapshot(
                    instrument=instrument,
                    timeframe="M15",
                    candles=m15_window,
                    indicators={
                        **entry_ind,
                        "h1_levels": (decision.meta or {}).get("h1_levels", {}),
                        "higher_tf": "H1",
                        "strategy_id": strategy.id,
                        "strategy_rationale": decision.rationale,
                    },
                    mid_price=price,
                )
                ai_dec = ai.decide(snap, [], session_ok=True)
                if ai_dec.action == SignalAction.HOLD and ai_dec.confidence >= 0.7:
                    decision.confidence = min(decision.confidence, 0.55)
                    decision.rationale = f"{decision.rationale} | AI caution: {ai_dec.rationale}"
                elif ai_dec.action == decision.action:
                    decision.confidence = min(0.92, decision.confidence + 0.05)
                    decision.rationale = f"{decision.rationale} | AI agrees: {ai_dec.rationale}"
            except Exception as exc:
                console.print(f"[yellow]AI overlay failed (skipping): {exc}[/]")
        
        # Confidence gate — same as live RiskManager (0.62)
        if ai is not None and decision.confidence < 0.62:
            continue
        
        # Min R:R check
        if decision.stop_loss and decision.take_profit and decision.entry:
            risk_dist = abs(decision.entry - decision.stop_loss)
            reward_dist = abs(decision.take_profit - decision.entry)
            rr = reward_dist / risk_dist if risk_dist > 0 else 0
            if rr < settings.min_rr_ratio:
                continue
        
        # Position sizing
        ps = compute_position_size(
            equity,
            settings.risk_per_trade_pct,
            float(decision.entry),
            float(decision.stop_loss),
            instrument,
        )
        if ps.units <= 0:
            continue
        
        # Max open trades
        if len(open_trades) >= settings.max_open_trades:
            continue
        
        # Open trade
        tid = new_id("bt_")
        open_trades[tid] = {
            "id": tid,
            "instrument": instrument,
            "side": "buy" if decision.action == SignalAction.BUY else "sell",
            "entry": price,  # use current close as fill
            "sl": float(decision.stop_loss),
            "tp": float(decision.take_profit),
            "units": ps.units,
            "confidence": decision.confidence,
            "rationale": decision.rationale,
            "open_bar": i,
            "open_time": current_bar.time,
        }
    
    # Close remaining at final price
    final_price = m15_all[-1].close
    for t in list(open_trades.values()):
        direction = 1 if t["side"] == "buy" else -1
        pnl = direction * (final_price - t["entry"]) * abs(t["units"])
        balance += pnl
        closed_pnls.append(pnl)
        trades_log.append({
            "time": m15_all[-1].time.isoformat(),
            "side": t["side"].upper(),
            "entry": t["entry"],
            "exit": final_price,
            "sl": t["sl"],
            "tp": t["tp"],
            "pnl": round(pnl, 2),
            "result": "open_closed",
            "bars_held": total_bars - t["open_bar"],
            "confidence": t["confidence"],
            "rationale": t.get("rationale", "") + " [open at end]",
        })
    
    # --- Results ---
    total_trades = len(closed_pnls)
    wins = sum(1 for x in closed_pnls if x > 0)
    losses = sum(1 for x in closed_pnls if x <= 0)
    total_pnl = float(sum(closed_pnls)) if closed_pnls else 0.0
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = float(np.mean([x for x in closed_pnls if x > 0])) if wins > 0 else 0.0
    avg_loss = float(np.mean([x for x in closed_pnls if x < 0])) if losses > 0 else 0.0
    gross_win = sum(x for x in closed_pnls if x > 0)
    gross_loss = sum(x for x in closed_pnls if x < 0)
    profit_factor = abs(gross_win / gross_loss) if gross_loss != 0 else float('inf') if gross_win > 0 else 0.0
    
    sharpe = float(np.mean(closed_pnls) / np.std(closed_pnls) * np.sqrt(365)) if closed_pnls and np.std(closed_pnls) > 0 else 0.0
    
    ending_equity = balance
    
    # Streaks
    max_win_streak = max_loss_streak = curr = 0
    curr_type = None
    for p in closed_pnls:
        t = "win" if p > 0 else "loss"
        if t == curr_type:
            curr += 1
        else:
            curr = 1
            curr_type = t
        if t == "win":
            max_win_streak = max(max_win_streak, curr)
        else:
            max_loss_streak = max(max_loss_streak, curr)
    
    return {
        "instrument": instrument,
        "months": months,
        "total_bars": total_bars,
        "date_range": f"{m15_all[0].time.date()} to {m15_all[-1].time.date()}",
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(win_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "starting_equity": settings.starting_equity,
        "ending_equity": round(ending_equity, 2),
        "return_pct": round((ending_equity - settings.starting_equity) / settings.starting_equity * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
        "sharpe_annual": round(sharpe, 2),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "trades": trades_log,
        "pnls": closed_pnls,
    }


def print_results(r: dict) -> None:
    if "error" in r:
        console.print(f"[red]Error: {r['error']}[/]")
        return
    
    console.print()
    
    # Main summary table
    table = Table(title=f"Human S/R H1+M15 Backtest — {r['instrument']} ({r['months']}mo)")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    
    table.add_row("Date range", r["date_range"])
    table.add_row("Total M15 bars", f"{r['total_bars']:,}")
    table.add_row("Starting equity", f"${r['starting_equity']:.2f}")
    table.add_row("Ending equity", f"${r['ending_equity']:.2f}")
    ret_style = "green" if r['return_pct'] > 0 else ("red" if r['return_pct'] < 0 else "white")
    table.add_row("Total return", f"{r['return_pct']:+.2f}%", style=ret_style)
    table.add_row("")
    table.add_row("Total trades", str(r["total_trades"]))
    table.add_row("Wins", str(r["wins"]))
    table.add_row("Losses", str(r["losses"]))
    table.add_row("Win rate", f"{r['win_rate_pct']}%")
    table.add_row("Total P&L", f"${r['total_pnl']:+.2f}", style="green" if r['total_pnl'] > 0 else "red")
    table.add_row("Max drawdown", f"{r['max_drawdown_pct']:.2f}%", style="yellow" if r['max_drawdown_pct'] > 5 else "white")
    table.add_row("")
    table.add_row("Avg win", f"${r['avg_win']:.2f}")
    table.add_row("Avg loss", f"${r['avg_loss']:.2f}")
    table.add_row("Profit factor", f"{r['profit_factor']:.2f}")
    table.add_row("Sharpe (annual)", f"{r['sharpe_annual']:.2f}")
    table.add_row("Max win streak", str(r["max_win_streak"]))
    table.add_row("Max loss streak", str(r["max_loss_streak"]))
    console.print(table)
    
    # Trade list
    if r["trades"]:
        t_table = Table(title=f"Trades ({r['total_trades']} total, showing last 25)")
        t_table.add_column("#")
        t_table.add_column("Date")
        t_table.add_column("Side")
        t_table.add_column("Entry")
        t_table.add_column("Exit")
        t_table.add_column("P&L")
        t_table.add_column("Result")
        
        for idx, t in enumerate(r["trades"][-25:], max(1, r['total_trades'] - 24)):
            style = "green" if t["result"] == "win" else "red"
            t_table.add_row(
                str(idx),
                t["time"][:10],
                t["side"],
                f"{t['entry']:.5f}",
                f"{t['exit']:.5f}",
                f"${t['pnl']:+.2f}",
                t["result"],
                style=style,
            )
        console.print(t_table)
    
    # Bottom line
    ret_str = f"+${r['total_pnl']:.2f}" if r['total_pnl'] >= 0 else f"-${abs(r['total_pnl']):.2f}"
    console.print(f"\n[bold]Result:[/] ${r['starting_equity']:.0f} -> ${r['ending_equity']:.0f} "
                  f"({r['return_pct']:+.2f}%)  "
                  f"[bold]Trades:[/] {r['total_trades']}  "
                  f"[bold]Win rate:[/] {r['win_rate_pct']}%  "
                  f"[bold]PF:[/] {r['profit_factor']:.2f}  "
                  f"[bold]Max DD:[/] {r['max_drawdown_pct']:.2f}%  "
                  f"[bold]PnL:[/] {ret_str}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Human S/R strategy backtest")
    parser.add_argument("--instrument", default="EUR_USD", help="EUR_USD, GBP_USD, XAU_USD")
    parser.add_argument("--months", type=int, default=6, help="Months of data")
    args = parser.parse_args()
    
    settings = get_settings()
    result = run_strategy_backtest(settings, instrument=args.instrument, months=args.months)
    print_results(result)
