from __future__ import annotations

"""Legacy KHL/HockeyTech adapter intentionally disabled.

KHL sports data must come exclusively from API-SPORT.ru. This module is kept
only for backward compatibility with old imports; it performs no HTTP calls.
"""


class KHLHockeyTechError(RuntimeError):
    pass


class KHLHockeyTechClient:
    """Disabled legacy provider. No network requests are made."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def _disabled(self, *args, **kwargs):
        raise KHLHockeyTechError("KHL HockeyTech provider is disabled; use API-SPORT.ru only")

    modulekit = _disabled
    gamecenter = _disabled
    daily_schedule = _disabled
    games_per_day = _disabled
    seasons = _disabled
    standings = _disabled
    scorebar = _disabled
    game_summary = _disabled
    game_clock = _disabled
    play_by_play = _disabled
    recent_team_games = _disabled
    head_to_head = _disabled

    @staticmethod
    def season_id_from_payload(payload):
        return None

    @staticmethod
    def compact_game(game):
        return {}
