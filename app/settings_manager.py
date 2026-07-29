"""Editable trading settings persisted to data/settings.json.

Loaded every tick so changes from dashboard take effect without restart.
Prefilled defaults match .env and sensible MT5 values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SETTINGS_PATH = Path("data") / "trader_settings.json"


class TraderSettings(BaseModel):
    # --- AI ---
    ai_provider: str = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_model: str = "deepseek-chat"
    # --- Position sizing ---
    # "fixed_lot" = use fixed_lot_volume
    # "risk_pct"   = use risk_per_trade_pct % of equity
    position_sizing_mode: str = "risk_pct"
    fixed_lot_volume: float = 0.01

    # --- Risk ---
    risk_per_trade_pct: float = 0.75
    daily_loss_limit_pct: float = 3.0
    max_drawdown_pct: float = 8.0
    max_open_trades: int = 2
    max_trades_per_day: int = 8
    min_rr_ratio: float = 1.5
    cooldown_losses: int = 3
    cooldown_minutes: int = 60

    # --- SL/TP geometry ---
    # When non-zero, these override strategy SL/TP distances (in pips / points)
    # 0 = use strategy defaults
    stop_loss_pips: float = 0.0
    take_profit_pips: float = 0.0

    # --- Sessions ---
    trading_sessions: str = "london,newyork"
    instruments: str = "EUR_USD,GBP_USD,XAU_USD"

    # --- Misc ---
    loop_seconds: int = 60

    @property
    def instrument_list(self) -> list[str]:
        return [x.strip() for x in self.instruments.split(",") if x.strip()]

    @property
    def session_list(self) -> list[str]:
        return [x.strip().lower() for x in self.trading_sessions.split(",") if x.strip()]


def load_settings() -> TraderSettings:
    if not SETTINGS_PATH.exists():
        save_settings(TraderSettings())
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return TraderSettings.model_validate(data)
    except Exception:
        return TraderSettings()


def save_settings(s: TraderSettings) -> TraderSettings:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        s.model_dump_json(indent=2), encoding="utf-8"
    )
    return s


def settings_schema() -> list[dict]:
    """Return form fields for the dashboard. No pydantic v2 dependency needed at runtime."""
    default = TraderSettings()
    return [
        {
            "key": "position_sizing_mode",
            "label": "Position sizing",
            "type": "select",
            "options": [
                {"value": "risk_pct", "label": "% of equity"},
                {"value": "fixed_lot", "label": "Fixed lot size"},
            ],
            "default": default.position_sizing_mode,
        },
        {
            "key": "fixed_lot_volume",
            "label": "Lot volume (fixed mode)",
            "type": "float",
            "min": 0.01,
            "max": 10.0,
            "step": 0.01,
            "default": default.fixed_lot_volume,
        },
        {
            "key": "risk_per_trade_pct",
            "label": "Risk per trade (%)",
            "type": "float",
            "min": 0.1,
            "max": 5.0,
            "step": 0.1,
            "default": default.risk_per_trade_pct,
        },
        {
            "key": "daily_loss_limit_pct",
            "label": "Daily loss limit (%)",
            "type": "float",
            "min": 0.5,
            "max": 20.0,
            "step": 0.5,
            "default": default.daily_loss_limit_pct,
        },
        {
            "key": "max_drawdown_pct",
            "label": "Max drawdown (%)",
            "type": "float",
            "min": 1,
            "max": 50,
            "step": 1,
            "default": default.max_drawdown_pct,
        },
        {
            "key": "max_open_trades",
            "label": "Max open trades",
            "type": "int",
            "min": 1,
            "max": 10,
            "default": default.max_open_trades,
        },
        {
            "key": "max_trades_per_day",
            "label": "Max trades per day",
            "type": "int",
            "min": 1,
            "max": 50,
            "default": default.max_trades_per_day,
        },
        {
            "key": "min_rr_ratio",
            "label": "Min reward:risk ratio",
            "type": "float",
            "min": 0.5,
            "max": 10.0,
            "step": 0.1,
            "default": default.min_rr_ratio,
        },
        {
            "key": "cooldown_losses",
            "label": "Losses before cooldown",
            "type": "int",
            "min": 1,
            "max": 20,
            "default": default.cooldown_losses,
        },
        {
            "key": "cooldown_minutes",
            "label": "Cooldown duration (min)",
            "type": "int",
            "min": 5,
            "max": 480,
            "default": default.cooldown_minutes,
        },
        {
            "key": "stop_loss_pips",
            "label": "Stop loss (pips, 0 = strategy)",
            "type": "float",
            "min": 0,
            "max": 500,
            "step": 1,
            "default": default.stop_loss_pips,
        },
        {
            "key": "take_profit_pips",
            "label": "Take profit (pips, 0 = strategy)",
            "type": "float",
            "min": 0,
            "max": 1000,
            "step": 1,
            "default": default.take_profit_pips,
        },
        {
            "key": "trading_sessions",
            "label": "Trading sessions (comma)",
            "type": "text",
            "default": default.trading_sessions,
            "hint": "london,newyork,asia",
        },
        {
            "key": "instruments",
            "label": "Instruments (comma)",
            "type": "text",
            "default": default.instruments,
            "hint": "EUR_USD,GBP_USD,XAU_USD",
        },
        {
            "key": "loop_seconds",
            "label": "Loop interval (seconds)",
            "type": "int",
            "min": 10,
            "max": 3600,
            "default": default.loop_seconds,
        },
        # --- AI settings ---
        {
            "key": "ai_provider",
            "label": "AI provider",
            "type": "select",
            "options": [
                {"value": "openai", "label": "OpenAI-compatible (DeepSeek, Grok, etc.)"},
            ],
            "default": default.ai_provider,
        },
        {
            "key": "openai_api_key",
            "label": "API key",
            "type": "text",
            "default": default.openai_api_key,
            "hint": "Stored in trader_settings.json",
        },
        {
            "key": "openai_base_url",
            "label": "API base URL",
            "type": "text",
            "default": default.openai_base_url,
            "hint": "https://api.deepseek.com/v1 / https://api.openai.com/v1 / https://api.x.ai/v1",
        },
        {
            "key": "openai_model",
            "label": "Model name",
            "type": "text",
            "default": default.openai_model,
            "hint": "deepseek-chat / gpt-4o-mini / grok-2-latest",
        },
    ]
