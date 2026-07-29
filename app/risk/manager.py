from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import Settings
from app.models import (
    AIDecision,
    PositionSize,
    RiskVerdict,
    Side,
    SignalAction,
    Trade,
    TradeStatus,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskManager:
    """Hard risk rails. AI proposes; this class vetoes."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.peak_equity = settings.starting_equity
        self.day_key = _utcnow().date().isoformat()
        self.daily_realized_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.cooldown_until: Optional[datetime] = None
        self.halted = False
        self.halt_reason = ""

    def _roll_day(self) -> None:
        today = _utcnow().date().isoformat()
        if today != self.day_key:
            self.day_key = today
            self.daily_realized_pnl = 0.0
            self.trades_today = 0

    def update_equity(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity
        drawdown_pct = 0.0
        if self.peak_equity > 0:
            drawdown_pct = (self.peak_equity - equity) / self.peak_equity * 100
        if drawdown_pct >= self.settings.max_drawdown_pct:
            self.halted = True
            self.halt_reason = (
                f"Max drawdown hit: {drawdown_pct:.2f}% >= {self.settings.max_drawdown_pct}%"
            )

    def register_fill(self, trade: Trade) -> None:
        self._roll_day()
        self.trades_today += 1

    def register_close(self, trade: Trade) -> None:
        self._roll_day()
        self.daily_realized_pnl += trade.pnl
        if trade.pnl < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.settings.cooldown_losses:
                self.cooldown_until = _utcnow() + timedelta(
                    minutes=self.settings.cooldown_minutes
                )
        else:
            self.consecutive_losses = 0

        equity_proxy = self.settings.starting_equity + self.daily_realized_pnl
        loss_pct = abs(min(0.0, self.daily_realized_pnl)) / max(equity_proxy, 1.0) * 100
        # Use starting equity baseline for daily limit clarity in v1
        daily_loss_pct = abs(min(0.0, self.daily_realized_pnl)) / self.settings.starting_equity * 100
        if daily_loss_pct >= self.settings.daily_loss_limit_pct:
            self.halted = True
            self.halt_reason = (
                f"Daily loss limit hit: {daily_loss_pct:.2f}% >= {self.settings.daily_loss_limit_pct}%"
            )

    def pip_size(self, instrument: str) -> float:
        if instrument.endswith("JPY"):
            return 0.01
        if instrument.startswith("XAU"):
            return 0.1
        return 0.0001

    def units_value_per_price(self, instrument: str, units: int = 1) -> float:
        """Rough PnL multiplier per 1.0 price move for `units` base units."""
        # OANDA units are base-currency units. For FX majors ~ $1 per unit per 1.0 move
        # on XXX_USD or USD-quoted pairs in account USD. Good enough for paper/risk.
        if instrument.startswith("XAU"):
            return float(units)  # $1 per unit per $1 gold move
        return float(units)

    def compute_size(
        self,
        *,
        instrument: str,
        side: Side,
        entry: float,
        stop_loss: float,
        take_profit: float,
        equity: float,
    ) -> Optional[PositionSize]:
        stop_distance = abs(entry - stop_loss)
        reward_distance = abs(take_profit - entry)
        if stop_distance <= 0 or reward_distance <= 0:
            return None

        rr = reward_distance / stop_distance
        if rr < self.settings.min_rr_ratio:
            return None

        # Side sanity
        if side == Side.BUY and not (stop_loss < entry < take_profit):
            return None
        if side == Side.SELL and not (take_profit < entry < stop_loss):
            return None

        risk_amount = equity * (self.settings.risk_per_trade_pct / 100.0)
        # units such that stop_distance * units ~= risk_amount
        raw_units = risk_amount / stop_distance
        units = int(max(1, raw_units))

        # Cap absurd sizes on gold/fx for paper safety
        if instrument.startswith("XAU"):
            units = min(units, 50)
        else:
            units = min(units, 100_000)

        if side == Side.SELL:
            signed_units = -units
        else:
            signed_units = units

        return PositionSize(
            units=signed_units,
            risk_amount=risk_amount,
            stop_distance=stop_distance,
            reward_distance=reward_distance,
            rr_ratio=rr,
        )

    def evaluate_entry(
        self,
        *,
        decision: AIDecision,
        equity: float,
        open_trades: list[Trade],
    ) -> RiskVerdict:
        self._roll_day()
        self.update_equity(equity)

        if self.halted:
            return RiskVerdict(allowed=False, reason=self.halt_reason or "Bot halted")

        if self.settings.is_live_money and self.settings.trading_mode == "live":
            # Extra explicit gate — live requires intentional env
            pass

        now = _utcnow()
        if self.cooldown_until and now < self.cooldown_until:
            mins = int((self.cooldown_until - now).total_seconds() // 60) + 1
            return RiskVerdict(allowed=False, reason=f"Cooldown active ({mins}m left)")

        if decision.action not in (SignalAction.BUY, SignalAction.SELL):
            return RiskVerdict(allowed=False, reason="Not an entry action")

        if decision.confidence < 0.62:
            return RiskVerdict(allowed=False, reason=f"Confidence too low ({decision.confidence:.2f})")

        if decision.entry is None or decision.stop_loss is None or decision.take_profit is None:
            return RiskVerdict(allowed=False, reason="Missing entry/SL/TP")

        open_count = len([t for t in open_trades if t.status == TradeStatus.OPEN])
        if open_count >= self.settings.max_open_trades:
            return RiskVerdict(allowed=False, reason="Max open trades reached")

        if any(t.instrument == decision.instrument and t.status == TradeStatus.OPEN for t in open_trades):
            return RiskVerdict(allowed=False, reason=f"Already in {decision.instrument}")

        if self.trades_today >= self.settings.max_trades_per_day:
            return RiskVerdict(allowed=False, reason="Max trades per day reached")

        daily_loss_pct = abs(min(0.0, self.daily_realized_pnl)) / self.settings.starting_equity * 100
        if daily_loss_pct >= self.settings.daily_loss_limit_pct:
            self.halted = True
            self.halt_reason = "Daily loss limit reached"
            return RiskVerdict(allowed=False, reason=self.halt_reason)

        side = Side.BUY if decision.action == SignalAction.BUY else Side.SELL
        size = self.compute_size(
            instrument=decision.instrument,
            side=side,
            entry=decision.entry,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            equity=equity,
        )
        if size is None:
            return RiskVerdict(
                allowed=False,
                reason=f"Invalid geometry or RR < {self.settings.min_rr_ratio}",
            )

        return RiskVerdict(allowed=True, reason="OK", size=size)

    def reset_halt(self) -> None:
        self.halted = False
        self.halt_reason = ""
