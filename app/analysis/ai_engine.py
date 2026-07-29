from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.config import Settings
from app.models import AIDecision, MarketSnapshot, SignalAction, Trade

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a disciplined intraday forex/gold trading analyst.
You DO NOT manage money sizing. You only propose trade ideas with invalidation.
Prefer NO TRADE (hold) unless setup quality is high.
Respect trend + volatility. Avoid chasing.
Always return STRICT JSON only, no markdown.

JSON schema:
{
  "instrument": "EUR_USD",
  "action": "buy" | "sell" | "hold" | "close",
  "confidence": 0.0-1.0,
  "entry": number|null,
  "stop_loss": number|null,
  "take_profit": number|null,
  "rationale": "short reason",
  "invalid_if": "what kills the idea",
  "risk_notes": "short notes"
}

Rules:
- If action is hold/close, entry/stop_loss/take_profit may be null.
- If buy: stop_loss < entry < take_profit
- If sell: take_profit < entry < stop_loss
- Minimum reward:risk should aim for >= 1.5
- confidence < 0.62 means you should usually choose hold
- Use provided ATR to place stops sensibly (often 1.0-1.5 ATR)
- One instrument only, the one provided
"""


class AIEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _heuristic_decision(self, snapshot: MarketSnapshot) -> AIDecision:
        """Fallback if no API key / API failure — conservative rules."""
        ind = snapshot.indicators
        if ind.get("error"):
            return AIDecision(
                instrument=snapshot.instrument,
                action=SignalAction.HOLD,
                confidence=0.0,
                rationale="Insufficient data",
            )

        trend = ind.get("trend")
        rsi = ind.get("rsi14") or 50
        atr = ind.get("atr14") or 0.0
        close = float(ind["last_close"])
        macd_hist = ind.get("macd_hist") or 0.0

        action = SignalAction.HOLD
        conf = 0.4
        entry = stop = tp = None
        rationale = "No high-quality setup"

        if atr > 0 and trend == "bullish" and rsi < 65 and macd_hist > 0 and close >= ind["ema20"]:
            action = SignalAction.BUY
            conf = 0.66
            entry = close
            stop = close - 1.2 * atr
            tp = close + 2.0 * atr
            rationale = "Bullish trend continuation: EMA stack + MACD hist > 0"
        elif atr > 0 and trend == "bearish" and rsi > 35 and macd_hist < 0 and close <= ind["ema20"]:
            action = SignalAction.SELL
            conf = 0.66
            entry = close
            stop = close + 1.2 * atr
            tp = close - 2.0 * atr
            rationale = "Bearish trend continuation: EMA stack + MACD hist < 0"

        return AIDecision(
            instrument=snapshot.instrument,
            action=action,
            confidence=conf,
            entry=entry,
            stop_loss=stop,
            take_profit=tp,
            rationale=rationale,
            invalid_if="Close back through EMA20 against position",
            risk_notes="Heuristic fallback (no AI or AI failed)",
        )

    def _extract_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    def decide(
        self,
        snapshot: MarketSnapshot,
        open_trades: list[Trade],
        session_ok: bool,
    ) -> AIDecision:
        if not session_ok:
            return AIDecision(
                instrument=snapshot.instrument,
                action=SignalAction.HOLD,
                confidence=0.0,
                rationale="Outside configured trading sessions",
            )

        if not self.settings.openai_api_key:
            return self._heuristic_decision(snapshot)

        open_summary = [
            {
                "instrument": t.instrument,
                "side": t.side.value,
                "entry": t.entry_price,
                "sl": t.stop_loss,
                "tp": t.take_profit,
            }
            for t in open_trades
            if t.instrument == snapshot.instrument
        ]

        user_payload = {
            "instrument": snapshot.instrument,
            "timeframe": snapshot.timeframe,
            "mid_price": snapshot.mid_price,
            "indicators": snapshot.indicators,
            "recent_candles": [
                {
                    "t": c.time.isoformat(),
                    "o": c.open,
                    "h": c.high,
                    "l": c.low,
                    "c": c.close,
                }
                for c in snapshot.candles[-40:]
            ],
            "open_trades_same_pair": open_summary,
            "style": "intraday",
            "instruction": "Propose buy/sell only if setup is strong; otherwise hold.",
        }

        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
        }

        try:
            with httpx.Client(base_url=self.settings.openai_base_url, timeout=45.0) as client:
                resp = client.post("/chat/completions", headers=headers, json=body)
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
            data = self._extract_json(content)
            data["instrument"] = snapshot.instrument
            decision = AIDecision.model_validate(data)
            return decision
        except Exception as exc:
            logger.warning("AI decision failed, using heuristic: %s", exc)
            d = self._heuristic_decision(snapshot)
            d.risk_notes = f"AI error fallback: {exc}"
            return d
