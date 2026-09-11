from __future__ import annotations

from typing import Any

import httpx

from ..models import Sport


class ApiSportsError(RuntimeError):
    pass


class ApiSportsClient:
    """Client for API-Sports football and hockey APIs.

    API keys are never stored in source control. The caller supplies the key
    from environment-backed settings.
    """

    BASE_URLS = {
        Sport.FOOTBALL: "https://v3.football.api-sports.io",
        Sport.KHL: "https://v1.hockey.api-sports.io",
    }

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def get(self, sport: Sport, endpoint: str, **params: Any) -> dict[str, Any]:
        base = self.BASE_URLS.get(sport)
        if not base:
            raise ApiSportsError(f"Unsupported API-Sports sport: {sport}")

        headers = {"x-apisports-key": self.api_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{base}/{endpoint.lstrip('/')}", headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()

        errors = payload.get("errors") or {}
        if errors:
            raise ApiSportsError(str(errors))
        return payload

    async def football_fixtures(self, *, date: str | None = None, league: int | None = None, season: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if date:
            params["date"] = date
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        return (await self.get(Sport.FOOTBALL, "fixtures", **params)).get("response", [])

    async def football_odds(self, *, fixture: int | None = None, league: int | None = None, season: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if fixture is not None:
            params["fixture"] = fixture
        if league is not None:
            params["league"] = league
        if season is not None:
            params["season"] = season
        return (await self.get(Sport.FOOTBALL, "odds", **params)).get("response", [])

    async def football_statistics(self, fixture: int) -> list[dict[str, Any]]:
        return (await self.get(Sport.FOOTBALL, "fixtures/statistics", fixture=fixture)).get("response", [])

    async def football_h2h(self, h2h: str, last: int = 10) -> list[dict[str, Any]]:
        return (await self.get(Sport.FOOTBALL, "fixtures/headtohead", h2h=h2h, last=last)).get("response", [])

    async def hockey_games(self, *, date: str | None = None, league: int | None = None, season: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if date:
            params["date"] = date
        if league is not None:
            params["league"] = league
        if season:
            params["season"] = season
        return (await self.get(Sport.KHL, "games", **params)).get("response", [])

    async def hockey_odds(self, *, game: int | None = None, league: int | None = None, season: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if game is not None:
            params["game"] = game
        if league is not None:
            params["league"] = league
        if season:
            params["season"] = season
        return (await self.get(Sport.KHL, "odds", **params)).get("response", [])

    async def hockey_statistics(self, team: int, *, league: int | None = None, season: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"team": team}
        if league is not None:
            params["league"] = league
        if season:
            params["season"] = season
        return (await self.get(Sport.KHL, "teams/statistics", **params)).get("response", [])
