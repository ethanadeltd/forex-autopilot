from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid4().hex[:12]}"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class SignalAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE = "close"


class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class MarketSnapshot(BaseModel):
    instrument: str
    timeframe: str
    candles: list[Candle]
    indicators: dict[str, Any] = Field(default_factory=dict)
    mid_price: float = 0.0


class AIDecision(BaseModel):
    instrument: str
    action: SignalAction
    confidence: float = Field(ge=0.0, le=1.0)
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rationale: str = ""
    invalid_if: str = ""
    risk_notes: str = ""

    @field_validator("confidence")
    @classmethod
    def clip_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))


class PositionSize(BaseModel):
    units: int
    risk_amount: float
    stop_distance: float
    reward_distance: float
    rr_ratio: float


class TradeIntent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ti_"))
    instrument: str
    side: Side
    units: int
    entry: float
    stop_loss: float
    take_profit: float
    confidence: float
    rationale: str
    created_at: datetime = Field(default_factory=utcnow)


class Trade(BaseModel):
    id: str = Field(default_factory=lambda: new_id("tr_"))
    broker_trade_id: Optional[str] = None
    instrument: str
    side: Side
    units: int
    entry_price: float
    stop_loss: float
    take_profit: float
    status: TradeStatus = TradeStatus.OPEN
    opened_at: datetime = Field(default_factory=utcnow)
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    rationale: str = ""
    confidence: float = 0.0
    mode: str = "paper"


class AccountState(BaseModel):
    equity: float
    balance: float
    unrealized_pnl: float = 0.0
    open_trades: int = 0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    mode: str = "paper"


class RiskVerdict(BaseModel):
    allowed: bool
    reason: str = ""
    size: Optional[PositionSize] = None


class BotEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ev_"))
    ts: datetime = Field(default_factory=utcnow)
    level: Literal["info", "warning", "error", "trade"] = "info"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
