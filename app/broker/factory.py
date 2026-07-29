from __future__ import annotations

from app.broker.base import Broker
from app.broker.paper import PaperBroker
from app.config import Settings


def _parse_symbol_map(raw: str) -> dict[str, str]:
    """Parse 'EUR_USD:EURUSDm,XAU_USD:XAUUSDm' into dict."""
    out: dict[str, str] = {}
    if not raw.strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def make_broker(settings: Settings) -> Broker:
    broker = settings.broker.lower().strip()

    if settings.trading_mode == "paper" or broker == "paper":
        return PaperBroker(starting_equity=settings.starting_equity)

    if broker == "mt5":
        from app.broker.mt5 import MT5Broker

        if not settings.mt5_login or not settings.mt5_password or not settings.mt5_server:
            raise RuntimeError(
                "MT5 requires MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in .env"
            )
        if settings.trading_mode == "live":
            # soft warning only — Exness demo/live is determined by server name
            pass

        return MT5Broker(
            login=int(settings.mt5_login),
            password=settings.mt5_password,
            server=settings.mt5_server,
            path=settings.mt5_path,
            mode=settings.trading_mode,
            symbol_map=_parse_symbol_map(settings.mt5_symbol_map),
            magic=settings.mt5_magic,
            deviation=settings.mt5_deviation,
        )

    if broker == "oanda":
        from app.broker.oanda import OandaBroker

        if settings.trading_mode == "live" and "fxpractice" in settings.oanda_base_url:
            raise RuntimeError(
                "Refusing live mode with practice API URL. "
                "Set OANDA_BASE_URL=https://api-fxtrade.oanda.com"
            )
        return OandaBroker(
            api_key=settings.oanda_api_key,
            account_id=settings.oanda_account_id,
            base_url=settings.oanda_base_url,
            mode=settings.trading_mode,
        )

    raise ValueError(
        f"Unknown broker '{settings.broker}'. Use paper, mt5, or oanda."
    )
