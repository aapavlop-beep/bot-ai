from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import Market, Match, Sport
from .providers.api_sports import ApiSportsClient


class KHLService:
    """Данные КХЛ, русификация рынков и базовая модель."""

    def __init__(self, client: ApiSportsClient) -> None:
        self.client = client

    async def today_games(self) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc).date().isoformat()
        games = await self.client.hockey_games(date=today)
        return [g for g in games if str(g.get("league", {}).get("name", "")).strip().lower() == "khl"]

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
            league=str((game.get("league") or {}).get("name") or "КХЛ"),
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
                if not isinstance(item, dict):
                    continue
                name = self.translate_outcome(str(item.get("value") or item.get("name") or "").strip())
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
                markets.append(Market(name=f"{self.translate_market(market_name)}: {name}", odds=odd, probability=probability))

        best: dict[str, Market] = {}
        for market in markets:
            previous = best.get(market.name)
            if previous is None or market.odds > previous.odds:
                best[market.name] = market
        return tuple(sorted(best.values(), key=lambda m: m.value_percent, reverse=True)[:30])

    @staticmethod
    def signal_score(market: Market) -> float:
        probability = market.probability * 100
        value = market.value_percent
        score = min(max((probability - 50.0) / 4.0, 0.0), 5.0)
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
    def _extract_bookmaker_bets(payload: Any) -> list[tuple[str, list[dict[str, Any]]]]:
        """Нормализует ответы API-Sports, если response приходит как dict или list."""
        if isinstance(payload, dict):
            result = payload.get("response") or []
        elif isinstance(payload, list):
            result = payload
        else:
            return []
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            return []

        collected: list[tuple[str, list[dict[str, Any]]]] = []
        for game in result:
            if not isinstance(game, dict):
                continue
            bookmakers = game.get("bookmakers") or []
            if not isinstance(bookmakers, list):
                continue
            for bookmaker in bookmakers:
                if not isinstance(bookmaker, dict):
                    continue
                bets = bookmaker.get("bets") or bookmaker.get("markets") or []
                if not isinstance(bets, list):
                    continue
                for bet in bets:
                    if not isinstance(bet, dict):
                        continue
                    name = str(bet.get("name") or bet.get("key") or "Рынок")
                    values = bet.get("values") or bet.get("outcomes") or []
                    if isinstance(values, list):
                        collected.append((name, values))
        return collected

    @staticmethod
    def translate_market(name: str) -> str:
        text = name.strip()
        lower = text.lower()
        replacements = {
            "moneyline": "Победитель матча",
            "match winner": "Победитель матча",
            "winner": "Победитель матча",
            "home/away": "Исход",
            "home away": "Исход",
            "double chance": "Двойной исход",
            "handicap": "Фора",
            "puck line": "Фора по шайбам",
            "total": "Тотал",
            "goals over/under": "Тотал шайб",
            "goals over under": "Тотал шайб",
            "period": "Период",
            "1st period": "1-й период",
            "2nd period": "2-й период",
            "3rd period": "3-й период",
            "odd/even": "Чёт / нечёт",
            "odd even": "Чёт / нечёт",
            "both teams to score": "Обе команды забьют",
        }
        for source, target in replacements.items():
            if lower == source or source in lower:
                text = text.replace(source, target).replace(source.title(), target)
                break
        return text

    @staticmethod
    def translate_outcome(name: str) -> str:
        text = name.strip()
        mapping = {
            "home": "Хозяева",
            "away": "Гости",
            "draw": "Ничья",
            "over": "Больше",
            "under": "Меньше",
            "yes": "Да",
            "no": "Нет",
            "odd": "Нечёт",
            "even": "Чёт",
            "1": "Хозяева",
            "2": "Гости",
            "x": "Ничья",
        }
        return mapping.get(text.lower(), text)

    @staticmethod
    def format_game(match: Match) -> str:
        return f"🏒 <b>{match.home} — {match.away}</b>\n🕒 {match.start_time}\n🏆 {match.league}"

    @classmethod
    def format_markets(cls, match: Match) -> str:
        if not match.markets:
            return "\n\n📈 <b>Линия пока недоступна.</b>"
        ranked = sorted(match.markets, key=cls.signal_score, reverse=True)
        lines = ["\n📈 <b>Лучшие линии</b>"]
        for index, market in enumerate(ranked[:15], 1):
            value_sign = "+" if market.value_percent >= 0 else ""
            score = cls.signal_score(market)
            label = cls.confidence_label(score)
            lines.append(
                f"{index}. <b>{market.name}</b>\n"
                f"   Коэффициент: {market.odds:.2f}\n"
                f"   Вероятность: {market.probability * 100:.1f}%\n"
                f"   Справедливый КФ: {market.fair_odds:.2f}\n"
                f"   Преимущество: {value_sign}{market.value_percent:.1f}%\n"
                f"   Сигнал: {score:.1f}/10 • {label}"
            )
        lines.append("\n<i>Вероятность — нормализованная рыночная оценка. Это базовый рейтинг, а не гарантия исхода.</i>")
        return "\n".join(lines)
