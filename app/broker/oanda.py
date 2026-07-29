from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.models import AccountState, Candle, Side, Trade, TradeStatus, utcnow


TF_MAP = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H4": "H4",
    "D": "D",
}


class OandaBroker:
    name = "oanda"

    def __init__(self, api_key: str, account_id: str, base_url: str, mode: str = "practice"):
        if not api_key or not account_id:
            raise ValueError("OANDA_API_KEY and OANDA_ACCOUNT_ID are required for practice/live")
        self.api_key = api_key
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        r = self._client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(path, json=payload)
        r.raise_for_status()
        return r.json()

    def get_candles(self, instrument: str, timeframe: str, count: int) -> list[Candle]:
        granularity = TF_MAP.get(timeframe.upper(), "M15")
        data = self._get(
            f"/v3/instruments/{instrument}/candles",
            params={
                "granularity": granularity,
                "count": min(count, 5000),
                "price": "M",
            },
        )
        out: list[Candle] = []
        for c in data.get("candles", []):
            if not c.get("complete", True):
                # include last forming candle for price continuity
                pass
            mid = c["mid"]
            ts = datetime.fromisoformat(c["time"].replace("Z", "+00:00"))
            out.append(
                Candle(
                    time=ts,
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=float(c.get("volume", 0)),
                )
            )
        return out

    def get_price(self, instrument: str) -> float:
        data = self._get(
            f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": instrument},
        )
        p = data["prices"][0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return (bid + ask) / 2.0

    def get_account(self) -> AccountState:
        data = self._get(f"/v3/accounts/{self.account_id}/summary")["account"]
        open_trades = int(data.get("openTradeCount", 0))
        return AccountState(
            equity=float(data.get("NAV", data.get("balance", 0))),
            balance=float(data.get("balance", 0)),
            unrealized_pnl=float(data.get("unrealizedPL", 0)),
            open_trades=open_trades,
            mode=self.mode,
        )

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
        signed_units = abs(units) if side == Side.BUY else -abs(units)
        payload = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(signed_units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": self._price_str(instrument, stop_loss)},
                "takeProfitOnFill": {"price": self._price_str(instrument, take_profit)},
            }
        }
        data = self._post(f"/v3/accounts/{self.account_id}/orders", payload)
        fill = data.get("orderFillTransaction") or {}
        trade_opened = fill.get("tradeOpened") or {}
        entry = float(fill.get("price") or self.get_price(instrument))
        broker_id = str(trade_opened.get("tradeID") or fill.get("id") or "")
        return Trade(
            broker_trade_id=broker_id,
            instrument=instrument,
            side=side,
            units=signed_units,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=TradeStatus.OPEN,
            rationale=rationale,
            confidence=confidence,
            mode=self.mode,
        )

    def close_trade(self, trade: Trade, price: Optional[float] = None) -> Trade:
        if not trade.broker_trade_id:
            raise ValueError("Missing broker_trade_id")
        data = self._put(
            f"/v3/accounts/{self.account_id}/trades/{trade.broker_trade_id}/close",
            {"units": "ALL"},
        )
        fill = data.get("orderFillTransaction") or {}
        exit_px = float(fill.get("price") or price or self.get_price(trade.instrument))
        pl = float(fill.get("pl") or 0.0)
        trade.exit_price = exit_px
        trade.pnl = pl
        trade.closed_at = utcnow()
        trade.status = TradeStatus.CLOSED
        return trade

    def _put(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.put(path, json=payload)
        r.raise_for_status()
        return r.json()

    def sync_open_trades(self, local_trades: list[Trade]) -> list[Trade]:
        data = self._get(f"/v3/accounts/{self.account_id}/openTrades")
        open_ids = {str(t["id"]) for t in data.get("trades", [])}
        updated: list[Trade] = []
        for trade in local_trades:
            if trade.status != TradeStatus.OPEN:
                updated.append(trade)
                continue
            if trade.broker_trade_id and trade.broker_trade_id not in open_ids:
                # closed at broker — fetch details if possible
                try:
                    remote = self._get(
                        f"/v3/accounts/{self.account_id}/trades/{trade.broker_trade_id}"
                    )["trade"]
                    trade.exit_price = float(remote.get("averageClosePrice") or trade.entry_price)
                    trade.pnl = float(remote.get("realizedPL") or 0.0)
                    trade.closed_at = utcnow()
                    trade.status = TradeStatus.CLOSED
                except Exception:
                    trade.status = TradeStatus.CLOSED
                    trade.closed_at = utcnow()
                    trade.exit_price = trade.exit_price or trade.entry_price
            updated.append(trade)
        return updated

    @staticmethod
    def _price_str(instrument: str, price: float) -> str:
        if instrument.startswith("XAU"):
            return f"{price:.2f}"
        if instrument.endswith("JPY"):
            return f"{price:.3f}"
        return f"{price:.5f}"
