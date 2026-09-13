from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .providers.khl_mobile import KHLMobileClient
from .providers.sofascore_khl import SofaScoreKHLClient
from .web_odds import markets_from_web_research
from .web_research import KHLWebResearcher


class EnhancedKHLService:
    """KHL analysis using official KHL data plus broad public web research."""

    def __init__(self) -> None:
        self.khl_mobile = KHLMobileClient()
        self.sofascore = SofaScoreKHLClient()
        self.web_research = KHLWebResearcher()

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

    async def markets_for_game(
        self,
        game_id: int,
        game: dict[str, Any] | None = None,
        analysis_data: dict[str, Any] | None = None,
    ) -> tuple[Market, ...]:
        # Bookmaker odds are collected from public web research first.
        if analysis_data:
            web_context = analysis_data.get("веб_исследование") or {}
            markets, meta = markets_from_web_research(web_context)
            analysis_data["веб_линии"] = meta
            if markets:
                analysis_data["режим_линии"] = "web bookmaker research"
                print(
                    f"KHL web odds: found {len(markets)} markets from {meta.get('основной_источник', {}).get('домен', 'web')}",
                    flush=True,
                )
                return markets

        # SofaScore remains only an optional public-data fallback for odds.
        try:
            reserve_game = None
            if game and game.get("__sofascore"):
                reserve_game = game
            elif game:
                reserve_game = await self._find_sofascore_match(game)
            if reserve_game and reserve_game.get("id") is not None:
                markets = await self.sofascore.markets_for_event(int(reserve_game["id"]))
                if markets:
                    if analysis_data is not None:
                        analysis_data["режим_линии"] = "SofaScore fallback"
                    return markets
        except Exception as exc:
            print(f"SofaScore KHL odds fallback failed: {type(exc).__name__}: {exc}", flush=True)
        print("KHL odds: no confirmed bookmaker line found on public web", flush=True)
        return ()

    async def _web_context(self, home: str, away: str, start: str) -> dict[str, Any]:
        date = start[:10] or ""
        try:
            return await self.web_research.research_match(home, away, date)
        except Exception as exc:
            print(f"KHL web research failed: {type(exc).__name__}: {exc}", flush=True)
            return {
                "собрано_в_utc": "",
                "метод": "web search + page extraction",
                "источники": [],
                "ошибка": type(exc).__name__,
            }

    @staticmethod
    def _merge_web_context(context: dict[str, Any], web_context: dict[str, Any]) -> dict[str, Any]:
        context["веб_исследование"] = web_context
        context["веб_источники"] = web_context.get("источники", [])
        context["источники_проверки"] = [
            "Официальный KHL Mobile API",
            "Официальные/спортивные сайты через web search",
            *([context.get("активный_источник_статистики")] if context.get("активный_источник_статистики") else []),
        ]
        context["режим_источника"] = "KHL mobile API + multi-source web research"
        context["правило_травм_и_составов"] = (
            "Не считать игрока травмированным или отсутствующим без подтверждённого текста источника. "
            "Для свежих кадровых новостей приоритет официальному клубу/KHL.ru; дата публикации обязательна для оценки свежести."
        )
        context["правило_коэффициентов"] = (
            "Использовать только коэффициенты, найденные на разрешённых публичных букмекерских страницах или "
            "агрегаторах с явным временем/датой обновления. Случайное число из сниппета не считать линией. "
            "При отсутствии подтверждения линия не создаётся и ИИ не имеет права её выдумывать."
        )
        context["статистика_для_ии"] = {
            "базовые_данные": context.get("статистика_для_ии", context.copy()),
            "веб_исследование": web_context,
        }
        return context

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        home = str(((game.get("teams") or {}).get("home") or {}).get("name") or "")
        away = str(((game.get("teams") or {}).get("away") or {}).get("name") or "")
        start = str(game.get("date") or game.get("datetime") or "")

        web_context = await self._web_context(home, away, start)

        if game.get("__khl_mobile"):
            context = await self.khl_mobile.build_match_context(home, away, start)
            context["активный_источник_статистики"] = "Официальный KHL Mobile API + web research"
            context["резервный_источник"] = "web research"
            return self._merge_web_context(context, web_context)

        if game.get("__sofascore"):
            context = await self.sofascore.build_context(game)
            context["активный_источник_статистики"] = "SofaScore + web research"
            context["резервный_источник"] = "web research"
            return self._merge_web_context(context, web_context)

        # A game should normally come from the KHL Mobile schedule. If a
        # manually supplied game reaches analysis, use KHL Mobile directly.
        try:
            context = await self.khl_mobile.build_match_context(home, away, start)
            context["активный_источник_статистики"] = "Официальный KHL Mobile API + web research"
            context["резервный_источник"] = "web research"
            return self._merge_web_context(context, web_context)
        except Exception as exc:
            print(f"Official KHL Mobile context failed: {type(exc).__name__}: {exc}; using web research", flush=True)
            context = {
                "активный_источник_статистики": "web research",
                "резервный_источник": "web search",
                "источники": [],
            }
            return self._merge_web_context(context, web_context)

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
        return f"🏒 <b>{match.home} — {match.away}</b>\n🕒 {match.start_time}\n🏆 {match.league}\n"
