from __future__ import annotations

from typing import Any

from .khl import KHLService
from .providers.khl_mobile import KHLMobileClient


class EnhancedKHLService(KHLService):
    """KHL service with the official KHL mobile API as the primary stats source."""

    def __init__(self, client) -> None:
        super().__init__(client)
        self.khl_mobile = KHLMobileClient()

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        home, away = self._teams(game)
        start = self._start_time(game)
        context: dict[str, Any] = {
            "источники": ["Официальный KHL mobile API", "API-Sports"],
            "сезон": self._season(game),
        }

        # The official KHL service is primary. API-Sports remains a fallback for
        # fields the official feed does not expose.
        try:
            official = await self.khl_mobile.build_match_context(home, away, start)
            context["официальные_данные_khl"] = official
        except Exception as exc:
            context["ошибка_официального_khl"] = f"{type(exc).__name__}: {exc}"

        # Keep the existing provider as a secondary source, but do not let a
        # failure there erase the official data.
        try:
            fallback = await super().analysis_for_game(game)
            context["резервные_данные"] = fallback
        except Exception as exc:
            context["ошибка_резервных_данных"] = type(exc).__name__

        return context
