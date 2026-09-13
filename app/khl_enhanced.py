from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .providers.api_sport_ru import ApiSportRuClient
from .providers.sofascore_khl import SofaScoreKHLClient


class EnhancedKHLService:
    """KHL service with API-SPORT.ru primary and SofaScore reserve source."""

    def __init__(self, client: ApiSportRuClient) -> None:
        self.client = client
        self.api_sport_ru = client
        self.sofascore = SofaScoreKHLClient()

    async def markets_for_game(self, game_id: int, game: dict[str, Any] | None = None) -> tuple[Market, ...]:
        if game is not None and game.get("__sofascore"):
            try:
                markets = await self.sofascore.markets_for_event(game_id)
                if markets:
                    return markets
            except Exception as exc:
                print(f"SofaScore KHL odds failed: {type(exc).__name__}: {exc}", flush=True)
            return ()

        if game is not None:
            raw = game.get("__raw_api_sport_ru")
            if isinstance(raw, dict):
                markets = self.api_sport_ru.markets_from_match(raw)
                if markets:
                    return markets
        try:
            detail = await self.api_sport_ru.match_by_id(game_id)
            return self.api_sport_ru.markets_from_match(detail)
        except Exception as exc:
            print(f"API-SPORT.ru odds failed: {type(exc).__name__}: {exc}; trying SofaScore reserve", flush=True)
            try:
                return await self.sofascore.markets_for_event(game_id)
            except Exception as fallback_exc:
                print(f"SofaScore KHL odds failed: {type(fallback_exc).__name__}: {fallback_exc}", flush=True)
                return ()

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Collect context from the primary source or automatically use SofaScore reserve."""
        if game.get("__sofascore"):
            return await self.sofascore.build_context(game)
        try:
            return await self.api_sport_ru.build_context(game)
        except Exception as exc:
            print(f"API-SPORT.ru KHL context failed: {type(exc).__name__}: {exc}; trying SofaScore reserve", flush=True)
            # Match the same event by team names/date on SofaScore so the
            # reserve context can use its own event ID and team IDs.
            raw = game.get("__raw_api_sport_ru") or {}
            teams = game.get("teams") or {}
            home = str((teams.get("home") or {}).get("name") or "")
            away = str((teams.get("away") or {}).get("name") or "")
            date = str(game.get("date") or "")[:10]
            candidates = await self.sofascore.today_games(date)
            match = next(
                (item for item in candidates if self._same_team((item.get("teams") or {}).get("home", {}).get("name", ""), home)
                 and self._same_team((item.get("teams") or {}).get("away", {}).get("name", ""), away)),
                None,
            )
            if match is None:
                raise RuntimeError("Не удалось сопоставить матч с резервным источником SofaScore") from exc
            game.clear()
            game.update(match)
            return await self.sofascore.build_context(game)

    @staticmethod
    def _norm(value: str) -> str:
        value = value.lower().replace("ё", "е")
        for char in "—–-.,:()[]{}'\"":
            value = value.replace(char, " ")
        return " ".join(value.split())

    @classmethod
    def _same_team(cls, left: str, right: str) -> bool:
        a, b = cls._norm(left), cls._norm(right)
        if not a or not b:
            return False
        if a == b or a in b or b in a:
            return True
        at, bt = set(a.split()), set(b.split())
        return len(at & bt) >= max(1, min(len(at), len(bt)) - 1)

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
