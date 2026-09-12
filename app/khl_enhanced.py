from __future__ import annotations

from typing import Any

from .khl import KHLService
from .providers.api_sport_ru import ApiSportRuClient
from .providers.khl_mobile import KHLMobileClient
from .providers.khl_sofascore import KHLScoreClient


class EnhancedKHLService(KHLService):
    """KHL service with multiple independent statistical sources."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.khl_mobile = KHLMobileClient()
        self.sofascore = KHLScoreClient()
        self.api_sport_ru = ApiSportRuClient(__import__("app.config", fromlist=["settings"]).settings.api_sport_ru_key) if __import__("app.config", fromlist=["settings"]).settings.api_sport_ru_key else None

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

    @classmethod
    def _usable(cls, data: dict[str, Any]) -> bool:
        q = cls._quality(data)
        return q["история_хозяев"] >= 3 and q["история_гостей"] >= 3

    @classmethod
    def _source_score(cls, data: dict[str, Any]) -> int:
        q = cls._quality(data)
        return (
            q["история_хозяев"]
            + q["история_гостей"]
            + min(q["h2h"], 10)
            + (5 if q["есть_таблица"] else 0)
            + (2 if q["есть_сезонная_статистика"] else 0)
        )

    @staticmethod
    def _api_quality(api: dict[str, Any]) -> dict[str, int | bool]:
        home_api = api.get("хозяева") or {}
        away_api = api.get("гости") or {}
        h = len(home_api.get("последние_матчи_api_sports") or [])
        a = len(away_api.get("последние_матчи_api_sports") or [])
        return {
            "история_хозяев": h,
            "история_гостей": a,
            "h2h": len(api.get("очные_встречи_khl") or []),
            "есть_таблица": bool(api.get("турнирная_таблица_khl")),
            "есть_сезонная_статистика": bool(
                home_api.get("сезонная_статистика_api_sports")
                or away_api.get("сезонная_статистика_api_sports")
            ),
        }

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        home, away = self._teams(game)
        start = self._start_time(game)
        context: dict[str, Any] = {
            "источники": [
                "API-SPORT.ru",
                "Официальный KHL mobile API",
                "SofaScore",
                "API-Sports",
                "KHL HockeyTech",
            ],
            "сезон": self._season(game),
        }

        # API-SPORT.ru: current match + pregame form/H2H/streaks + match stats/odds.
        # It is optional; if unavailable, all existing sources continue to work.
        if self.api_sport_ru is not None:
            try:
                context["данные_api_sport_ru"] = await self.api_sport_ru.build_context(home, away, start)
            except Exception as exc:
                context["ошибка_api_sport_ru"] = f"{type(exc).__name__}: {exc}"

        # Collect every existing source independently. One provider returning an empty
        # result must not hide useful data from another provider.
        try:
            official = await self.khl_mobile.build_match_context(home, away, start)
            context["официальные_данные_khl"] = official
        except Exception as exc:
            context["ошибка_официального_khl"] = f"{type(exc).__name__}: {exc}"

        try:
            context["резервные_данные_khl"] = await self.sofascore.build_context(home, away, start)
        except Exception as exc:
            context["ошибка_sofascore"] = f"{type(exc).__name__}: {exc}"

        try:
            context["резервные_данные_api_sports"] = await super().analysis_for_game(game)
        except Exception as exc:
            context["ошибка_резервных_данных"] = f"{type(exc).__name__}: {exc}"

        # HockeyTech is kept as an additional independent fallback. Its
        # payload is not used as the sole source unless it contains real games.
        try:
            ht = self.khl_data
            recent_home = await ht.recent_team_games(home, days=120, limit=10)
            recent_away = await ht.recent_team_games(away, days=120, limit=10)
            h2h = await ht.head_to_head(home, away, days=730, limit=10)
            context["резервные_данные_hockeytech"] = {
                "источник_статистики": "KHL HockeyTech",
                "хозяева": {"команда": home, "последние_10": recent_home},
                "гости": {"команда": away, "последние_10": recent_away},
                "очные_встречи": h2h,
                "качество_данных": {
                    "история_хозяев": len(recent_home),
                    "история_гостей": len(recent_away),
                    "h2h": len(h2h),
                    "есть_таблица": False,
                    "есть_сезонная_статистика": False,
                },
            }
        except Exception as exc:
            context["ошибка_hockeytech"] = f"{type(exc).__name__}: {exc}"

        candidates: list[tuple[str, dict[str, Any], int]] = []

        api_sport_ru = context.get("данные_api_sport_ru")
        if isinstance(api_sport_ru, dict) and api_sport_ru.get("матч_найден"):
            q = self._quality(api_sport_ru)
            # Give API-SPORT.ru a meaningful score when it supplies pregame
            # form/H2H, while still allowing a richer existing source to win.
            score = (
                int(q["история_хозяев"])
                + int(q["история_гостей"])
                + min(int(q["h2h"]), 10)
                + (2 if q["есть_сезонная_статистика"] else 0)
            )
            candidates.append(("API-SPORT.ru", api_sport_ru, score))

        for key in ("официальные_данные_khl", "резервные_данные_khl"):
            data = context.get(key)
            if isinstance(data, dict):
                score = self._source_score(data)
                if score > 0:
                    candidates.append((str(data.get("источник_статистики", key)), data, score))

        api = context.get("резервные_данные_api_sports")
        if isinstance(api, dict):
            q = self._api_quality(api)
            api_score = int(q["история_хозяев"]) + int(q["история_гостей"]) + min(int(q["h2h"]), 10) + (5 if q["есть_таблица"] else 0) + (2 if q["есть_сезонная_статистика"] else 0)
            if api_score > 0:
                candidates.append(("API-Sports", api, api_score))

        ht = context.get("резервные_данные_hockeytech")
        if isinstance(ht, dict):
            score = self._source_score(ht)
            if score > 0:
                candidates.append(("KHL HockeyTech", ht, score))

        if candidates:
            best_source, best_data, best_score = max(candidates, key=lambda item: item[2])
            context["активный_источник_статистики"] = best_source
            context["качество_активных_данных"] = self._quality(best_data)
        else:
            context["активный_источник_статистики"] = "нет"
            context["качество_активных_данных"] = {
                "история_хозяев": 0,
                "история_гостей": 0,
                "h2h": 0,
                "есть_таблица": False,
                "есть_сезонная_статистика": False,
            }

        q = context["качество_активных_данных"]
        context["диагностика_статистики"] = (
            f"Источник: {context['активный_источник_статистики']}; "
            f"последние матчи: хозяева {q.get('история_хозяев', 0)}, "
            f"гости {q.get('история_гостей', 0)}; "
            f"H2H: {q.get('h2h', 0)}; "
            f"таблица: {'да' if q.get('есть_таблица') else 'нет'}; "
            f"сезонная статистика: {'да' if q.get('есть_сезонная_статистика') else 'нет'}."
        )
        return context
