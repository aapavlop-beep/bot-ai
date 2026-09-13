from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .providers.api_sport_ru import ApiSportRuClient


class EnhancedKHLService:
    """KHL service backed exclusively by API-SPORT.ru.

    The daily matches response is the primary source. Odds are read from that
    response first. Historical form is built from two cached team-match feeds
    only when a prediction is requested. No HockeyTech/SofaScore fallback is
    used anywhere in the active KHL path.
    """

    def __init__(self, client: ApiSportRuClient) -> None:
        self.client = client
        self.api_sport_ru = client

    async def markets_for_game(self, game_id: int, game: dict[str, Any] | None = None) -> tuple[Market, ...]:
        if game is not None:
            raw = game.get("__raw_api_sport_ru")
            if isinstance(raw, dict):
                markets = self.api_sport_ru.markets_from_match(raw)
                if markets:
                    return markets
        # Fallback: one detail request, only if the daily feed did not contain odds.
        detail = await self.api_sport_ru.match_by_id(game_id)
        return self.api_sport_ru.markets_from_match(detail)

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Collect pre-match context exclusively from API-SPORT.ru."""
        return await self.api_sport_ru.build_context(game)

    @staticmethod
    def to_match(game: dict[str, Any], markets: tuple[Market, ...] = (), analysis_data: dict[str, Any] | None = None) -> Match:
        teams = game.get("teams") or {}
        home_obj = teams.get("home") or {}
        away_obj = teams.get("away") or {}
        home = str(home_obj.get("name") or "Хозяева")
        away = str(away_obj.get("name") or "Гости")
        league = str((game.get("league") or {}).get("name") or "КХЛ")
        start_time = str(game.get("date") or game.get("datetime") or "")
        return Match(
            sport=Sport.KHL,
            league=league,
            home=home,
            away=away,
            start_time=start_time,
            markets=markets,
            analysis_data=analysis_data or {},
        )

    @staticmethod
    def format_game(match: Match) -> str:
        return f"🏒 <b>{match.home} — {match.away}</b>\n🕒 {match.start_time}\n🏆 {match.league}"
