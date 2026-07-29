from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from app.models import AccountState, Candle, Side, Trade, TradeStatus, utcnow


def _tf_minutes(tf: str) -> int:
    tf = tf.upper()
    return {
        "M1": 1,
        "M5": 5,
        "M15": 15,
        "M30": 30,
        "H1": 60,
        "H4": 240,
        "D": 1440,
    }.get(tf, 15)


class PaperBroker:
    """Local simulated broker with synthetic seeded random-walk candles."""

    name = "paper"

    def __init__(self, starting_equity: float = 10_000.0, seed: int = 42):
        self.balance = float(starting_equity)
        self.equity = float(starting_equity)
        self.starting_equity = float(starting_equity)
        self._rng = random.Random(seed)
        self._np = np.random.default_rng(seed)
        self._prices: dict[str, float] = {
            "EUR_USD": 1.0850,
            "GBP_USD": 1.2750,
            "XAU_USD": 2350.0,
        }
        self._candle_cache: dict[tuple[str, str], list[Candle]] = {}
        self._open: dict[str, Trade] = {}

    def _vol(self, instrument: str) -> float:
        if instrument.startswith("XAU"):
            return 0.0012
        return 0.00035

    def _generate_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        key = (instrument, timeframe)
        existing = self._candle_cache.get(key, [])
        minutes = _tf_minutes(timeframe)
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        now = now - timedelta(minutes=now.minute % minutes)

        if existing and len(existing) >= count:
            last_t = existing[-1].time
            if now > last_t:
                price = existing[-1].close
                new_bars = self._walk(
                    instrument,
                    price,
                    1,
                    minutes,
                    start=last_t + timedelta(minutes=minutes),
                )
                existing = (existing + new_bars)[-max(count, 300):]
                self._candle_cache[key] = existing
                self._prices[instrument] = existing[-1].close
            return existing[-count:]

        base = self._prices.get(instrument, 1.0)
        start = now - timedelta(minutes=minutes * count)
        bars = self._walk(instrument, base, count, minutes, start=start)
        self._candle_cache[key] = bars
        self._prices[instrument] = bars[-1].close
        return bars[-count:]

    def _walk(
        self,
        instrument: str,
        start_price: float,
        count: int,
        minutes: int,
        start: datetime,
    ) -> list[Candle]:
        vol = self._vol(instrument)
        price = float(start_price)
        mu = 0.0
        out: list[Candle] = []
        t = start
        for _ in range(count):
            ret = float(self._np.normal(mu, vol))
            opn = price
            close = max(0.0001, opn * (1.0 + ret))
            wick = abs(float(self._np.normal(0, vol * 0.6)))
            high = max(opn, close) * (1 + wick)
            low = min(opn, close) * (1 - wick)
            if instrument.startswith("XAU"):
                high, low, opn, close = (round(x, 2) for x in (high, low, opn, close))
            else:
                high, low, opn, close = (round(x, 5) for x in (high, low, opn, close))
            out.append(
                Candle(
                    time=t,
                    open=float(opn),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(self._rng.randint(50, 500)),
                )
            )
            price = close
            t += timedelta(minutes=minutes)
        return out

    def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        return self._generate_candles(instrument, timeframe, count)

    def get_price(self, instrument: str) -> float:
        candles = self.get_candles(instrument, "M15", 10)
        return float(candles[-1].close)

    def get_account(self) -> AccountState:
        unreal = 0.0
        for t in self._open.values():
            price = self.get_price(t.instrument)
            unreal += self._pnl(t, price)
        self.equity = self.balance + unreal
        return AccountState(
            equity=self.equity,
            balance=self.balance,
            unrealized_pnl=unreal,
            open_trades=len(self._open),
            mode="paper",
        )

    def _pnl(self, trade: Trade, price: float) -> float:
        direction = 1 if trade.side == Side.BUY else -1
        return direction * (price - trade.entry_price) * abs(trade.units)

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
        price = self.get_price(instrument)
        signed = abs(units) if side == Side.BUY else -abs(units)
        trade = Trade(
            instrument=instrument,
            side=side,
            units=signed,
            entry_price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=TradeStatus.OPEN,
            rationale=rationale,
            confidence=confidence,
            mode="paper",
            broker_trade_id=f"paper-{len(self._open) + 1}",
        )
        self._open[trade.id] = trade
        return trade

    def close_trade(self, trade: Trade, price: Optional[float] = None) -> Trade:
        px = price if price is not None else self.get_price(trade.instrument)
        trade.exit_price = px
        trade.pnl = self._pnl(trade, px)
        trade.closed_at = utcnow()
        trade.status = TradeStatus.CLOSED
        self.balance += trade.pnl
        self._open.pop(trade.id, None)
        self.equity = self.balance
        return trade

    def sync_open_trades(self, local_trades: list[Trade]) -> list[Trade]:
        updated: list[Trade] = []
        for t in local_trades:
            if t.status == TradeStatus.OPEN and t.id not in self._open:
                self._open[t.id] = t

        for trade in list(self._open.values()):
            price = self.get_price(trade.instrument)
            hit = False
            exit_px = price
            if trade.side == Side.BUY:
                if price <= trade.stop_loss:
                    hit, exit_px = True, trade.stop_loss
                elif price >= trade.take_profit:
                    hit, exit_px = True, trade.take_profit
            else:
                if price >= trade.stop_loss:
                    hit, exit_px = True, trade.stop_loss
                elif price <= trade.take_profit:
                    hit, exit_px = True, trade.take_profit
            if hit:
                closed = self.close_trade(trade, exit_px)
                updated.append(closed)
            else:
                updated.append(trade)
        return updated
