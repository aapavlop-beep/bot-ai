from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .providers.api_sport_ru import ApiSportRuClient
from .providers.khl_mobile import KHLMobileClient
from .providers.sofascore_khl import SofaScoreKHLClient


class EnhancedKHLService:
    """KHL service with API-SPORT.ru primary and official KHL mobile reserve."""

    def __init__(self, client: ApiSportRuClient) -> None:
        self.client = client
        self.api_sport_ru = client
        self.khl_mobile = KHLMobileClient()
        self.sofascore = SofaScoreKHLClient()

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

    async def _find_sofascore_match(self, game: dict[str, Any]) -> dict[str, Any] | None:
        teams = game.get("teams") or {}
        home = str((teams.get("home") or {}).get("name") or "")
        away = str((teams.get("away") or {}).get("name") or "")
        date = str(game.get("date") or game.get("datetime") or "")[:10]
        candidates = await self.sofascore.today_games(date)
        return next(
            (
                item for item in candidates
                if self._same_team(str((item.get("teams") or {}).get("home", {}).get("name") or ""), home)
                and self._same_team(str((item.get("teams") or {}).get("away", {}).get("name") or ""), away)
            ),
            None,
        )

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
            markets = self.api_sport_ru.markets_from_match(detail)
            if markets:
                return markets
        except Exception as exc:
            print(f"API-SPORT.ru odds failed: {type(exc).__name__}: {exc}; odds unavailable from reserve", flush=True)

        # The official KHL mobile API is the reliable reserve for match data,
        # but it is not treated as a bookmaker odds feed. Never invent odds.
        if game is not None and game.get("__khl_mobile"):
            print("KHL mobile reserve: match data available; bookmaker odds are unavailable", flush=True)
            return ()

        try:
            reserve_game = await self._find_sofascore_match(game or {}) if game else None
            if reserve_game and reserve_game.get("id") is not None:
                return await self.sofascore.markets_for_event(int(reserve_game["id"]))
        except Exception as fallback_exc:
            print(f"SofaScore KHL odds failed: {type(fallback_exc).__name__}: {fallback_exc}", flush=True)
        return ()

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Collect KHL context using layered sources without hiding source failures."""
        if game.get("__khl_mobile"):
            home = str(((game.get("teams") or {}).get("home") or {}).get("name") or "")
            away = str(((game.get("teams") or {}).get("away") or {}).get("name") or "")
            start = str(game.get("date") or game.get("datetime") or "")
            context = await self.khl_mobile.build_match_context(home, away, start)
            context["активный_источник_статистики"] = "Официальный KHL mobile API"
            context["резервный_источник"] = "KHL mobile API"
            return context

        if game.get("__sofascore"):
            context = await self.sofascore.build_context(game)
            context["активный_источник_статистики"] = "SofaScore"
            context["резервный_источник"] = "SofaScore"
            return context

        try:
            context = await self.api_sport_ru.build_context(game)
            context["активный_источник_статистики"] = "API-SPORT.ru"
            return context
        except Exception as exc:
            print(f"API-SPORT.ru KHL context failed: {type(exc).__name__}: {exc}; trying official KHL mobile API", flush=True)
            home = str(((game.get("teams") or {}).get("home") or {}).get("name") or "")
            away = str(((game.get("teams") or {}).get("away") or {}).get("name") or "")
            start = str(game.get("date") or game.get("datetime") or "")
            try:
                context = await self.khl_mobile.build_match_context(home, away, start)
                context["активный_источник_статистики"] = "Официальный KHL mobile API"
                context["резервный_источник"] = "KHL mobile API"
                return context
            except Exception as mobile_exc:
                print(f"Official KHL mobile API context failed: {type(mobile_exc).__name__}: {mobile_exc}; trying SofaScore last", flush=True)
            match = await self._find_sofascore_match(game)
            if match is None:
                raise RuntimeError("Не удалось получить данные КХЛ из API-SPORT.ru и официального KHL mobile API") from exc
            game.clear()
            game.update(match)
            context = await self.sofascore.build_context(game)
            context["активный_источник_статистики"] = "SofaScore"
            context["резервный_источник"] = "SofaScore LAST RESORT"
            return context

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
