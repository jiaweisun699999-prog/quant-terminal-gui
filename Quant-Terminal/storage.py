from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config import AppConfig
from models import AppState, DripState


@dataclass(frozen=True)
class Storage:
    config: AppConfig

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.sqlite_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def init_db(self) -> None:
        Path(self.config.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """.strip()
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """.strip()
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ledger (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """.strip()
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                  code TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  alias TEXT,
                  enabled INTEGER NOT NULL DEFAULT 1
                )
                """.strip()
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio (
                  code TEXT PRIMARY KEY,
                  kind TEXT NOT NULL,
                  qty REAL NOT NULL DEFAULT 0,
                  cost REAL NOT NULL DEFAULT 0
                )
                """.strip()
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS position_state (
                  code TEXT PRIMARY KEY,
                  last_buy_date TEXT
                )
                """.strip()
            )

    def load_state(self) -> AppState:
        self.init_db()
        with self._connect() as conn:
            cur = conn.execute("SELECT key, value FROM state")
            rows = cur.fetchall()

        kv: dict[str, str] = {k: v for (k, v) in rows}

        drip_weeks = int(kv.get("drip_weeks", "12"))
        drip_queue_raw = kv.get("drip_queue_json", None)
        if drip_queue_raw:
            queue = [float(x) for x in json.loads(drip_queue_raw)]
            drip = DripState(weeks=drip_weeks, queue=queue)
        else:
            drip = DripState(weeks=drip_weeks)

        highest_nav = kv.get("highest_nav", None)
        last_shot_date = kv.get("last_shot_date", None)
        last_drip_release_date = kv.get("last_drip_release_date", None)

        state = AppState(
            drip=drip,
            highest_nav=float(highest_nav) if highest_nav is not None else None,
            last_shot_date=datetime.fromisoformat(last_shot_date).date() if last_shot_date else None,
            last_drip_release_date=datetime.fromisoformat(last_drip_release_date).date() if last_drip_release_date else None,
        )
        return state

    def save_state(self, state: AppState) -> None:
        self.init_db()
        payload = {
            "drip_weeks": str(state.drip.weeks),
            "drip_queue_json": json.dumps(state.drip.queue, ensure_ascii=False),
            "highest_nav": "" if state.highest_nav is None else str(state.highest_nav),
            "last_shot_date": "" if state.last_shot_date is None else state.last_shot_date.isoformat(),
            "last_drip_release_date": "" if state.last_drip_release_date is None else state.last_drip_release_date.isoformat(),
        }

        with self._connect() as conn:
            for k, v in payload.items():
                if v == "":
                    conn.execute("DELETE FROM state WHERE key = ?", (k,))
                else:
                    conn.execute(
                        "INSERT INTO state(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (k, v),
                    )

    def append_ledger(self, payload: dict[str, Any]) -> None:
        self.init_db()
        ts = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ledger(ts, payload_json) VALUES(?, ?)",
                (ts, json.dumps(payload, ensure_ascii=False)),
            )

    def list_ledger(self, limit: int = 50) -> list[dict[str, Any]]:
        self.init_db()
        with self._connect() as conn:
            cur = conn.execute("SELECT id, ts, payload_json FROM ledger ORDER BY id DESC LIMIT ?", (int(limit),))
            rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for _id, ts, payload_json in rows:
            try:
                payload = json.loads(payload_json)
            except Exception:
                payload = {"raw": payload_json}
            out.append({"id": _id, "ts": ts, "payload": payload})
        return out

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        self.init_db()
        with self._connect() as conn:
            cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
        if not row:
            return default
        return str(row[0])

    def set_setting(self, key: str, value: str) -> None:
        self.init_db()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def list_watchlist(self) -> list[dict[str, Any]]:
        self.init_db()
        with self._connect() as conn:
            cur = conn.execute("SELECT code, kind, alias, enabled FROM watchlist ORDER BY code")
            rows = cur.fetchall()
        return [{"code": c, "kind": k, "alias": a, "enabled": int(e)} for (c, k, a, e) in rows]

    def upsert_watchlist(self, code: str, kind: str, alias: str | None = None, enabled: bool = True) -> None:
        self.init_db()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO watchlist(code, kind, alias, enabled) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(code) DO UPDATE SET kind=excluded.kind, alias=excluded.alias, enabled=excluded.enabled",
                (code, kind, alias, 1 if enabled else 0),
            )

    def delete_watchlist(self, code: str) -> None:
        self.init_db()
        with self._connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE code = ?", (code,))

    def list_portfolio(self) -> list[dict[str, Any]]:
        self.init_db()
        with self._connect() as conn:
            cur = conn.execute("SELECT code, kind, qty, cost FROM portfolio ORDER BY code")
            rows = cur.fetchall()
        return [{"code": c, "kind": k, "qty": float(q), "cost": float(cost)} for (c, k, q, cost) in rows]

    def upsert_portfolio(self, code: str, kind: str, qty: float, cost: float) -> None:
        self.init_db()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO portfolio(code, kind, qty, cost) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(code) DO UPDATE SET kind=excluded.kind, qty=excluded.qty, cost=excluded.cost",
                (code, kind, float(qty), float(cost)),
            )

    def delete_portfolio(self, code: str) -> None:
        self.init_db()
        with self._connect() as conn:
            conn.execute("DELETE FROM portfolio WHERE code = ?", (code,))

    def get_last_buy_date(self, code: str) -> str | None:
        self.init_db()
        with self._connect() as conn:
            cur = conn.execute("SELECT last_buy_date FROM position_state WHERE code = ?", (code,))
            row = cur.fetchone()
        if not row:
            return None
        return row[0]

    def set_last_buy_date(self, code: str, iso_date: str) -> None:
        self.init_db()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO position_state(code, last_buy_date) VALUES(?, ?) "
                "ON CONFLICT(code) DO UPDATE SET last_buy_date=excluded.last_buy_date",
                (code, iso_date),
            )

