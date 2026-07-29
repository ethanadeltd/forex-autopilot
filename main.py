from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

# Ensure project root on path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.alerts.telegram import TelegramAlerter
from app.analysis.ai_engine import AIEngine
from app.backtest.runner import run_backtest
from app.broker.factory import make_broker
from app.config import get_settings
from app.data.store import Store
from app.execution.engine import TradingEngine
from app.risk.manager import RiskManager

console = Console()


def setup_logging(level: str) -> None:
    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("logs/autopilot.log", encoding="utf-8"),
        ],
    )


def cmd_run(once: bool = False) -> None:
    import signal as sigmod

    from app.runtime import is_running, mark_running, mark_stopped, should_stop, PID_FILE, STOP_FILE

    settings = get_settings()
    setup_logging(settings.log_level)

    if is_running():
        console.print(f"[red]Bot is already running (PID {PID_FILE.read_text()}). Stop it first.[/]")
        console.print("  Dashboard: click STOP, or delete data/bot.stop + data/bot.pid")
        return

    mark_running()

    def _cleanup():
        mark_stopped()
        try:
            broker.shutdown()
        except Exception:
            pass

    console.print(
        f"[bold green]AI Forex Autopilot[/] broker=[cyan]{settings.broker}[/] "
        f"mode=[yellow]{settings.trading_mode}[/] "
        f"Dashboard stop available | PID={os.getpid()}"
    )
    if settings.trading_mode == "live":
        console.print("[bold red]WARNING: LIVE MONEY MODE[/]")

    broker = make_broker(settings)
    store = Store(settings.db_path)
    risk = RiskManager(settings)
    if settings.trading_mode == "paper" and hasattr(broker, "_open"):
        for t in store.open_trades():
            broker._open[t.id] = t
    try:
        acct = broker.get_account()
        risk.peak_equity = max(acct.equity, 0.01)  # use real balance, not hardcoded starting_equity
        # Import recent MT5 trade history so dashboard shows past closed trades
        if hasattr(broker, 'import_closed_history'):
            broker.import_closed_history(store, days=30)
    except Exception:
        pass

    engine = TradingEngine(
        settings=settings,
        broker=broker,
        store=store,
        risk=risk,
        ai=AIEngine(settings),
        alerter=TelegramAlerter(settings),
    )

    if once:
        engine.run_once()
        _print_status(store, broker)
        _cleanup()
        return

    console.print(f"Loop every {settings.loop_seconds}s. Stop via dashboard or Ctrl+C.")
    try:
        while True:
            if should_stop():
                console.print("[yellow]Stop signal received from dashboard.[/]")
                break
            try:
                engine.run_once()
            except KeyboardInterrupt:
                console.print("\nStopped by user.")
                break
            except Exception as exc:
                logging.exception("Loop error: %s", exc)
            if should_stop():
                console.print("[yellow]Stop signal received from dashboard.[/]")
                break
            time.sleep(settings.loop_seconds)
    finally:
        _cleanup()


def _print_status(store: Store, broker) -> None:
    acct = broker.get_account()
    table = Table(title="Account")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Equity", f"{acct.equity:.2f}")
    table.add_row("Balance", f"{acct.balance:.2f}")
    table.add_row("Open trades", str(acct.open_trades))
    table.add_row("Unrealized", f"{acct.unrealized_pnl:.2f}")
    console.print(table)

    opens = store.open_trades()
    if opens:
        t = Table(title="Open Trades")
        t.add_column("Instrument")
        t.add_column("Side")
        t.add_column("Entry")
        t.add_column("SL")
        t.add_column("TP")
        for tr in opens:
            t.add_row(
                tr.instrument,
                tr.side.value,
                f"{tr.entry_price}",
                f"{tr.stop_loss}",
                f"{tr.take_profit}",
            )
        console.print(t)


def cmd_backtest(bars: int, instrument: str, use_mt5: bool = True) -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    result = run_backtest(settings, bars=bars, instrument=instrument, use_mt5=use_mt5)
    table = Table(title=f"Backtest {instrument} ({bars} bars)")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Source", result.source)
    table.add_row("Trades", str(result.trades))
    table.add_row("Wins", str(result.wins))
    table.add_row("Losses", str(result.losses))
    table.add_row("Win rate", f"{result.win_rate:.1f}%")
    table.add_row("PnL", f"{result.pnl:.2f}")
    table.add_row("Ending equity", f"{result.ending_equity:.2f}")
    table.add_row("Max DD", f"{result.max_drawdown_pct:.2f}%")
    table.add_row("Notes", result.notes)
    console.print(table)


def cmd_dashboard(host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn

    console.print(f"[bold green]Monitor dashboard:[/] http://{host}:{port}")
    console.print("Leave this running. Bot loop is separate: python main.py run")
    uvicorn.run("app.dashboard:app", host=host, port=port, reload=False)


def cmd_status() -> None:
    settings = get_settings()
    broker = make_broker(settings)
    store = Store(settings.db_path)
    if settings.trading_mode == "paper" and hasattr(broker, "_open"):
        for t in store.open_trades():
            broker._open[t.id] = t
    _print_status(store, broker)
    events = store.recent_events(10)
    if events:
        t = Table(title="Recent events")
        t.add_column("Time")
        t.add_column("Level")
        t.add_column("Message")
        for e in events:
            t.add_row(e.ts.isoformat(timespec="seconds"), e.level, e.message[:80])
        console.print(t)


def cmd_mt5_test() -> None:
    """Connect to Exness MT5 and print account + symbol resolution + last prices."""
    settings = get_settings()
    setup_logging(settings.log_level)

    if not settings.mt5_login or not settings.mt5_password or not settings.mt5_server:
        console.print("[red]Missing MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in .env[/]")
        console.print("Fill Exness MT5 credentials, keep MT5 terminal installed, then retry.")
        return

    console.print("[bold]Connecting to MT5 / Exness...[/]")
    from app.broker.factory import _parse_symbol_map
    from app.broker.mt5 import MT5Broker

    broker = MT5Broker(
        login=int(settings.mt5_login),
        password=settings.mt5_password,
        server=settings.mt5_server,
        path=settings.mt5_path,
        mode="practice" if settings.trading_mode == "paper" else settings.trading_mode,
        symbol_map=_parse_symbol_map(settings.mt5_symbol_map),
        magic=settings.mt5_magic,
        deviation=settings.mt5_deviation,
    )
    try:
        acct = broker.get_account()
        table = Table(title="MT5 Account")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("Balance", f"{acct.balance:.2f}")
        table.add_row("Equity", f"{acct.equity:.2f}")
        table.add_row("Open positions", str(acct.open_trades))
        table.add_row("Server", settings.mt5_server)
        table.add_row("Login", str(settings.mt5_login))
        console.print(table)

        sym_table = Table(title="Symbol check")
        sym_table.add_column("Internal")
        sym_table.add_column("MT5 symbol")
        sym_table.add_column("Price")
        for inst in settings.instrument_list:
            try:
                sym = broker.resolve_symbol(inst)
                px = broker.get_price(inst)
                candles = broker.get_candles(inst, settings.timeframe, 5)
                sym_table.add_row(inst, sym, f"{px} ({len(candles)} candles ok)")
            except Exception as exc:
                sym_table.add_row(inst, "-", f"ERROR: {exc}")
        console.print(sym_table)
        console.print("[green]MT5 connection OK. Ready for demo trading when you switch BROKER=mt5.[/]")
    finally:
        broker.shutdown()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI Forex Autopilot")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Start trading loop")
    run_p.add_argument("--once", action="store_true", help="Single iteration")

    bt = sub.add_parser("backtest", help="Backtest strategy (prefers real MT5 history)")
    bt.add_argument("--bars", type=int, default=1500)
    bt.add_argument("--instrument", default="EUR_USD")
    bt.add_argument("--synthetic", action="store_true", help="Force synthetic data")

    dash = sub.add_parser("dashboard", help="Open local web monitor")
    dash.add_argument("--host", default="127.0.0.1")
    dash.add_argument("--port", type=int, default=8787)

    sub.add_parser("status", help="Show account + open trades")
    sub.add_parser("mt5-test", help="Test Exness MT5 login, symbols, and prices")

    bt2 = sub.add_parser("strategy-bt", help="Backtest human_sr_h1_m15 strategy (MTF: M15+H1+H4+Daily)")
    bt2.add_argument("--instrument", default="EUR_USD", help="EUR_USD, GBP_USD, XAU_USD")
    bt2.add_argument("--months", type=int, default=6, help="Months of data")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "run":
        cmd_run(once=args.once)
    elif args.cmd == "backtest":
        cmd_backtest(bars=args.bars, instrument=args.instrument, use_mt5=not args.synthetic)
    elif args.cmd == "dashboard":
        cmd_dashboard(host=args.host, port=args.port)
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "mt5-test":
        cmd_mt5_test()
    elif args.cmd == "strategy-bt":
        from app.backtest.strategy_bt import run_strategy_backtest, print_results
        settings = get_settings()
        result = run_strategy_backtest(settings, instrument=args.instrument, months=args.months)
        print_results(result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
