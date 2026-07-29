from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # paper | practice | live
    # For Exness: practice = demo server, live = real server
    trading_mode: Literal["paper", "practice", "live"] = "paper"

    # paper | mt5 | oanda
    broker: str = "paper"

    # --- MT5 / Exness ---
    mt5_login: str = ""
    mt5_password: str = ""
    mt5_server: str = ""  # e.g. Exness-MT5Trial8 / Exness-MT5Real
    mt5_path: str = ""  # optional full path to terminal64.exe
    mt5_symbol_map: str = ""  # EUR_USD:EURUSDm,GBP_USD:GBPUSDm,XAU_USD:XAUUSDm
    mt5_magic: int = 260727
    mt5_deviation: int = 20

    # --- OANDA (optional legacy) ---
    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_base_url: str = "https://api-fxpractice.oanda.com"

    # --- AI ---
    ai_provider: str = "openai"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # --- Risk ---
    risk_per_trade_pct: float = 0.75
    daily_loss_limit_pct: float = 3.0
    max_drawdown_pct: float = 8.0
    max_open_trades: int = 2
    max_trades_per_day: int = 8
    min_rr_ratio: float = 1.5
    cooldown_losses: int = 3
    cooldown_minutes: int = 60

    # --- Strategy ---
    # human_sr_h1_m15 | trend_m15  (dropdown can override via data/strategy_selected.json)
    strategy_id: str = "human_sr_h1_m15"
    instruments: str = "EUR_USD,GBP_USD,XAU_USD"
    timeframe: str = "M15"  # entry timeframe default; strategy may override
    higher_timeframe: str = "H1"
    lookback_candles: int = 200
    trading_sessions: str = "london,newyork"
    starting_equity: float = 10_000.0

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Runtime ---
    loop_seconds: int = 60
    log_level: str = "INFO"
    db_path: str = "data/autopilot.db"

    @field_validator("risk_per_trade_pct", "daily_loss_limit_pct", "max_drawdown_pct")
    @classmethod
    def positive_pct(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("percentage must be > 0")
        return v

    @property
    def instrument_list(self) -> list[str]:
        return [x.strip() for x in self.instruments.split(",") if x.strip()]

    @property
    def session_list(self) -> list[str]:
        return [x.strip().lower() for x in self.trading_sessions.split(",") if x.strip()]

    @property
    def is_live_money(self) -> bool:
        return self.trading_mode == "live"


@lru_cache
def get_settings() -> Settings:
    return Settings()
