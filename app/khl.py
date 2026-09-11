from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import Market, Match, Sport
from .providers.api_sports import ApiSportsClient, ApiSportsError


class KHLService:
    """KHL data and market adapter.

    The first version deliberately uses bookmaker prices as the baseline
    probability source. We normalize implied probabilities to remove the
    bookmaker margin. Later the statistical model can replace/adjust this
    baseline without changing the Telegram layer.
    """

    def __init__(self, client: ApiSportsClient) -> None:
        self.client = client

    async def today_games(self) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).date().isoformat()
        games = await self.client.hockey_games(date=today)
        return [g for g in games if str(g.get("league", {}).get("name", "")).strip().lower() == "khl"]

    @staticmethod
    def _game_id(game: dict[str, Any]) -> int | None:
        value = game.get("id")
        return int(value) if value is not None else None

    @staticmethod
    def _teams(game: dict[str, Any]) -> tuple[str, str]:
        teams = game.get("teams") or {}
        home = (teams.get("home") or {}).get("name") or "Хозяева"
        away = (teams.get("away") or {}).get("name") or "Гости"
        return str(home), str(away)

    @staticmethod
    def _start_time(game: dict[str, Any]) -> str:
        date = game.get("date") or game.get("datetime") or ""
        return str(date)

    def to_match(self, game: dict[str, Any], markets: tuple[Market, ...] = ()) -> Match:
        home, away = self._teams(game)
        return Match(
            sport=Sport.KHL,
            league=str((game.get("league") or {}).get("name") or "KHL"),
            home=home,
            away=away,
            start_time=self._start_time(game),
            markets=markets,
        )

    async def markets_for_game(self, game_id: int) -> tuple[Market, ...]:
        payload = await self.client.hockey_odds(game=game_id)
        raw = self._extract_bookmaker_bets(payload)
        markets: list[Market] = []

        for market_name, values in raw:
            parsed: list[tuple[str, float]] = []
            for item in values:
                name = str(item.get("value") or item.get("name") or "").strip()
                odd = self._odd(item.get("odd") or item.get("price") or item.get("odds"))
                if name and odd and odd > 1.0:
                    handicap = item.get("handicap")
                    if handicap not in (None, ""):
                        name = f"{name} {handicap}"
                    parsed.append((name, odd))

            if len(parsed) < 2:
                continue

            denominator = sum(1.0 / odd for _, odd in parsed)
            if denominator <= 0:
                continue

            for name, odd in parsed:
                probability = (1.0 / odd) / denominator
                markets.append(Market(name=f"{market_name}: {name}", odds=odd, probability=probability))

        # Keep the best price for duplicate market selections and limit the UI.
        best: dict[str, Market] = {}
        for market in markets:
            old = best.get(market.name)
            if old is None or market.odds > old.odds:
                best[market.name] = market
        return tuple(sorted(best.values(), key=lambda m: m.value_percent, reverse=True)[:30])

    @staticmethod
    def _odd(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_bookmaker_bets(payload: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
        result = payload.get("response") or []
        if isinstance(result, dict):
            result = [result]

        collected: list[tuple[str, list[dict[str, Any]]]] = []
        for game in result:
            bookmakers = game.get("bookmakers") or []
            for bookmaker in bookmakers:
                for bet in bookmaker.get("bets") or bookmaker.get("markets") or []:
                    name = str(bet.get("name") or bet.get("key") or "Рынок")
                    values = bet.get("values") or bet.get("outcomes") or []
                    if isinstance(values, list):
                        collected.append((name, values))
        return collected

    @staticmethod
    def format_game(match: Match) -> str:
        return (
            f"🏒 <b>{match.home} — {match.away}</b>\n"
            f"🕒 {match.start_time}\n"
            f"🏆 {match.league}"
        )

    @staticmethod
    def format_markets(match: Match) -> str:
        if not match.markets:
            return "\n\nЛиния пока недоступна."

        lines = ["\n📈 <b>Линии</b>"]
        for index, market in enumerate(match.markets[:12], 1):
            value_sign = "+" if market.value_percent >= 0 else ""
            lines.append(
                f"{index}. {market.name}\n"
                f"   КФ {market.odds:.2f} • вероятность {market.probability * 100:.1f}%\n"
                f"   Fair {market.fair_odds:.2f} • Value {value_sign}{market.value_percent:.1f}%"
            )
        lines.append("\n<i>Вероятность здесь — нормализованная рыночная оценка по доступным котировкам, а не результат обученной модели.</i>")
        return "\n".join(lines)
