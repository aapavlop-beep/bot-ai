from __future__ import annotations

from datetime import datetime
from typing import Any

import aiohttp

from .base import Event, Market, SportsProvider


class OddsApiProvider(SportsProvider):
    """Adapter for The Odds API. API key is read from settings by the caller."""

    BASE_URL = "https://api.the-odds-api.com/v4"

    SPORT_KEYS = {
        "football": "soccer_epl",
        "hockey": "icehockey_nhl",
        # CS2 and KHL coverage must be validated against the provider's current sport list.
    }

    def __init__(self, api_key: str, regions: str = "eu") -> None:
        self.api_key = api_key
        self.regions = regions

    async def events(self, sport: str) -> list[Event]:
        sport_key = self.SPORT_KEYS.get(sport)
        if not sport_key:
            return []

        params = {
            "apiKey": self.api_key,
            "regions": self.regions,
            "oddsFormat": "decimal",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.BASE_URL}/sports/{sport_key}/odds",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                response.raise_for_status()
                payload: list[dict[str, Any]] = await response.json()

        events: list[Event] = []
        for item in payload:
            events.append(
                Event(
                    id=str(item["id"]),
                    sport=sport,
                    league=sport_key,
                    home=item["home_team"],
                    away=item["away_team"],
                    start_time=datetime.fromisoformat(item["commence_time"].replace("Z", "+00:00")),
                    metadata={"raw": item},
                )
            )
        return events

    async def markets(self, event_id: str) -> list[Market]:
        # Event-level odds are returned by events(); this method is reserved for
        # a future normalized event/market endpoint implementation.
        return []
