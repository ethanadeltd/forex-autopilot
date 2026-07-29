from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.models import AccountState, Candle, Side, Trade, TradeStatus, utcnow

logger = logging.getLogger(__name__)

# Internal bot symbols -> common Exness/MT5 variants (first match wins at runtime)
DEFAULT_SYMBOL_CANDIDATES: dict[str, list[str]] = {
    "EUR_USD": ["EURUSDm", "EURUSD", "EURUSDz", "EURUSDc"],
    "GBP_USD": ["GBPUSDm", "GBPUSD", "GBPUSDz", "GBPUSDc"],
    "XAU_USD": ["XAUUSDm", "XAUUSD", "GOLD", "XAUUSDz"],
    "USD_JPY": ["USDJPYm", "USDJPY", "USDJPYz"],
    "USD_BRL": ["USDBRLm", "USDBRL"],
}

TF_MAP = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 16385,
    "H4": 16388,
    "D": 16408,
}


class MT5Broker:
    """Exness (and other) broker via local MetaTrader 5 terminal."""

    name = "mt5"

    def __init__(
        self,
        *,
        login: int,
        password: str,
        server: str,
        path: str = "",
        mode: str = "practice",
        symbol_map: Optional[dict[str, str]] = None,
        magic: int = 260727,
        deviation: int = 20,
    ):
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise RuntimeError(
                "MetaTrader5 package not installed. Run: pip install MetaTrader5"
            ) from exc

        self.mt5 = mt5
        self.login = int(login)
        self.password = password
        self.server = server
        self.path = path.strip()
        self.mode = mode
        self.magic = magic
        self.deviation = deviation
        self._symbol_map = symbol_map or {}
        self._resolved: dict[str, str] = {}
        self._connect()

    def _connect(self) -> None:
        import time

        mt5 = self.mt5
        last_err: tuple = (-1, "not attempted")
        ok = False

        # Retry: attach to running terminal first, then launch via path+creds.
        for attempt in range(1, 6):
            # 1) Attach to already-open MT5 (best when user logged in manually)
            ok = bool(mt5.initialize(timeout=60_000))
            if not ok and self.path:
                ok = bool(
                    mt5.initialize(
                        path=self.path,
                        login=self.login,
                        password=self.password,
                        server=self.server,
                        timeout=120_000,
                    )
                )
            elif not ok:
                ok = bool(mt5.initialize(timeout=60_000))

            if ok:
                break

            last_err = mt5.last_error()
            logger.warning("MT5 initialize attempt %s failed: %s", attempt, last_err)
            time.sleep(3)

        if not ok:
            code, msg = last_err if isinstance(last_err, tuple) else (-1, str(last_err))
            raise RuntimeError(
                f"MT5 initialize failed: {code} {msg}. "
                "Open MetaTrader 5 EXNESS manually, login to the demo account, "
                "enable Algo Trading, leave it open, then retry."
            )

        info = mt5.account_info()
        need_login = info is None or int(getattr(info, "login", 0) or 0) != self.login
        if need_login:
            authorized = mt5.login(self.login, password=self.password, server=self.server)
            if not authorized:
                code, msg = mt5.last_error()
                mt5.shutdown()
                raise RuntimeError(
                    f"MT5 login failed for {self.login}@{self.server}: {code} {msg}. "
                    "Confirm trading password in Exness and manual MT5 login works."
                )
            info = mt5.account_info()

        if info is None:
            mt5.shutdown()
            raise RuntimeError(f"MT5 account_info failed: {mt5.last_error()}")

        logger.info(
            "MT5 connected: login=%s server=%s balance=%.2f equity=%.2f trade_mode=%s",
            info.login,
            info.server,
            info.balance,
            info.equity,
            info.trade_mode,
        )

    def shutdown(self) -> None:
        try:
            self.mt5.shutdown()
        except Exception:
            pass

    def resolve_symbol(self, instrument: str) -> str:
        if instrument in self._resolved:
            return self._resolved[instrument]

        # Explicit map from settings first
        if instrument in self._symbol_map:
            sym = self._symbol_map[instrument]
            if self._ensure_symbol(sym):
                self._resolved[instrument] = sym
                return sym
            raise RuntimeError(f"Configured MT5 symbol not available: {sym}")

        candidates = DEFAULT_SYMBOL_CANDIDATES.get(instrument, [])
        # also try stripped form
        bare = instrument.replace("_", "")
        candidates = list(dict.fromkeys(candidates + [bare, instrument]))

        for sym in candidates:
            if self._ensure_symbol(sym):
                self._resolved[instrument] = sym
                logger.info("Resolved %s -> %s", instrument, sym)
                return sym

        raise RuntimeError(
            f"Could not resolve MT5 symbol for {instrument}. "
            f"Set MT5_SYMBOL_MAP e.g. EUR_USD:EURUSDm"
        )

    def _ensure_symbol(self, symbol: str) -> bool:
        mt5 = self.mt5
        info = mt5.symbol_info(symbol)
        if info is None:
            return False
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                return False
        return True

    def _tf(self, timeframe: str) -> int:
        key = timeframe.upper()
        if key not in TF_MAP:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        # Prefer named constants when available
        mt5 = self.mt5
        const_name = {
            "M1": "TIMEFRAME_M1",
            "M5": "TIMEFRAME_M5",
            "M15": "TIMEFRAME_M15",
            "M30": "TIMEFRAME_M30",
            "H1": "TIMEFRAME_H1",
            "H4": "TIMEFRAME_H4",
            "D": "TIMEFRAME_D1",
        }[key]
        return int(getattr(mt5, const_name, TF_MAP[key]))

    def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        mt5 = self.mt5
        symbol = self.resolve_symbol(instrument)
        rates = mt5.copy_rates_from_pos(symbol, self._tf(timeframe), 0, count)
        if rates is None:
            raise RuntimeError(f"MT5 candles failed for {symbol}: {mt5.last_error()}")
        out: list[Candle] = []
        for r in rates:
            ts = datetime.fromtimestamp(int(r["time"]), tz=timezone.utc)
            out.append(
                Candle(
                    time=ts,
                    open=float(r["open"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    volume=float(r["tick_volume"]),
                )
            )
        return out

    def get_price(self, instrument: str) -> float:
        import time

        mt5 = self.mt5
        symbol = self.resolve_symbol(instrument)
        self._ensure_symbol(symbol)
        tick = None
        for _ in range(5):
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None and (float(tick.bid) > 0 or float(tick.ask) > 0):
                break
            time.sleep(0.2)
        if tick is None or (float(tick.bid) <= 0 and float(tick.ask) <= 0):
            raise RuntimeError(f"MT5 tick failed for {symbol}: {mt5.last_error()}")
        bid = float(tick.bid) if float(tick.bid) > 0 else float(tick.ask)
        ask = float(tick.ask) if float(tick.ask) > 0 else float(tick.bid)
        return (bid + ask) / 2.0

    def get_account(self) -> AccountState:
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError(f"MT5 account_info failed: {self.mt5.last_error()}")
        positions = self.mt5.positions_get() or []
        return AccountState(
            equity=float(info.equity),
            balance=float(info.balance),
            unrealized_pnl=float(info.equity) - float(info.balance),
            open_trades=len(positions),
            mode=self.mode,
        )

    def _volume_from_units(self, symbol: str, units: int) -> float:
        """Convert internal integer units to MT5 lot volume."""
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info missing for {symbol}")

        # Our bot uses "units" roughly as base-currency units.
        # Standard FX: 1.0 lot = 100_000 units. Gold often 100 oz / lot (broker-dependent).
        if symbol.upper().startswith("XAU") or "GOLD" in symbol.upper():
            # treat units as ounces; many Exness gold symbols: 1 lot = 100 oz
            contract = float(getattr(info, "trade_contract_size", 100) or 100)
            volume = abs(units) / contract
        else:
            contract = float(getattr(info, "trade_contract_size", 100_000) or 100_000)
            volume = abs(units) / contract

        step = float(info.volume_step or 0.01)
        vmin = float(info.volume_min or 0.01)
        vmax = float(info.volume_max or 100.0)

        # round down to step
        steps = int(volume / step)
        volume = max(vmin, min(vmax, steps * step))
        # fix float artifacts
        decimals = max(0, str(step)[::-1].find("."))
        volume = round(volume, decimals if decimals > 0 else 2)
        if volume < vmin:
            volume = vmin
        return volume

    def _filling_mode(self, symbol: str) -> int:
        mt5 = self.mt5
        info = mt5.symbol_info(symbol)
        filling = int(getattr(info, "filling_mode", 0) or 0)
        # SYMBOL_FILLING_IOC = 1, FOK = 2, RETURN = 4 (bit flags vary by build)
        order_filling_ioc = getattr(mt5, "ORDER_FILLING_IOC", 1)
        order_filling_fok = getattr(mt5, "ORDER_FILLING_FOK", 0)
        order_filling_return = getattr(mt5, "ORDER_FILLING_RETURN", 2)
        # Prefer IOC then FOK then RETURN
        for mode, flag in (
            (order_filling_ioc, 1),
            (order_filling_fok, 2),
            (order_filling_return, 4),
        ):
            if filling & flag or filling == 0:
                return mode
        return order_filling_ioc

    def open_trade(
        self,
        *,
        instrument: str,
        side: Side,
        units: int,
        stop_loss: float,
        take_profit: float,
        rationale: str = "",
        confidence: float = 0.0,
    ) -> Trade:
        mt5 = self.mt5
        symbol = self.resolve_symbol(instrument)
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise RuntimeError(f"MT5 market data unavailable for {symbol}")

        volume = self._volume_from_units(symbol, units)
        order_type = mt5.ORDER_TYPE_BUY if side == Side.BUY else mt5.ORDER_TYPE_SELL
        price = float(tick.ask if side == Side.BUY else tick.bid)

        digits = int(info.digits)
        sl = round(float(stop_loss), digits)
        tp = round(float(take_profit), digits)

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"autopilot c={confidence:.2f}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        }

        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"MT5 order_send returned None: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"MT5 order rejected: retcode={result.retcode} comment={result.comment}"
            )

        entry = float(result.price or price)
        broker_id = str(result.order or result.deal or "")
        # Prefer position ticket if available shortly after fill
        pos_ticket = self._find_position_ticket(symbol, side)
        if pos_ticket:
            broker_id = str(pos_ticket)

        signed_units = abs(units) if side == Side.BUY else -abs(units)
        return Trade(
            broker_trade_id=broker_id,
            instrument=instrument,
            side=side,
            units=signed_units,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            status=TradeStatus.OPEN,
            rationale=rationale,
            confidence=confidence,
            mode=self.mode,
        )

    def _find_position_ticket(self, symbol: str, side: Side) -> Optional[int]:
        positions = self.mt5.positions_get(symbol=symbol) or []
        want_type = self.mt5.POSITION_TYPE_BUY if side == Side.BUY else self.mt5.POSITION_TYPE_SELL
        for p in reversed(list(positions)):
            if int(p.magic) == self.magic and int(p.type) == want_type:
                return int(p.ticket)
        # fallback: any position on symbol
        if positions:
            return int(positions[-1].ticket)
        return None

    def close_trade(self, trade: Trade, price: Optional[float] = None) -> Trade:
        mt5 = self.mt5
        if not trade.broker_trade_id:
            raise ValueError("Missing broker_trade_id")
        ticket = int(trade.broker_trade_id)
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            # already closed
            trade.status = TradeStatus.CLOSED
            trade.closed_at = utcnow()
            trade.exit_price = price or trade.entry_price
            return trade

        pos = positions[0]
        symbol = pos.symbol
        volume = float(pos.volume)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No tick for {symbol}")

        if int(pos.type) == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            px = float(tick.bid)
        else:
            order_type = mt5.ORDER_TYPE_BUY
            px = float(tick.ask)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": px,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "autopilot-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(symbol),
        }
        result = mt5.order_send(request)
        if result is None:
            raise RuntimeError(f"MT5 close failed: {mt5.last_error()}")
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(
                f"MT5 close rejected: retcode={result.retcode} comment={result.comment}"
            )

        trade.exit_price = float(result.price or px)
        # approximate pnl from position profit if still available else 0
        trade.pnl = float(getattr(pos, "profit", 0.0) or 0.0)
        trade.closed_at = utcnow()
        trade.status = TradeStatus.CLOSED
        return trade

    def sync_open_trades(self, local_trades: list[Trade]) -> list[Trade]:
        mt5 = self.mt5
        positions = mt5.positions_get() or []
        open_tickets = {str(p.ticket) for p in positions}
        # map ticket -> position for pnl updates
        by_ticket = {str(p.ticket): p for p in positions}

        updated: list[Trade] = []
        for trade in local_trades:
            if trade.status != TradeStatus.OPEN:
                updated.append(trade)
                continue

            tid = str(trade.broker_trade_id or "")
            if tid and tid in open_tickets:
                updated.append(trade)
                continue

            # closed externally / SL / TP
            if tid:
                # try deal history for realized pnl
                pnl = self._recent_closed_pnl(tid)
                trade.pnl = pnl
                trade.exit_price = trade.exit_price or trade.entry_price
                trade.closed_at = utcnow()
                trade.status = TradeStatus.CLOSED
                updated.append(trade)
            else:
                updated.append(trade)

        # Import all unknown MT5 positions (any magic) so dashboard shows them
        known = {str(t.broker_trade_id) for t in local_trades if t.broker_trade_id}
        for p in positions:
            if str(p.ticket) in known:
                continue
            side = Side.BUY if int(p.type) == mt5.POSITION_TYPE_BUY else Side.SELL
            instrument = self._instrument_from_symbol(p.symbol)
            imported = Trade(
                broker_trade_id=str(p.ticket),
                instrument=instrument,
                side=side,
                units=int(p.volume * self._contract_size(p.symbol))
                if side == Side.BUY
                else -int(p.volume * self._contract_size(p.symbol)),
                entry_price=float(p.price_open),
                stop_loss=float(p.sl or 0.0),
                take_profit=float(p.tp or 0.0),
                status=TradeStatus.OPEN,
                rationale="imported-from-mt5",
                mode=self.mode,
            )
            updated.append(imported)

        return updated

    def _contract_size(self, symbol: str) -> float:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            return 100_000.0
        return float(getattr(info, "trade_contract_size", 100_000) or 100_000)

    def _instrument_from_symbol(self, symbol: str) -> str:
        for internal, resolved in self._resolved.items():
            if resolved == symbol:
                return internal
        # reverse guess
        s = symbol.upper().replace("M", "").replace("Z", "").replace("C", "")
        if s == "GOLD":
            return "XAU_USD"
        if len(s) == 6:
            return f"{s[:3]}_{s[3:]}"
        return symbol

    def _recent_closed_pnl(self, ticket: str) -> float:
        mt5 = self.mt5
        # Best-effort: scan recent deals
        try:
            from datetime import timedelta

            now = datetime.now(timezone.utc)
            deals = mt5.history_deals_get(now - timedelta(days=7), now)
            if not deals:
                return 0.0
            total = 0.0
            for d in deals:
                # position_id links deals to position ticket
                if str(getattr(d, "position_id", "")) == ticket or str(getattr(d, "order", "")) == ticket:
                    total += float(getattr(d, "profit", 0.0) or 0.0)
                    total += float(getattr(d, "swap", 0.0) or 0.0)
                    total += float(getattr(d, "commission", 0.0) or 0.0)
            return total
        except Exception:
            return 0.0

    def import_closed_history(self, store, days: int = 30) -> None:
        """Import recent closed positions from MT5 history into store."""
        mt5 = self.mt5
        from datetime import timedelta
        from app.models import Side, Trade, TradeStatus
        
        now = datetime.now(timezone.utc)
        try:
            deals = mt5.history_deals_get(now - timedelta(days=days), now)
            if not deals:
                return
            # Group deals by position_id to reconstruct closed trades
            positions: dict[str, dict] = {}
            for d in deals:
                pos_id = str(getattr(d, "position_id", "") or "")
                if not pos_id:
                    continue
                if pos_id not in positions:
                    positions[pos_id] = {
                        "profit": 0.0,
                        "swap": 0.0,
                        "commission": 0.0,
                        "symbol": "",
                        "side": None,
                        "volume": 0.0,
                        "price_open": 0.0,
                        "price_close": 0.0,
                        "time_open": None,
                        "time_close": None,
                    }
                p = positions[pos_id]
                p["profit"] += float(getattr(d, "profit", 0.0) or 0.0)
                p["swap"] += float(getattr(d, "swap", 0.0) or 0.0)
                p["commission"] += float(getattr(d, "commission", 0.0) or 0.0)
                p["symbol"] = getattr(d, "symbol", "") or p["symbol"]
                entry = float(getattr(d, "price", 0.0) or 0.0)
                d_type = int(getattr(d, "type", -1))
                if d_type in (0, 1):  # BUY(0)/SELL(1) deals
                    if p["time_open"] is None:
                        p["time_open"] = datetime.fromtimestamp(int(d.time), tz=timezone.utc)
                        p["price_open"] = entry
                        p["side"] = Side.BUY if d_type == 0 else Side.SELL
                elif d_type in (1, 0):  # reverse
                    p["time_close"] = datetime.fromtimestamp(int(d.time), tz=timezone.utc)
                    p["price_close"] = entry
            
            existing = {t.broker_trade_id for t in store.list_trades() if t.broker_trade_id}
            for pos_id, p in positions.items():
                if pos_id in existing:
                    continue
                if not p["symbol"] or not p["time_close"]:
                    continue
                instrument = self._instrument_from_symbol(p["symbol"])
                total_pnl = p["profit"] + p["swap"] + p["commission"]
                trade = Trade(
                    broker_trade_id=pos_id,
                    instrument=instrument,
                    side=p["side"] or Side.BUY,
                    units=0,
                    entry_price=p["price_open"],
                    exit_price=p["price_close"],
                    stop_loss=0.0,
                    take_profit=0.0,
                    pnl=total_pnl,
                    status=TradeStatus.CLOSED,
                    opened_at=p["time_open"] or now,
                    closed_at=p["time_close"],
                    rationale="imported-from-mt5-history",
                    mode=self.mode,
                )
                store.save_trade(trade)
        except Exception as exc:
            logger.warning("Failed to import MT5 history: %s", exc)
