from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .providers.api_sport_ru import ApiSportRuClient
from .team_names import display_team_name


async def verified_today_games(client: ApiSportRuClient) -> list[dict[str, Any]]:
    """Return today's KHL schedule directly from API-SPORT.ru."""
    today = datetime.now(timezone.utc).date().isoformat()
    games = await client.khl_matches(today)
    result: list[dict[str, Any]] = []
    for raw in sorted(games, key=ApiSportRuClient._match_date):
        game = ApiSportRuClient.normalize_match(raw)
        teams = game.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        home["name"] = display_team_name(str(home.get("name") or ""))
        away["name"] = display_team_name(str(away.get("name") or ""))
        game["teams"] = {"home": home, "away": away}
        game["__api_sport_ru"] = True
        game["__raw_api_sport_ru"] = raw
        result.append(game)
    return result
