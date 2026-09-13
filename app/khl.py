from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .models import Market, Match, Sport
from .web_odds_search import search_bookmaker_web
from .web_research import KHLWebResearcher

MSK = ZoneInfo("Europe/Moscow")


class KHLService:
    """Compatibility KHL service backed exclusively by browser research."""

    def __init__(self, client: Any | None = None) -> None:
        # Kept only for compatibility with older code. The client is never used.
        self.client = None
        self.web_research = KHLWebResearcher()

    @staticmethod
    def _teams(game: dict[str, Any]) -> tuple[str, str]:
        teams = game.get("teams") or {}
        home = (teams.get("home") or {}).get("name") or "Хозяева"
        away = (teams.get("away") or {}).get("name") or "Гости"
        return str(home), str(away)

    @staticmethod
    def _start_time(game: dict[str, Any]) -> str:
        return str(game.get("date") or game.get("datetime") or "")

    async def today_games(self) -> list[dict[str, Any]]:
        from .khl_schedule import verified_today_games
        return await verified_today_games()

    async def games_for_date(self, date: str) -> list[dict[str, Any]]:
        from .khl_schedule import verified_games_for_date
        return await verified_games_for_date(date)

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        home, away = self._teams(game)
        date = self._start_time(game)[:10]
        web = await self.web_research.research_match(home, away, date)
        return {
            "активный_источник_статистики": "browser web research",
            "режим_источника": "browser-only web research",
            "веб_исследование": web,
            "веб_источники": web.get("источники", []),
            "статистика_для_ии": web,
            "источники_проверки": [s.get("url") for s in web.get("источники", []) if s.get("url")][:20],
            "правило_травм_и_составов": "Использовать только явно подтверждённые браузерными источниками сведения; отсутствие игрока не доказывает травму.",
            "правило_коэффициентов": "Только подтверждённые браузером Winline, Фонбет, BetBoom или Parimatch. API не используется.",
        }

    async def markets_for_game(self, game_id: int, game: dict[str, Any] | None = None) -> tuple[Market, ...]:
        if not game:
            return ()
        home, away = self._teams(game)
        date = self._start_time(game)[:10]
        odds, _ = await search_bookmaker_web(home, away, date)
        return tuple(Market(name, odd, 1 / odd) for name, odd in odds.items())

    @staticmethod
    def to_match(game: dict[str, Any], markets: tuple[Market, ...] = (), analysis_data: dict[str, Any] | None = None) -> Match:
        home, away = KHLService._teams(game)
        return Match(
            sport=Sport.KHL,
            league=str((game.get("league") or {}).get("name") or "КХЛ"),
            home=home,
            away=away,
            start_time=KHLService._start_time(game),
            markets=markets,
            analysis_data=analysis_data or {},
        )
