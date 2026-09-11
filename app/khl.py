from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import Market, Match, Sport
from .providers.api_sports import ApiSportsClient, ApiSportsError


class KHLService:
    """KHL data, market normalization and a transparent baseline model.

    The baseline model is market-derived: it removes the bookmaker margin and
    adds a conservative signal score from probability, Value and market type.
    It is intentionally not presented as an ML model until we have enough
    historical samples for backtesting and calibration.
    """

    def __init__(self, client: ApiSportsClient) -> None:
        self.client = client

    async def today_games(self) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).date().isoformat()
        games = await self.client.hockey_games(date=today)
        return [
            g
            for g in games
            if str(g.get("league", {}).get("name", "")).strip().lower() == "khl"
        ]

    @staticmethod
    def _teams(game: dict[str, Any]) -> tuple[str, str]:
        teams = game.get("teams") or {}
        home = (teams.get("home") or {}).get("name") or "Хозяева"
        away = (teams.get("away") or {}).get("name") or "Гости"
        return str(home), str(away)

    @staticmethod
    def _start_time(game: dict[str, Any]) -> str:
        return str(game.get("date") or game.get("datetime") or "")

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
                if not name or odd is None or odd <= 1.0:
                    continue
                handicap = item.get("handicap") or item.get("line")
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
                markets.append(
                    Market(
                        name=f"{market_name}: {name}",
                        odds=odd,
                        probability=probability,
                    )
                )

        # Keep the best available price for duplicate selections.
        best: dict[str, Market] = {}
        for market in markets:
            previous = best.get(market.name)
            if previous is None or market.odds > previous.odds:
                best[market.name] = market

        return tuple(
            sorted(best.values(), key=lambda m: m.value_percent, reverse=True)[:30]
        )

    @staticmethod
    def signal_score(market: Market) -> float:
        """Transparent 0-10 score for prioritizing lines, not a guarantee."""
        probability = market.probability * 100
        value = market.value_percent
        score = 0.0
        score += min(max((probability - 50.0) / 4.0, 0.0), 5.0)
        score += min(max(value / 3.0, 0.0), 4.0)
        if 1.55 <= market.odds <= 2.30:
            score += 1.0
        return round(min(score, 10.0), 1)

    @staticmethod
    def confidence_label(score: float) -> str:
        if score >= 8.0:
            return "🔥 Высокий"
        if score >= 6.0:
            return "🟢 Хороший"
        if score >= 4.0:
            return "🟡 Средний"
        return "⚪ Осторожно"

    @staticmethod
    def _odd(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_bookmaker_bets(
        payload: dict[str, Any],
    ) -> list[tuple[str, list[dict[str, Any]]]]:
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

    @classmethod
    def format_markets(cls, match: Match) -> str:
        if not match.markets:
            return "\n\nЛиния пока недоступна."

        ranked = sorted(
            match.markets,
            key=lambda market: cls.signal_score(market),
            reverse=True,
        )
        lines = ["\n📈 <b>Лучшие линии</b>"]
        for index, market in enumerate(ranked[:15], 1):
            value_sign = "+" if market.value_percent >= 0 else ""
            score = cls.signal_score(market)
            label = cls.confidence_label(score)
            lines.append(
                f"{index}. <b>{market.name}</b>\n"
                f"   КФ {market.odds:.2f} • вероятность {market.probability * 100:.1f}%\n"
                f"   Fair {market.fair_odds:.2f} • Value {value_sign}{market.value_percent:.1f}%\n"
                f"   Сигнал {score:.1f}/10 • {label}"
            )

        lines.append(
            "\n<i>Вероятность — нормализованная рыночная оценка по доступным котировкам. "
            "Это базовый рейтинг, а не обученная ML-модель и не гарантия исхода.</i>"
        )
        return "\n".join(lines)
