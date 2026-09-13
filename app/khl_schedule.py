from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from .providers.api_sport_ru import ApiSportRuClient
from .providers.khl_mobile import KHLMobileClient
from .providers.sofascore_khl import SofaScoreKHLClient
from .team_names import display_team_name

MSK = ZoneInfo("Europe/Moscow")


def _mobile_event_date(event: dict[str, Any], fallback: str) -> str:
    value = event.get("start_at")
    if value in (None, ""):
        return fallback
    try:
        timestamp = float(value)
        if timestamp > 100_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=MSK).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return fallback


async def _mobile_games_for_date(date_value: str) -> list[dict[str, Any]]:
    client = KHLMobileClient()
    start = datetime.fromisoformat(date_value).replace(tzinfo=MSK)
    end = start.replace(hour=23, minute=59, second=59)
    events = await client.events(start=start, end=end)
    result: list[dict[str, Any]] = []
    for event in events:
        a = event.get("team_a") or {}
        b = event.get("team_b") or {}
        event_id = event.get("id")
        if event_id is None or not a.get("name") or not b.get("name"):
            continue
        actual_date = _mobile_event_date(event, date_value)
        if actual_date != date_value:
            continue
        result.append({
            "id": int(event_id),
            "date": actual_date,
            "datetime": actual_date,
            "teams": {
                "home": {"id": a.get("id"), "name": display_team_name(str(a.get("name")))},
                "away": {"id": b.get("id"), "name": display_team_name(str(b.get("name")))},
            },
            "league": {"name": "КХЛ"},
            "__khl_mobile": True,
            "__raw_khl_mobile": event,
        })
    return result


async def verified_games_for_date(client: ApiSportRuClient, date_value: str) -> list[dict[str, Any]]:
    """Return the KHL schedule for an exact Moscow date with layered failover."""
    try:
        games = await client.khl_matches(date_value)
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
            print(f"KHL schedule source: API-SPORT.ru ({len(result)} games) date={date_value}", flush=True)
            return result
        print(f"KHL API-SPORT.ru returned no games date={date_value}; trying official KHL mobile API", flush=True)
    except Exception as exc:
        print(f"KHL API-SPORT.ru failed date={date_value}: {type(exc).__name__}: {exc}; trying official KHL mobile API", flush=True)

    try:
        result = await _mobile_games_for_date(date_value)
        if result:
            print(f"KHL schedule source: OFFICIAL KHL MOBILE API RESERVE ({len(result)} games) date={date_value}", flush=True)
            return result
        print(f"Official KHL mobile API returned no games date={date_value}; trying SofaScore last", flush=True)
    except Exception as exc:
        print(f"Official KHL mobile API failed date={date_value}: {type(exc).__name__}: {exc}; trying SofaScore last", flush=True)

    try:
        fallback = SofaScoreKHLClient()
        games = await fallback.today_games(date_value)
        result: list[dict[str, Any]] = []
        for game in games:
            teams = game.get("teams") or {}
            home = teams.get("home") or {}
            away = teams.get("away") or {}
            home["name"] = display_team_name(str(home.get("name") or ""))
            away["name"] = display_team_name(str(away.get("name") or ""))
            game["teams"] = {"home": home, "away": away}
            game["__sofascore"] = True
            result.append(game)
        print(f"KHL schedule source: SofaScore LAST RESORT ({len(result)} games) date={date_value}", flush=True)
        return result
    except Exception as exc:
        print(f"SofaScore last resort failed date={date_value}: {type(exc).__name__}: {exc}", flush=True)
        return []


async def verified_today_games(client: ApiSportRuClient) -> list[dict[str, Any]]:
    today = datetime.now(MSK).date().isoformat()
    return await verified_games_for_date(client, today)


def upcoming_moscow_dates(days: int = 3) -> list[str]:
    today = datetime.now(MSK).date()
    return [(today + timedelta(days=i)).isoformat() for i in range(max(1, days))]
