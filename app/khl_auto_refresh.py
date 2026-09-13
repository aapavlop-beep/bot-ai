from __future__ import annotations

import asyncio
import time
from typing import Any

from .khl_schedule import verified_today_games


class KHLBackgroundCache:
    """Cache only the daily KHL feed.

    The previous implementation expanded one schedule request into multiple
    detail/statistics requests for every match every hour. That could consume
    the API quota before a user even opened a match. Context and odds are now
    loaded lazily for the selected match, with provider-level caching handling
    repeated requests.
    """

    def __init__(self, service, interval_minutes: int = 30) -> None:
        self.service = service
        self.interval_seconds = max(60, interval_minutes * 60)
        self.games: list[dict[str, Any]] = []
        self.context: dict[int, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
        self.updated_at: float = 0.0
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    async def refresh(self) -> None:
        if self.service is None:
            return
        async with self.lock:
            try:
                # Exactly one API request for the whole KHL schedule. The
                # response already contains teams and, when available, odds.
                self.games = await verified_today_games(self.service.client)
                self.updated_at = time.time()
                print(
                    f"KHL background refresh: {len(self.games)} games; "
                    f"API requests total={self.service.client.request_count}",
                    flush=True,
                )
            except Exception as exc:
                print(f"KHL background refresh failed: {type(exc).__name__}: {exc}", flush=True)

    async def loop(self) -> None:
        await self.refresh()
        while True:
            await asyncio.sleep(self.interval_seconds)
            await self.refresh()

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.loop(), name="khl-background-refresh")

    async def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def get_games(self) -> list[dict[str, Any]]:
        return list(self.games)

    def get_context(self, game_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        return self.context.get(game_id)
