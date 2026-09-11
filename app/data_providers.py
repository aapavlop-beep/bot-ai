from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from .config import settings
from .models import Match, Market, Sport


@dataclass(frozen=True)
class ProviderResult:
    matches: tuple[Match, ...]
    provider: str


class SportsProvider(ABC):
    sport: Sport

    @abstractmethod
    async def upcoming(self) -> ProviderResult:
        raise NotImplementedError


class OddsApiProvider(SportsProvider):
    """Adapter for The Odds API.

    It is intentionally isolated from prediction logic. Availability of a
    competition/market must be checked in the provider layer rather than
    assumed by the model.
    """

    def __init__(self, sport: Sport, api_sport_key: str):
        self.sport = sport
        self.api_sport_key = api_sport_key

    async def upcoming(self) -> ProviderResult:
        if not settings.odds_api_key:
            return ProviderResult((), "the-odds-api:disabled")

        url = f"https://api.the-odds-api.com/v4/sports/{self.api_sport_key}/odds"
        params: dict[str, Any] = {
            "apiKey": settings.odds_api_key,
            "regions": settings.odds_api_regions,
            "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()

        matches: list[Match] = []
        for item in payload:
            markets: list[Market] = []
            for bookmaker in item.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        price = float(outcome.get("price", 0))
                        if price > 1:
                            markets.append(
                                Market(
                                    name=outcome.get("name", market.get("key", "market")),
                                    odds=price,
                                    probability=0.0,
                                )
                            )
            matches.append(
                Match(
                    sport=self.sport,
                    league=item.get("sport_title", self.api_sport_key),
                    home=item.get("home_team", ""),
                    away=item.get("away_team", ""),
                    start_time=item.get("commence_time", ""),
                    markets=tuple(markets),
                )
            )
        return ProviderResult(tuple(matches), "the-odds-api")
