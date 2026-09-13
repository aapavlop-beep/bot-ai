from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .providers.api_sport_ru import ApiSportRuClient
from .providers.khl_mobile import KHLMobileClient
from .providers.sofascore_khl import SofaScoreKHLClient
from .web_odds import markets_from_web_research
from .web_research import KHLWebResearcher


class EnhancedKHLService:
    """KHL service with layered API data plus broad public web research."""

    def __init__(self, client: ApiSportRuClient) -> None:
        self.client = client
        self.api_sport_ru = client
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
        if game is not None and game.get("__sofascore"):
            try:
                markets = await self.sofascore.markets_for_event(game_id)
                if markets:
                    return markets
            except Exception as exc:
                print(f"SofaScore KHL odds failed: {type(exc).__name__}: {exc}", flush=True)

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
            print(f"API-SPORT.ru odds failed: {type(exc).__name__}: {exc}; trying web bookmaker line", flush=True)

        # The official KHL API is not a bookmaker feed. Use the already collected
        # public-web evidence instead of declaring the line unavailable.
        if analysis_data:
            web_context = analysis_data.get("веб_исследование") or {}
            markets, meta = markets_from_web_research(web_context)
            if markets:
                analysis_data["веб_линии"] = meta
                analysis_data["режим_линии"] = "web bookmaker research"
                print(
                    f"KHL web odds: found {len(markets)} markets from {meta.get('основной_источник', {}).get('домен', 'web')}",
                    flush=True,
                )
                return markets
            analysis_data["веб_линии"] = meta

        # Last resort: if a SofaScore event can be resolved, use its markets.
        try:
            reserve_game = await self._find_sofascore_match(game or {}) if game else None
            if reserve_game and reserve_game.get("id") is not None:
                markets = await self.sofascore.markets_for_event(int(reserve_game["id"]))
                if markets:
                    return markets
        except Exception as fallback_exc:
            print(f"SofaScore KHL odds failed: {type(fallback_exc).__name__}: {fallback_exc}", flush=True)
        print("KHL odds: no confirmed bookmaker line found on API or public web", flush=True)
        return ()

    async def _web_context(self, home: str, away: str, start: str) -> dict[str, Any]:
        """Collect fresh public-web evidence for the match.

        This is intentionally independent from sports APIs: it searches the
        public web, follows result pages, and preserves source URLs/snippets so
        the AI can distinguish confirmed facts from missing information.
        """
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
            "Официальные/спортивные сайты через web search",
            *([context.get("активный_источник_статистики")] if context.get("активный_источник_статистики") else []),
        ]
        context["режим_источника"] = "API + multi-source web research"
        context["правило_травм_и_составов"] = (
            "Не считать игрока травмированным или отсутствующим без подтверждённого текста источника. "
            "Для свежих кадровых новостей приоритет официальному клубу/KHL.ru; дата публикации обязательна для оценки свежести."
        )
        context["правило_коэффициентов"] = (
            "Веб-страница с коэффициентом может быть использована как подтверждённая публичная линия, "
            "если в извлечённом тексте есть полный рынок и источник относится к разрешённому списку. "
            "Иначе линия не создаётся и ИИ не имеет права её выдумывать."
        )
        context["статистика_для_ии"] = {
            "базовые_данные": context.get("статистика_для_ии", context.copy()),
            "веб_исследование": web_context,
        }
        return context

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Collect structured API/reserve data and broad web evidence."""
        home = str(((game.get("teams") or {}).get("home") or {}).get("name") or "")
        away = str(((game.get("teams") or {}).get("away") or {}).get("name") or "")
        start = str(game.get("date") or game.get("datetime") or "")

        # Web research is always performed. It is not disabled when an API
        # happens to work, because injuries, lineups and fresh news often live
        # only in club/media pages.
        web_context = await self._web_context(home, away, start)

        if game.get("__khl_mobile"):
            context = await self.khl_mobile.build_match_context(home, away, start)
            context["активный_источник_статистики"] = "Официальный KHL mobile API + web research"
            context["резервный_источник"] = "KHL mobile API"
            return self._merge_web_context(context, web_context)

        if game.get("__sofascore"):
            context = await self.sofascore.build_context(game)
            context["активный_источник_статистики"] = "SofaScore + web research"
            context["резервный_источник"] = "SofaScore"
            return self._merge_web_context(context, web_context)

        try:
            context = await self.api_sport_ru.build_context(game)
            context["активный_источник_статистики"] = "API-SPORT.ru + web research"
            return self._merge_web_context(context, web_context)
        except Exception as exc:
            print(f"API-SPORT.ru KHL context failed: {type(exc).__name__}: {exc}; trying official KHL mobile API", flush=True)
            try:
                context = await self.khl_mobile.build_match_context(home, away, start)
                context["активный_источник_статистики"] = "Официальный KHL mobile API + web research"
                context["резервный_источник"] = "KHL mobile API"
                return self._merge_web_context(context, web_context)
            except Exception as mobile_exc:
                print(f"Official KHL mobile API context failed: {type(mobile_exc).__name__}: {mobile_exc}; trying SofaScore last", flush=True)
            try:
                match = await self._find_sofascore_match(game)
            except Exception as sofa_exc:
                print(f"SofaScore lookup failed: {type(sofa_exc).__name__}: {sofa_exc}", flush=True)
                match = None
            if match is None:
                context = {
                    "активный_источник_статистики": "web research",
                    "резервный_источник": "web search",
                    "ошибка_API": type(exc).__name__,
                    "источники": [],
                }
                return self._merge_web_context(context, web_context)
            game.clear()
            game.update(match)
            context = await self.sofascore.build_context(game)
            context["активный_источник_статистики"] = "SofaScore + web research"
            context["резервный_источник"] = "SofaScore LAST RESORT"
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
        return f"🏒 <b>{match.home} — {match.away}</b>\n🕒 {match.start_time}\n🏆 {match.league}"
