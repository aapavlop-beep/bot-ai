from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any

from .providers.api_sport_ru import ApiSportRuClient
from .providers.khl_mobile import KHLMobileClient
from .providers.khl_hockeytech import KHLHockeyTechClient
from .providers.sofascore_khl import SofaScoreKHLClient
from .team_names import display_team_name


def _mobile_event_date(event: dict[str, Any], fallback: str) -> str:
    """Normalize KHL mobile timestamps without assuming seconds vs milliseconds."""
    value = event.get("start_at")
    if value in (None, ""):
        return fallback
    try:
        timestamp = float(value)
        # KHL mobile API normally uses Unix seconds, but some responses may
        # contain milliseconds. Convert only when the value is clearly ms.
        if timestamp > 100_000_000_000:
            timestamp /= 1000.0
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo("Europe/Moscow")).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return fallback


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
        date_value = _mobile_event_date(event, today)
        # The API query already restricts the window to this Moscow day. Keep
        # malformed timestamps from killing the reserve source.
        if date_value != today:
            continue
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
        print("Official KHL mobile API returned no games; trying SofaScore last", flush=True)
    except Exception as exc:
        print(f"Official KHL mobile API failed: {type(exc).__name__}: {exc}; trying SofaScore last", flush=True)

    # HockeyTech is intentionally disabled in this project, so do not spend a
    # request on a provider that can only return a known disabled-provider error.
    try:
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
            game["__sofascore"] = True
            result.append(game)
        print(f"KHL schedule source: SofaScore LAST RESORT ({len(result)} games)", flush=True)
        return result
    except Exception as exc:
        print(f"SofaScore last resort failed: {type(exc).__name__}: {exc}", flush=True)
        return []
