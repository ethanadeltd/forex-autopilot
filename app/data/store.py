from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from app.models import BotEvent, Trade, TradeStatus


class Store:
    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def save_trade(self, trade: Trade) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO trades (id, payload) VALUES (?, ?)",
                (trade.id, trade.model_dump_json()),
            )

    def list_trades(self, status: Optional[TradeStatus] = None) -> list[Trade]:
        with self._conn() as conn:
            rows = conn.execute("SELECT payload FROM trades").fetchall()
        trades = [Trade.model_validate_json(r["payload"]) for r in rows]
        if status is not None:
            trades = [t for t in trades if t.status == status]
        return sorted(trades, key=lambda t: t.opened_at)

    def open_trades(self) -> list[Trade]:
        return self.list_trades(TradeStatus.OPEN)

    def log_event(self, event: BotEvent) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO events (id, ts, level, message, payload) VALUES (?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.ts.isoformat(),
                    event.level,
                    event.message,
                    event.model_dump_json(),
                ),
            )

    def recent_events(self, limit: int = 50) -> list[BotEvent]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM events ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [BotEvent.model_validate_json(r["payload"]) for r in rows]

    def set_kv(self, key: str, value: Any) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if not row:
            return default
        return json.loads(row["value"])
