from __future__ import annotations

from typing import Any

from .khl import KHLService
from .providers.khl_mobile import KHLMobileClient
from .providers.khl_sofascore import KHLScoreClient


class EnhancedKHLService(KHLService):
    """KHL service with multiple independent statistical sources."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.khl_mobile = KHLMobileClient()
        self.sofascore = KHLScoreClient()

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
        )

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        home, away = self._teams(game)
        start = self._start_time(game)
        context: dict[str, Any] = {
            "источники": ["Официальный KHL mobile API", "SofaScore", "API-Sports"],
            "сезон": self._season(game),
        }

        official: dict[str, Any] | None = None
        try:
            official = await self.khl_mobile.build_match_context(home, away, start)
            context["официальные_данные_khl"] = official
        except Exception as exc:
            context["ошибка_официального_khl"] = f"{type(exc).__name__}: {exc}"

        if not official or not self._usable(official):
            try:
                context["резервные_данные_khl"] = await self.sofascore.build_context(home, away, start)
            except Exception as exc:
                context["ошибка_sofascore"] = f"{type(exc).__name__}: {exc}"

        try:
            fallback = await super().analysis_for_game(game)
            context["резервные_данные_api_sports"] = fallback
        except Exception as exc:
            context["ошибка_резервных_данных"] = f"{type(exc).__name__}: {exc}"

        # Pick the source that actually contains the most usable recent-game data.
        candidates: list[tuple[str, dict[str, Any]]] = []
        for key in ("официальные_данные_khl", "резервные_данные_khl"):
            data = context.get(key)
            if isinstance(data, dict):
                candidates.append((str(data.get("источник_статистики", key)), data))

        best_source = None
        best_data = None
        best_score = -1
        for source, data in candidates:
            score = self._source_score(data)
            if score > best_score:
                best_source, best_data, best_score = source, data, score

        if best_data is not None and best_score > 0:
            context["активный_источник_статистики"] = best_source
            context["качество_активных_данных"] = self._quality(best_data)
        else:
            # API-Sports can still contain real recent games even when the
            # specialized providers fail. Mark it as usable instead of telling
            # the model that all sports data is missing.
            api = context.get("резервные_данные_api_sports") or {}
            home_api = api.get("хозяева") or {}
            away_api = api.get("гости") or {}
            h = len(home_api.get("последние_матчи_api_sports") or [])
            a = len(away_api.get("последние_матчи_api_sports") or [])
            if h or a:
                context["активный_источник_статистики"] = "API-Sports"
                context["качество_активных_данных"] = {
                    "история_хозяев": h,
                    "история_гостей": a,
                    "h2h": len(api.get("очные_встречи_khl") or []),
                    "есть_таблица": bool(api.get("турнирная_таблица_khl")),
                    "есть_сезонная_статистика": bool(
                        home_api.get("сезонная_статистика_api_sports")
                        or away_api.get("сезонная_статистика_api_sports")
                    ),
                }
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
