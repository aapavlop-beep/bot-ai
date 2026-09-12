from __future__ import annotations

from typing import Any

from .khl import KHLService
from .providers.khl_mobile import KHLMobileClient
from .providers.khl_sofascore import KHLScoreClient


class EnhancedKHLService(KHLService):
    """KHL service with official KHL API plus independent SofaScore fallback."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.khl_mobile = KHLMobileClient()
        self.sofascore = KHLScoreClient()

    @staticmethod
    def _usable(data: dict[str, Any]) -> bool:
        quality = data.get("качество_данных") or {}
        return int(quality.get("история_хозяев") or 0) >= 3 and int(quality.get("история_гостей") or 0) >= 3

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        home, away = self._teams(game)
        start = self._start_time(game)
        context: dict[str, Any] = {"источники": ["Официальный KHL mobile API", "SofaScore", "API-Sports"], "сезон": self._season(game)}

        official: dict[str, Any] | None = None
        try:
            official = await self.khl_mobile.build_match_context(home, away, start)
            context["официальные_данные_khl"] = official
        except Exception as exc:
            context["ошибка_официального_khl"] = f"{type(exc).__name__}: {exc}"

        # If the official endpoint answers but gives empty history, do not pass
        # that empty response to the model as if it were useful statistics.
        if not official or not self._usable(official):
            try:
                context["резервные_данные_khl"] = await self.sofascore.build_context(home, away, start)
            except Exception as exc:
                context["ошибка_sofascore"] = f"{type(exc).__name__}: {exc}"

        try:
            fallback = await super().analysis_for_game(game)
            context["резервные_данные_api_sports"] = fallback
        except Exception as exc:
            context["ошибка_резервных_данных"] = type(exc).__name__

        # Prefer a real independent source in the quality score whenever it has
        # actual recent games. This prevents the AI from claiming that data is
        # missing while a fallback provider has supplied it.
        for key in ("официальные_данные_khl", "резервные_данные_khl"):
            data = context.get(key)
            if isinstance(data, dict) and self._usable(data):
                context["активный_источник_статистики"] = data.get("источник_статистики", key)
                context["качество_активных_данных"] = data.get("качество_данных", {})
                break

        return context
