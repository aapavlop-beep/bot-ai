from __future__ import annotations

import sqlite3
from pathlib import Path


class PredictionStore:
    def __init__(self, path: str = "data/bot.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sport TEXT NOT NULL,
                    league TEXT NOT NULL,
                    event TEXT NOT NULL,
                    market TEXT NOT NULL,
                    odds REAL NOT NULL,
                    probability REAL NOT NULL,
                    value_percent REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                )"""
            )

    def add_prediction(
        self,
        sport: str,
        league: str,
        event: str,
        market: str,
        odds: float,
        probability: float,
        value_percent: float,
        created_at: str,
    ) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO predictions
                (sport, league, event, market, odds, probability,
                 value_percent, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (sport, league, event, market, odds, probability, value_percent, created_at),
            )
            return int(cursor.lastrowid)

    def stats(self) -> dict[str, int | float]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS won,
                    SUM(CASE WHEN status = 'lost' THEN 1 ELSE 0 END) AS lost
                   FROM predictions"""
            ).fetchone()
        total = int(row["total"] or 0)
        won = int(row["won"] or 0)
        lost = int(row["lost"] or 0)
        settled = won + lost
        return {
            "total": total,
            "won": won,
            "lost": lost,
            "hit_rate": (won / settled * 100) if settled else 0.0,
        }
