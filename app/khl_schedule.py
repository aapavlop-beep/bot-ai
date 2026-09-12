from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .providers.api_sport_ru import ApiSportRuClient
from .providers.api_sports import ApiSportsClient
from .team_names import display_team_name


def _norm(value: str) -> str:
    return display_team_name(value).lower().replace("ё", "е").strip()


def _api_pair(game: dict[str, Any]) -> tuple[str, str]:
    teams = game.get("teams") or {}
    return _norm(str((teams.get("home") or {}).get("name") or "")), _norm(str((teams.get("away") or {}).get("name") or ""))


def _ru_pair(game: dict[str, Any]) -> tuple[str, str]:
    home, away = ApiSportRuClient._teams(game)
    return _norm(home), _norm(away)


def _same(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a in b or b in a or bool(set(a.split()) & set(b.split()))


def _same_pair(api_game: dict[str, Any], ru_game: dict[str, Any]) -> bool:
    ah, aa = _api_pair(api_game)
    rh, ra = _ru_pair(ru_game)
    return _same(ah, rh) and _same(aa, ra)


def _placeholder(ru_game: dict[str, Any]) -> dict[str, Any]:
    home, away = ApiSportRuClient._teams(ru_game)
    return {
        "id": None,
        "__schedule_only": True,
        "__api_sport_ru": True,
        "__raw_api_sport_ru": ru_game,
        "date": ApiSportRuClient._match_date(ru_game),
        "teams": {"home": {"name": display_team_name(home)}, "away": {"name": display_team_name(away)}},
        "league": {"name": "КХЛ"},
    }


async def verified_today_games(client: ApiSportsClient) -> list[dict[str, Any]]:
    """API-SPORT.ru is authoritative for today's KHL schedule.

    API-Sports is queried only to attach the existing numeric IDs required by
    the legacy odds/cache layer. HockeyTech is not used to decide the schedule.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    from .config import settings

    ru_games: list[dict[str, Any]] = []
    if settings.api_sport_ru_key:
        try:
            ru_client = ApiSportRuClient(settings.api_sport_ru_key)
            ru_games = await ru_client.khl_matches(today)
        except Exception as exc:
            print(f"API-SPORT.ru schedule error: {type(exc).__name__}: {exc}")

    api_games = await client.hockey_games(date=today)
    api_khl = [g for g in api_games if str((g.get("league") or {}).get("name") or "").strip().lower() == "khl"]

    if not ru_games:
        return api_khl

    result: list[dict[str, Any]] = []
    used: set[int] = set()
    for ru_game in sorted(ru_games, key=ApiSportRuClient._match_date):
        found = None
        for index, api_game in enumerate(api_khl):
            if index not in used and _same_pair(api_game, ru_game):
                found = index
                break
        if found is None:
            result.append(_placeholder(ru_game))
            continue
        used.add(found)
        game = dict(api_khl[found])
        game["__api_sport_ru"] = True
        game["__raw_api_sport_ru"] = ru_game
        result.append(game)
    return result
