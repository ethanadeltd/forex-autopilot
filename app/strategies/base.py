from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models import AIDecision, Candle, SignalAction, Trade


@dataclass
class StrategyContext:
    instrument: str
    entry_timeframe: str
    entry_candles: list[Candle]
    higher_timeframe: str
    higher_candles: list[Candle]
    mid_price: float
    # Optional medium (4H) and macro (Daily) extra timeframes
    medium_timeframe: str = ""
    medium_candles: list[Candle] = field(default_factory=list)
    macro_timeframe: str = ""
    macro_candles: list[Candle] = field(default_factory=list)
    entry_indicators: dict[str, Any] = field(default_factory=dict)
    higher_indicators: dict[str, Any] = field(default_factory=dict)
    medium_indicators: dict[str, Any] = field(default_factory=dict)
    macro_indicators: dict[str, Any] = field(default_factory=dict)
    open_trades: list[Trade] = field(default_factory=list)
    session_ok: bool = True
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyDecision:
    action: SignalAction
    confidence: float = 0.0
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rationale: str = ""
    invalid_if: str = ""
    risk_notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_ai_decision(self, instrument: str) -> AIDecision:
        return AIDecision(
            instrument=instrument,
            action=self.action,
            confidence=self.confidence,
            entry=self.entry,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            rationale=self.rationale,
            invalid_if=self.invalid_if,
            risk_notes=self.risk_notes,
        )


class Strategy(ABC):
    id: str = "base"
    name: str = "Base"
    description: str = ""

    # Default chart context the engine should fetch
    entry_timeframe: str = "M15"
    higher_timeframe: str = "H1"
    min_higher_candles: int = 5
    lookback_entry: int = 200
    lookback_higher: int = 120
    # Optional medium (4H) and macro (Daily) extra timeframes
    medium_timeframe: str = "H4"
    macro_timeframe: str = "D"
    min_medium_candles: int = 10
    min_macro_candles: int = 10
    lookback_medium: int = 80
    lookback_macro: int = 60

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> StrategyDecision:
        raise NotImplementedError
