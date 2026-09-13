from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from .providers.api_sport_ru import ApiSportRuClient
from .providers.sofascore_khl import SofaScoreKHLClient
from .team_names import display_team_name


async def verified_today_games(client: ApiSportRuClient) -> list[dict[str, Any]]:
    """Return today's KHL schedule with API-SPORT.ru as primary and SofaScore as reserve."""
    today = datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    try:
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
        if result:
            print(f"KHL schedule source: API-SPORT.ru ({len(result)} games)", flush=True)
            return result
        print("KHL schedule source: API-SPORT.ru returned no games; switching to SofaScore", flush=True)
    except Exception as exc:
        print(f"KHL primary source failed: {type(exc).__name__}: {exc}; switching to SofaScore", flush=True)

    fallback = SofaScoreKHLClient()
    games = await fallback.today_games(today)
    result: list[dict[str, Any]] = []
    for game in games:
        teams = game.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        home["name"] = display_team_name(str(home.get("name") or ""))
        away["name"] = display_team_name(str(away.get("name") or ""))
        game["teams"] = {"home": home, "away": away}
        result.append(game)
    print(f"KHL schedule source: SofaScore RESERVE ({len(result)} games)", flush=True)
    return result
