from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .providers.api_sports import ApiSportsClient
from .providers.khl_hockeytech import KHLHockeyTechClient
from .team_names import display_team_name


def _norm_team(value: str) -> str:
    """Normalize both API-Sports and HockeyTech names to the same club name."""
    return display_team_name(value).lower().replace("ё", "е").strip()


def _pair(game: dict[str, Any]) -> tuple[str, str]:
    teams = game.get("teams") or {}
    home = str((teams.get("home") or {}).get("name") or "")
    away = str((teams.get("away") or {}).get("name") or "")
    return _norm_team(home), _norm_team(away)


def _ht_pair(game: dict[str, Any]) -> tuple[str, str]:
    home, away = KHLHockeyTechClient._game_teams(game)
    return _norm_team(home), _norm_team(away)


def _same_pair(api_game: dict[str, Any], ht_game: dict[str, Any]) -> bool:
    ah, aa = _pair(api_game)
    hh, ha = _ht_pair(ht_game)
    return ah == hh and aa == ha


def _placeholder(ht_game: dict[str, Any]) -> dict[str, Any]:
    home, away = KHLHockeyTechClient._game_teams(ht_game)
    return {
        "id": None,
        "__schedule_only": True,
        "__schedule_game_id": ht_game.get("id") or ht_game.get("game_id") or ht_game.get("gameId"),
        "date": KHLHockeyTechClient._game_date(ht_game),
        "teams": {"home": {"name": display_team_name(home)}, "away": {"name": display_team_name(away)}},
        "league": {"name": "KHL"},
    }


async def verified_today_games(client: ApiSportsClient) -> list[dict[str, Any]]:
    """Return the official KHL daily schedule enriched with API-Sports IDs.

    HockeyTech is used as a completeness check because API-Sports can temporarily
    omit a scheduled/live KHL game. Team aliases are normalized before matching,
    so city-only names such as Khabarovsk/Cherepovets still match the club record.
    """
    today = datetime.now(timezone.utc).date()
    api_games = await client.hockey_games(date=today.isoformat())
    api_khl = [
        game for game in api_games
        if str((game.get("league") or {}).get("name") or "").strip().lower() == "khl"
    ]

    try:
        hockeytech = KHLHockeyTechClient()
        payload = await hockeytech.daily_schedule(today)
        ht_games = KHLHockeyTechClient._items(payload)
    except Exception:
        return [
            {**game, "teams": {
                "home": {**(game.get("teams") or {}).get("home", {}), "name": display_team_name(str(((game.get("teams") or {}).get("home") or {}).get("name") or ""))},
                "away": {**(game.get("teams") or {}).get("away", {}), "name": display_team_name(str(((game.get("teams") or {}).get("away") or {}).get("name") or ""))},
            }}
            for game in api_khl
        ]

    scheduled = [game for game in ht_games if all(_ht_pair(game))]
    if not scheduled:
        return api_khl

    result: list[dict[str, Any]] = []
    used: set[int] = set()
    for ht_game in sorted(scheduled, key=KHLHockeyTechClient._game_date):
        found_index = None
        for index, api_game in enumerate(api_khl):
            if index in used:
                continue
            if _same_pair(api_game, ht_game):
                found_index = index
                break
        if found_index is not None:
            used.add(found_index)
            result.append(api_khl[found_index])
        else:
            result.append(_placeholder(ht_game))

    result.extend(game for index, game in enumerate(api_khl) if index not in used)
    return result
