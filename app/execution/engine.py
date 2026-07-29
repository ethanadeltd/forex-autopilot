from __future__ import annotations

import logging
from typing import Optional

from app.alerts.telegram import TelegramAlerter
from app.analysis.ai_engine import AIEngine
from app.analysis.indicators import build_indicator_pack
from app.broker.base import Broker
from app.config import Settings
from app.data.store import Store
from app.models import (
    BotEvent,
    Side,
    SignalAction,
    Trade,
    TradeStatus,
)
from app.risk.manager import RiskManager
from app.sessions import in_trading_session
from app.strategies.base import StrategyContext
from app.strategy_state import get_selected_strategy_id, selected_strategy

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(
        self,
        settings: Settings,
        broker: Broker,
        store: Store,
        risk: RiskManager,
        ai: Optional[AIEngine] = None,
        alerter: Optional[TelegramAlerter] = None,
    ):
        self.settings = settings
        self.broker = broker
        self.store = store
        self.risk = risk
        self.ai = ai or AIEngine(settings)
        self.alerter = alerter or TelegramAlerter(settings)

    def _log(self, level: str, message: str, **data) -> None:
        event = BotEvent(level=level, message=message, data=data)  # type: ignore[arg-type]
        self.store.log_event(event)
        log_fn = getattr(logger, "info" if level == "trade" else level, logger.info)
        log_fn("%s | %s", message, data or "")

    def _sync_positions(self) -> list[Trade]:
        local_open = self.store.open_trades()
        synced = self.broker.sync_open_trades(local_open)
        still_open: list[Trade] = []
        for trade in synced:
            prev = next((t for t in local_open if t.id == trade.id), None)
            self.store.save_trade(trade)
            if trade.status == TradeStatus.CLOSED:
                if prev and prev.status == TradeStatus.OPEN:
                    self.risk.register_close(trade)
                    msg = (
                        f"CLOSED {trade.instrument} {trade.side.value} "
                        f"pnl={trade.pnl:.2f} exit={trade.exit_price}"
                    )
                    self._log("trade", msg, trade_id=trade.id, pnl=trade.pnl)
                    self.alerter.send(f"CLOSED: {msg}")
            else:
                still_open.append(trade)
        return still_open

    def _build_context(self, instrument: str, open_trades: list[Trade], session_ok: bool) -> StrategyContext:
        strategy = selected_strategy(self.settings.strategy_id)
        entry_tf = strategy.entry_timeframe or self.settings.timeframe
        higher_tf = strategy.higher_timeframe or self.settings.higher_timeframe
        med_tf = getattr(strategy, 'medium_timeframe', '') or ''
        macro_tf = getattr(strategy, 'macro_timeframe', '') or ''

        entry_candles = self.broker.get_candles(instrument, entry_tf, strategy.lookback_entry)
        if higher_tf == entry_tf:
            higher_candles = entry_candles
        else:
            higher_candles = self.broker.get_candles(
                instrument,
                higher_tf,
                max(strategy.lookback_higher, strategy.min_higher_candles + 5),
            )

        # Fetch medium (4H) candles if strategy uses them
        medium_candles = []
        medium_tf_actual = ""
        medium_ind = {}
        if med_tf and med_tf not in (entry_tf, higher_tf):
            lookback = getattr(strategy, 'lookback_medium', 80)
            min_c = getattr(strategy, 'min_medium_candles', 10)
            medium_candles = self.broker.get_candles(instrument, med_tf, max(lookback, min_c + 5))
            medium_tf_actual = med_tf
            medium_ind = build_indicator_pack(medium_candles)

        # Fetch macro (Daily) candles if strategy uses them
        macro_candles = []
        macro_tf_actual = ""
        macro_ind = {}
        if macro_tf and macro_tf not in (entry_tf, higher_tf, med_tf):
            lookback = getattr(strategy, 'lookback_macro', 60)
            min_c = getattr(strategy, 'min_macro_candles', 8)
            macro_candles = self.broker.get_candles(instrument, macro_tf, max(lookback, min_c + 5))
            macro_tf_actual = macro_tf
            macro_ind = build_indicator_pack(macro_candles)

        entry_ind = build_indicator_pack(entry_candles)
        higher_ind = build_indicator_pack(higher_candles) if higher_tf != entry_tf else entry_ind
        mid = self.broker.get_price(instrument)

        return StrategyContext(
            instrument=instrument,
            entry_timeframe=entry_tf,
            entry_candles=entry_candles,
            higher_timeframe=higher_tf,
            higher_candles=higher_candles,
            medium_timeframe=medium_tf_actual,
            medium_candles=medium_candles,
            macro_timeframe=macro_tf_actual,
            macro_candles=macro_candles,
            mid_price=mid,
            entry_indicators=entry_ind,
            higher_indicators=higher_ind,
            medium_indicators=medium_ind,
            macro_indicators=macro_ind,
            open_trades=open_trades,
            session_ok=session_ok,
        )

    def _apply_settings_overrides(self):
        from app.settings_manager import load_settings

        ts = load_settings()
        self._ts = ts
        return ts

    def run_once(self) -> None:
        if self.settings.trading_mode == "live":
            self._log("warning", "LIVE MODE active — real money at risk")

        # Load editable settings every tick so dashboard changes take effect immediately
        ts = self._apply_settings_overrides()

        account = self.broker.get_account()
        self.risk.update_equity(account.equity)
        open_trades = self._sync_positions()
        session_ok = in_trading_session(ts.session_list)
        strategy_id = get_selected_strategy_id(self.settings.strategy_id)
        strategy = selected_strategy(self.settings.strategy_id)

        self._log(
            "info",
            "tick",
            equity=account.equity,
            open=len(open_trades),
            session_ok=session_ok,
            halted=self.risk.halted,
            mode=self.settings.trading_mode,
            strategy=strategy_id,
        )

        if self.risk.halted:
            self._log("warning", f"Halted: {self.risk.halt_reason}")
            return

        for instrument in ts.instrument_list:
            try:
                self._process_instrument(instrument, open_trades, account.equity, session_ok, strategy)
                open_trades = self.store.open_trades()
            except Exception as exc:
                self._log("error", f"Error on {instrument}: {exc}")

    def _process_instrument(
        self,
        instrument: str,
        open_trades: list[Trade],
        equity: float,
        session_ok: bool,
        strategy,
    ) -> None:
        ctx = self._build_context(instrument, open_trades, session_ok)
        decision_s = strategy.evaluate(ctx)

        # Optional AI second opinion only enriches rationale; does not override HOLD->trade
        # unless strategy already wants an entry and AI strongly disagrees -> reduce confidence.
        # Apply user overrides from settings panel (fixed lot, SL pips, etc.)
        from app.settings_manager import load_settings

        ts = load_settings()
        decision = decision_s.to_ai_decision(instrument)

        # Override SL/TP in pips if user set them
        if ts.stop_loss_pips > 0 and decision.entry is not None:
            pip = self.risk.pip_size(instrument)
            sl_dist = ts.stop_loss_pips * pip
            if decision.action == SignalAction.BUY:
                decision.stop_loss = round(decision.entry - sl_dist, 5)
                if ts.take_profit_pips > 0:
                    decision.take_profit = round(decision.entry + ts.take_profit_pips * pip, 5)
            elif decision.action == SignalAction.SELL:
                decision.stop_loss = round(decision.entry + sl_dist, 5)
                if ts.take_profit_pips > 0:
                    decision.take_profit = round(decision.entry - ts.take_profit_pips * pip, 5)

        if decision.action in (SignalAction.BUY, SignalAction.SELL) and self.settings.openai_api_key:
            try:
                from app.models import MarketSnapshot

                snap = MarketSnapshot(
                    instrument=instrument,
                    timeframe=ctx.entry_timeframe,
                    candles=ctx.entry_candles,
                    indicators={
                        **ctx.entry_indicators,
                        "h1_levels": (decision_s.meta or {}).get("h1_levels", {}),
                        "higher_tf": ctx.higher_timeframe,
                        "strategy_id": strategy.id,
                        "strategy_rationale": decision.rationale,
                    },
                    mid_price=ctx.mid_price,
                )
                ai_dec = self.ai.decide(snap, open_trades, session_ok=session_ok)
                if ai_dec.action == SignalAction.HOLD and ai_dec.confidence >= 0.7:
                    decision.confidence = min(decision.confidence, 0.55)
                    decision.risk_notes = (
                        (decision.risk_notes or "") + f" | AI caution: {ai_dec.rationale}"
                    ).strip(" |")
                elif ai_dec.action == decision.action:
                    decision.confidence = min(0.92, decision.confidence + 0.05)
                    decision.rationale = f"{decision.rationale} | AI agrees: {ai_dec.rationale}"
            except Exception as exc:
                logger.warning("AI overlay failed: %s", exc)

        self._log(
            "info",
            f"decision {instrument}",
            strategy=strategy.id,
            action=decision.action.value,
            confidence=decision.confidence,
            rationale=decision.rationale,
            meta=decision_s.meta,
        )

        if decision.action == SignalAction.CLOSE:
            for t in open_trades:
                if t.instrument == instrument and t.status == TradeStatus.OPEN:
                    closed = self.broker.close_trade(t)
                    self.store.save_trade(closed)
                    self.risk.register_close(closed)
                    msg = f"AI CLOSE {instrument} pnl={closed.pnl:.2f}"
                    self._log("trade", msg)
                    self.alerter.send(f"AI CLOSE: {msg}")
            return

        if decision.action not in (SignalAction.BUY, SignalAction.SELL):
            return

        verdict = self.risk.evaluate_entry(
            decision=decision,
            equity=equity,
            open_trades=open_trades,
        )
        if not verdict.allowed or verdict.size is None:
            self._log("info", f"risk veto {instrument}", reason=verdict.reason)
            return

        side = Side.BUY if decision.action == SignalAction.BUY else Side.SELL
        trade = self.broker.open_trade(
            instrument=instrument,
            side=side,
            units=verdict.size.units,
            stop_loss=float(decision.stop_loss),
            take_profit=float(decision.take_profit),
            rationale=decision.rationale,
            confidence=decision.confidence,
        )
        self.store.save_trade(trade)
        self.risk.register_fill(trade)
        msg = (
            f"OPEN {side.value.upper()} {instrument} units={trade.units} "
            f"entry={trade.entry_price} SL={trade.stop_loss} TP={trade.take_profit} "
            f"conf={decision.confidence:.2f} strat={strategy.id}"
        )
        self._log("trade", msg, rationale=decision.rationale)
        self.alerter.send(f"OPEN: {msg}\n{decision.rationale}")
