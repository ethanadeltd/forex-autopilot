from app.config import Settings
from app.models import AIDecision, SignalAction, Trade, Side, TradeStatus
from app.risk.manager import RiskManager


def test_rejects_low_confidence():
    s = Settings(trading_mode="paper")
    rm = RiskManager(s)
    d = AIDecision(
        instrument="EUR_USD",
        action=SignalAction.BUY,
        confidence=0.4,
        entry=1.1,
        stop_loss=1.09,
        take_profit=1.12,
    )
    v = rm.evaluate_entry(decision=d, equity=10_000, open_trades=[])
    assert v.allowed is False


def test_accepts_valid_setup():
    s = Settings(trading_mode="paper")
    rm = RiskManager(s)
    d = AIDecision(
        instrument="EUR_USD",
        action=SignalAction.BUY,
        confidence=0.8,
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
    )
    v = rm.evaluate_entry(decision=d, equity=10_000, open_trades=[])
    assert v.allowed is True
    assert v.size is not None
    assert v.size.units > 0


def test_blocks_duplicate_instrument():
    s = Settings(trading_mode="paper")
    rm = RiskManager(s)
    open_trades = [
        Trade(
            instrument="EUR_USD",
            side=Side.BUY,
            units=1000,
            entry_price=1.1,
            stop_loss=1.09,
            take_profit=1.12,
            status=TradeStatus.OPEN,
        )
    ]
    d = AIDecision(
        instrument="EUR_USD",
        action=SignalAction.BUY,
        confidence=0.9,
        entry=1.1000,
        stop_loss=1.0950,
        take_profit=1.1100,
    )
    v = rm.evaluate_entry(decision=d, equity=10_000, open_trades=open_trades)
    assert v.allowed is False
