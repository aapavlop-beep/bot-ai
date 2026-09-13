from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from .providers.api_sport_ru import ApiSportRuClient
from .providers.khl_mobile import KHLMobileClient
from .providers.khl_hockeytech import KHLHockeyTechClient
from .providers.sofascore_khl import SofaScoreKHLClient
from .team_names import display_team_name


async def _mobile_today_games(today: str) -> list[dict[str, Any]]:
    client = KHLMobileClient()
    start = datetime.fromisoformat(today).replace(tzinfo=ZoneInfo("Europe/Moscow"))
    end = start.replace(hour=23, minute=59, second=59)
    events = await client.events(start=start, end=end)
    result: list[dict[str, Any]] = []
    for event in events:
        a = event.get("team_a") or {}
        b = event.get("team_b") or {}
        event_id = event.get("id")
        if event_id is None or not a.get("name") or not b.get("name"):
            continue
        start_at = event.get("start_at")
        date_value = datetime.fromtimestamp(int(start_at), tz=ZoneInfo("Europe/Moscow")).isoformat() if start_at else today
        result.append({
            "id": int(event_id),
            "date": date_value,
            "datetime": date_value,
            "teams": {
                "home": {"id": a.get("id"), "name": display_team_name(str(a.get("name")))},
                "away": {"id": b.get("id"), "name": display_team_name(str(b.get("name")))},
            },
            "league": {"name": "КХЛ"},
            "__khl_mobile": True,
            "__raw_khl_mobile": event,
        })
    return result


async def verified_today_games(client: ApiSportRuClient) -> list[dict[str, Any]]:
    """Return today's KHL schedule with layered failover."""
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
        print("KHL API-SPORT.ru returned no games; trying official KHL mobile API", flush=True)
    except Exception as exc:
        print(f"KHL API-SPORT.ru failed: {type(exc).__name__}: {exc}; trying official KHL mobile API", flush=True)

    try:
        result = await _mobile_today_games(today)
        if result:
            print(f"KHL schedule source: OFFICIAL KHL MOBILE API RESERVE ({len(result)} games)", flush=True)
            return result
        print("Official KHL mobile API returned no games; trying HockeyTech reserve", flush=True)
    except Exception as exc:
        print(f"Official KHL mobile API failed: {type(exc).__name__}: {exc}; trying HockeyTech reserve", flush=True)

    try:
        hockeytech = KHLHockeyTechClient()
        payload = await hockeytech.daily_schedule(today)
        raw_items = payload if isinstance(payload, list) else ((payload.get("SiteKit") or payload.get("games") or payload.get("data") or []) if isinstance(payload, dict) else [])
        result = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            home = item.get("homeTeam") or item.get("home_team") or item.get("home") or {}
            away = item.get("awayTeam") or item.get("away_team") or item.get("away") or {}
            home_name = home.get("name") if isinstance(home, dict) else home
            away_name = away.get("name") if isinstance(away, dict) else away
            game_id = item.get("id") or item.get("game_id")
            if not game_id or not home_name or not away_name:
                continue
            result.append({
                "id": int(game_id),
                "date": str(item.get("date") or item.get("startTime") or today),
                "teams": {
                    "home": {"id": home.get("id") if isinstance(home, dict) else None, "name": display_team_name(str(home_name))},
                    "away": {"id": away.get("id") if isinstance(away, dict) else None, "name": display_team_name(str(away_name))},
                },
                "league": {"name": "КХЛ"},
                "__khl_hockeytech": True,
                "__raw_khl_hockeytech": item,
            })
        if result:
            print(f"KHL schedule source: HockeyTech RESERVE ({len(result)} games)", flush=True)
            return result
    except Exception as exc:
        print(f"KHL HockeyTech reserve failed: {type(exc).__name__}: {exc}; trying SofaScore last", flush=True)

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
    print(f"KHL schedule source: SofaScore LAST RESORT ({len(result)} games)", flush=True)
    return result
