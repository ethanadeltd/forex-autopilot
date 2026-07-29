from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.models import Candle


def candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.model_dump() for c in candles])
    if df.empty:
        return df
    df = df.sort_values("time").reset_index(drop=True)
    return df


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def build_indicator_pack(candles: list[Candle]) -> dict[str, Any]:
    df = candles_to_df(candles)
    if len(df) < 50:
        return {"error": "not_enough_candles", "count": len(df)}

    close = df["close"]
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200) if len(df) >= 200 else _ema(close, min(100, len(df) // 2 or 1))
    rsi = _rsi(close, 14)
    atr = _atr(df, 14)

    # MACD
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    hist = macd - signal

    # Donchian / recent structure
    look = min(20, len(df))
    recent_high = df["high"].tail(look).max()
    recent_low = df["low"].tail(look).min()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    trend = "range"
    if ema20.iloc[-1] > ema50.iloc[-1] > ema200.iloc[-1]:
        trend = "bullish"
    elif ema20.iloc[-1] < ema50.iloc[-1] < ema200.iloc[-1]:
        trend = "bearish"

    return {
        "last_close": float(last["close"]),
        "last_open": float(last["open"]),
        "last_high": float(last["high"]),
        "last_low": float(last["low"]),
        "prev_close": float(prev["close"]),
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": float(ema200.iloc[-1]),
        "rsi14": float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None,
        "atr14": float(atr.iloc[-1]) if pd.notna(atr.iloc[-1]) else None,
        "macd": float(macd.iloc[-1]),
        "macd_signal": float(signal.iloc[-1]),
        "macd_hist": float(hist.iloc[-1]),
        "recent_high_20": float(recent_high),
        "recent_low_20": float(recent_low),
        "trend": trend,
        "candle_count": int(len(df)),
        "returns_last_20_pct": float((close.iloc[-1] / close.iloc[-21] - 1) * 100)
        if len(close) > 21
        else 0.0,
    }
