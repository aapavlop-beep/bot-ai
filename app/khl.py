from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re

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
    def _replace_phrases(text: str, replacements: dict[str, str]) -> str:
        """Заменяет английские названия рынков без учета регистра."""
        result = text
        for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
            result = re.sub(re.escape(source), target, result, flags=re.IGNORECASE)
        return result

    @classmethod
    def translate_market(cls, name: str) -> str:
        text = name.strip()
        replacements = {
            "match ends in regular time": "Матч завершится в основное время",
            "match ends in over time": "Матч завершится в дополнительное время",
            "either team win by 3 goal": "Любая команда выиграет в 3 шайбы",
            "either team wins by 3 goals": "Любая команда выиграет в 3 шайбы",
            "away team will score a goal": "Гости забьют хотя бы одну шайбу",
            "home team will score a goal": "Хозяева забьют хотя бы одну шайбу",
            "team to score first": "Кто забьёт первым",
            "team to score last": "Кто забьёт последним",
            "both teams to score": "Обе команды забьют",
            "3way result": "Исход",
            "3 way result": "Исход",
            "moneyline": "Победитель матча",
            "match winner": "Победитель матча",
            "winner": "Победитель матча",
            "home/away": "Исход",
            "home away": "Исход",
            "double chance": "Двойной исход",
            "handicap": "Фора",
            "puck line": "Фора по шайбам",
            "goals over/under": "Тотал шайб",
            "goals over under": "Тотал шайб",
            "odd/even": "Чёт / нечёт",
            "odd even": "Чёт / нечёт",
            "1st period": "1-й период",
            "2nd period": "2-й период",
            "3rd period": "3-й период",
            "1st period result": "Исход 1-го периода",
            "2nd period result": "Исход 2-го периода",
            "3rd period result": "Исход 3-го периода",
            "period": "Период",
            "total": "Тотал",
            "home odd/even": "Хозяева — чёт / нечёт",
            "away odd/even": "Гости — чёт / нечёт",
            "home odd even": "Хозяева — чёт / нечёт",
            "away odd even": "Гости — чёт / нечёт",
        }
        return cls._replace_phrases(text, replacements)

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
