from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx


class KHLHockeyTechError(RuntimeError):
    pass


class KHLHockeyTechClient:
    """Бесплатный адаптер к открытому KHL/HockeyTech proxy.

    Использует публичный proxy puckway/shayypy без API-ключа. Источник
    документирует расписание, таблицы, статистику игроков, play-by-play,
    игровые события и LIVE-данные.
    """

    PROXY = "https://khl.shayy.workers.dev/?url="
    MODULEKIT = "https://lscluster.hockeytech.com/feed/"
    GAMECENTER = "https://cluster.leaguestat.com/feed/"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def _url(self, base: str, params: dict[str, Any]) -> str:
        query = "&".join(
            f"{quote(str(key), safe='')}={quote(str(value), safe='')}"
            for key, value in params.items()
            if value is not None
        )
        target = f"{base}?{query}"
        return self.PROXY + quote(target, safe="")

    async def _get(self, base: str, params: dict[str, Any]) -> Any:
        url = self._url(base, params)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, trust_env=False) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            raise KHLHockeyTechError(f"KHL HockeyTech: {type(exc).__name__}: {exc}") from exc

    async def modulekit(self, view: str, **params: Any) -> Any:
        base_params = {
            "feed": "modulekit",
            "key": "khl",
            "fmt": "json",
            "client_code": "khl",
            "view": view,
            "lang": "ru",
        }
        base_params.update(params)
        return await self._get(self.MODULEKIT, base_params)

    async def gamecenter(self, game_id: int, tab: str) -> Any:
        return await self._get(
            self.GAMECENTER,
            {
                "feed": "gc",
                "key": "khl",
                "fmt": "json",
                "client_code": "khl",
                "game_id": game_id,
                "tab": tab,
                "lang_code": "ru",
            },
        )

    async def daily_schedule(self, day: date) -> Any:
        return await self.modulekit("gamesbydate", fetch_date=day.isoformat())

    async def games_per_day(self, start: date, end: date) -> Any:
        return await self.modulekit("gamesperday", start_date=start.isoformat(), end_date=end.isoformat())

    async def seasons(self) -> Any:
        return await self.modulekit("seasons")

    async def standings(self, season_id: int) -> Any:
        return await self.modulekit("statviewtype", season_id=season_id, stat="conference", type="standings")

    async def scorebar(self, ahead: int = 1, back: int = 1) -> Any:
        return await self.modulekit("scorebar", numberofdaysahead=ahead, numberofdaysback=back)

    async def game_summary(self, game_id: int) -> Any:
        return await self.gamecenter(game_id, "gamesummary")

    async def game_clock(self, game_id: int) -> Any:
        return await self.gamecenter(game_id, "clock")

    async def play_by_play(self, game_id: int) -> Any:
        return await self.gamecenter(game_id, "pxpverbose")

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("SiteKit", "sitekit", "games", "Games", "schedule", "Schedule", "data", "Data", "records", "Records", "teams", "Teams", "players", "Players"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = KHLHockeyTechClient._items(value)
                if nested:
                    return nested
        # HockeyTech sometimes wraps the useful object under a single key.
        for value in payload.values():
            if isinstance(value, list) and all(isinstance(x, dict) for x in value):
                return value
        return []

    @classmethod
    def _game_teams(cls, game: dict[str, Any]) -> tuple[str, str]:
        home = game.get("homeTeam") or game.get("home_team") or game.get("home") or {}
        away = game.get("awayTeam") or game.get("away_team") or game.get("away") or {}
        if not isinstance(home, dict):
            home = {}
        if not isinstance(away, dict):
            away = {}
        return (
            str(home.get("name") or home.get("teamName") or game.get("home_team_name") or ""),
            str(away.get("name") or away.get("teamName") or game.get("away_team_name") or ""),
        )

    @staticmethod
    def _game_date(game: dict[str, Any]) -> str:
        return str(game.get("date") or game.get("gameDate") or game.get("startDate") or game.get("start_time") or "")

    @classmethod
    def _matches_team(cls, game: dict[str, Any], team_name: str) -> bool:
        home, away = cls._game_teams(game)
        needle = team_name.strip().lower()
        return needle in home.lower() or needle in away.lower() or home.lower() in needle or away.lower() in needle

    @classmethod
    def compact_game(cls, game: dict[str, Any]) -> dict[str, Any]:
        home, away = cls._game_teams(game)
        return {
            "дата": cls._game_date(game)[:10],
            "хозяева": home,
            "гости": away,
            "счёт": game.get("finalScore") or game.get("score") or game.get("gameScore") or "",
            "статус": game.get("status") or game.get("gameStatus") or "",
            "id": game.get("id") or game.get("game_id") or game.get("gameId"),
        }

    async def recent_team_games(self, team_name: str, *, days: int = 45, limit: int = 10) -> list[dict[str, Any]]:
        end = date.today()
        start = end - timedelta(days=days)
        payload = await self.games_per_day(start, end)
        games = [g for g in self._items(payload) if self._matches_team(g, team_name)]
        games.sort(key=self._game_date, reverse=True)
        return [self.compact_game(g) for g in games[:limit]]

    @staticmethod
    def season_id_from_payload(payload: Any) -> int | None:
        items = KHLHockeyTechClient._items(payload)
        current_year = date.today().year
        candidates: list[tuple[int, int]] = []
        for item in items:
            raw_id = item.get("id") or item.get("season_id") or item.get("seasonId")
            label = str(item.get("name") or item.get("seasonName") or item.get("description") or "")
            try:
                sid = int(raw_id)
            except (TypeError, ValueError):
                continue
            score = 0
            if str(current_year) in label:
                score += 10
            if str(current_year + 1) in label:
                score += 10
            if "2026" in label:
                score += 20
            candidates.append((score, sid))
        return max(candidates)[1] if candidates else None
