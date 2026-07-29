from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.models import AccountState, Candle, Side, Trade


class Broker(ABC):
    name: str = "base"

    @abstractmethod
    def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        raise NotImplementedError

    @abstractmethod
    def get_price(self, instrument: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def get_account(self) -> AccountState:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def close_trade(self, trade: Trade, price: Optional[float] = None) -> Trade:
        raise NotImplementedError

    @abstractmethod
    def sync_open_trades(self, local_trades: list[Trade]) -> list[Trade]:
        """Update local open trades with SL/TP hits / broker state."""
        raise NotImplementedError
