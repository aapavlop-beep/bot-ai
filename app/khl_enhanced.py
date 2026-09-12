from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .providers.api_sport_ru import ApiSportRuClient


class EnhancedKHLService:
    """KHL service backed exclusively by API-SPORT.ru.

    This class deliberately does not inherit from the legacy KHL service so
    the HockeyTech client cannot be instantiated or queried from the active
    KHL code path.
    """

    def __init__(self, client: ApiSportRuClient) -> None:
        self.client = client
        self.api_sport_ru = client

    @staticmethod
    def _quality(data: dict[str, Any]) -> dict[str, int | bool]:
        q = data.get("качество_данных") or {}
        return {
            "история_хозяев": int(q.get("история_хозяев") or 0),
            "история_гостей": int(q.get("история_гостей") or 0),
            "h2h": int(q.get("h2h") or 0),
            "есть_таблица": bool(q.get("есть_таблица")),
            "есть_сезонная_статистика": bool(q.get("есть_сезонная_статистика")),
        }

    async def _detail(self, game: dict[str, Any]) -> dict[str, Any]:
        raw = game.get("__raw_api_sport_ru")
        match_id = game.get("id")
        if isinstance(raw, dict):
            match_id = raw.get("id") or match_id
        if match_id is None:
            return raw if isinstance(raw, dict) else game
        try:
            return await self.api_sport_ru.match_by_id(match_id)
        except Exception:
            return raw if isinstance(raw, dict) else game

    async def markets_for_game(self, game_id: int) -> tuple[Market, ...]:
        detail = await self.api_sport_ru.match_by_id(game_id)
        return self.api_sport_ru.markets_from_match(detail)

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Collect the complete pre-game context only from API-SPORT.ru."""
        detail = await self._detail(game)
        home, away = self.api_sport_ru._teams(detail)
        tournament = detail.get("tournament") or detail.get("league") or {}
        season = detail.get("season") or {}
        pregame = detail.get("pregame") or {}
        quality = self.api_sport_ru._quality(pregame)

        context: dict[str, Any] = {
            "источники": ["API-SPORT.ru"],
            "главный_источник_статистики": "API-SPORT.ru",
            "активный_источник_статистики": "API-SPORT.ru",
            "режим_источника": "API-SPORT.ru (единственный источник)",
            "матч_найден": bool(detail.get("id") or game.get("id")),
            "match_id": detail.get("id") or game.get("id"),
            "турнир": self.api_sport_ru._compact(tournament),
            "сезон": self.api_sport_ru._compact(season),
            "команды": {"хозяева": home, "гости": away},
            "статус": self.api_sport_ru._compact(detail.get("status")),
            "счёт": self.api_sport_ru._compact(detail.get("homeScore")),
            "счёт_гостей": self.api_sport_ru._compact(detail.get("awayScore")),
            "форма_и_серии": self.api_sport_ru._compact(pregame),
            "статистика_матча": self.api_sport_ru._compact(detail.get("matchStatistics")),
            "события": self.api_sport_ru._compact(detail.get("liveEvents")),
            "коэффициенты": self.api_sport_ru._compact(detail.get("oddsBase")),
            "букмекерские_коэффициенты": self.api_sport_ru._compact(detail.get("oddsBk")),
            "качество_данных": quality,
        }

        if isinstance(pregame, dict):
            form = pregame.get("form") or pregame.get("teamForm") or {}
            h2h = pregame.get("h2h") or pregame.get("headToHead") or []
            home_form = (form.get("home") or form.get("homeTeam") or form.get("host")) if isinstance(form, dict) else {}
            away_form = (form.get("away") or form.get("awayTeam") or form.get("guest")) if isinstance(form, dict) else {}
            context["форма_хозяев"] = self.api_sport_ru._compact(home_form)
            context["форма_гостей"] = self.api_sport_ru._compact(away_form)
            context["очные_встречи_api_sport_ru"] = self.api_sport_ru._compact(h2h)

        context["статистика_для_ии"] = {
            "турнир": context["турнир"],
            "сезон": context["сезон"],
            "команды": context["команды"],
            "статус": context["статус"],
            "счёт": context["счёт"],
            "счёт_гостей": context["счёт_гостей"],
            "форма_и_серии": context["форма_и_серии"],
            "форма_хозяев": context.get("форма_хозяев", {}),
            "форма_гостей": context.get("форма_гостей", {}),
            "очные_встречи_api_sport_ru": context.get("очные_встречи_api_sport_ru", []),
            "статистика_матча": context["статистика_матча"],
            "события": context["события"],
            "коэффициенты": context["коэффициенты"],
            "букмекерские_коэффициенты": context["букмекерские_коэффициенты"],
            "качество_данных": quality,
        }
        context["качество_активных_данных"] = quality
        context["диагностика_статистики"] = (
            f"Источник: API-SPORT.ru; последние матчи: хозяева {quality['история_хозяев']}, "
            f"гости {quality['история_гостей']}; H2H: {quality['h2h']}; "
            f"таблица: {'да' if quality['есть_таблица'] else 'нет'}; "
            f"сезонная статистика: {'да' if quality['есть_сезонная_статистика'] else 'нет'}."
        )
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
