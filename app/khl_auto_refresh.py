from __future__ import annotations

import asyncio
import time
from typing import Any

from .khl_schedule import verified_today_games


class KHLBackgroundCache:
    """Keeps the KHL schedule, markets and statistics fresh while the bot runs."""

    def __init__(self, service, interval_minutes: int = 60) -> None:
        self.service = service
        self.interval_seconds = max(15, interval_minutes * 60)
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
                games = await verified_today_games(self.service.client)
                self.games = games

                # Warm the data used by predictions. AI calls are deliberately
                # excluded; only sports data and betting markets are refreshed.
                for game in games:
                    game_id = game.get("id")
                    if game_id is None:
                        continue
                    try:
                        markets = await self.service.markets_for_game(int(game_id))
                        analysis = await self.service.analysis_for_game(game)
                        self.context[int(game_id)] = (game, markets, analysis)
                    except Exception as exc:
                        print(f"KHL background refresh: game {game_id}: {type(exc).__name__}: {exc}")

                # Remove games that are no longer on today's schedule.
                valid_ids = {int(g["id"]) for g in games if g.get("id") is not None}
                self.context = {key: value for key, value in self.context.items() if key in valid_ids}
                self.updated_at = time.time()
                print(f"KHL background refresh: {len(games)} games, {len(self.context)} fully refreshed")
            except Exception as exc:
                print(f"KHL background refresh failed: {type(exc).__name__}: {exc}")

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
