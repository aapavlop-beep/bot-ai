from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .khl_schedule import upcoming_moscow_dates, verified_games_for_date

MSK = ZoneInfo("Europe/Moscow")


class KHLBackgroundCache:
    """Continuously maintain today's and near-future KHL schedule.

    The schedule is refreshed every interval and automatically rolls over at
    midnight Moscow time. Per-date caches let Telegram show today/tomorrow
    without making a fresh request for every button press.
    """

    def __init__(self, service, interval_minutes: int = 30, days_ahead: int = 3) -> None:
        self.service = service
        self.interval_seconds = max(300, interval_minutes * 60)
        self.days_ahead = max(1, days_ahead)
        self.games_by_date: dict[str, list[dict[str, Any]]] = {}
        self.context: dict[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
        self.updated_at_by_date: dict[str, float] = {}
        self.last_refresh_date: str = ""
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    async def refresh(self) -> None:
        if self.service is None:
            return
        async with self.lock:
            dates = upcoming_moscow_dates(self.days_ahead)
            current_date = dates[0]
            try:
                for date_value in dates:
                    # The first date is refreshed most often. Future dates are
                    # collected too, so the bot already knows what is coming.
                    games = await verified_games_for_date(self.service.client, date_value)
                    self.games_by_date[date_value] = games
                    self.updated_at_by_date[date_value] = time.time()
                    print(
                        f"KHL daily collector: date={date_value} games={len(games)} "
                        f"API requests total={self.service.client.request_count}",
                        flush=True,
                    )
                self.last_refresh_date = current_date
                # Drop dates that are no longer relevant after a date rollover.
                keep = set(dates)
                self.games_by_date = {k: v for k, v in self.games_by_date.items() if k in keep}
                self.updated_at_by_date = {k: v for k, v in self.updated_at_by_date.items() if k in keep}
            except Exception as exc:
                print(f"KHL daily collector failed: {type(exc).__name__}: {exc}", flush=True)

    async def loop(self) -> None:
        await self.refresh()
        while True:
            await asyncio.sleep(self.interval_seconds)
            await self.refresh()

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.loop(), name="khl-daily-collector")

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def get_games(self, date_value: str | None = None) -> list[dict[str, Any]]:
        date_value = date_value or datetime.now(MSK).date().isoformat()
        return list(self.games_by_date.get(date_value, []))

    def get_context(self, game_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        return self.context.get(game_id)
