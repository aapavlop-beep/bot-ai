from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import re

from .models import Market, Match, Sport
from .providers.api_sports import ApiSportsClient


class KHLService:
    """Данные КХЛ, русификация рынков и контекст для ИИ."""

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

    @staticmethod
    def _team_id(game: dict[str, Any], side: str) -> int | None:
        try:
            value = ((game.get("teams") or {}).get(side) or {}).get("id")
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _league_id(game: dict[str, Any]) -> int | None:
        try:
            value = (game.get("league") or {}).get("id")
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _season(game: dict[str, Any]) -> str | None:
        value = (game.get("league") or {}).get("season")
        return str(value) if value not in (None, "") else None

    def to_match(
        self,
        game: dict[str, Any],
        markets: tuple[Market, ...] = (),
        analysis_data: dict[str, Any] | None = None,
    ) -> Match:
        home, away = self._teams(game)
        return Match(
            sport=Sport.KHL,
            league=str((game.get("league") or {}).get("name") or "КХЛ"),
            home=home,
            away=away,
            start_time=self._start_time(game),
            markets=markets,
            analysis_data=analysis_data or {},
        )

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Собирает сезонную статистику и последние завершённые матчи обеих команд.

        Если отдельный endpoint недоступен, не ломает прогноз: возвращает только
        те данные, которые удалось получить.
        """
        league_id = self._league_id(game)
        season = self._season(game)
        context: dict[str, Any] = {"источник": "API-Sports", "сезон": season}

        for side, label in (("home", "хозяева"), ("away", "гости")):
            team_id = self._team_id(game, side)
            if team_id is None:
                continue

            team_context: dict[str, Any] = {}
            try:
                stats = await self.client.hockey_statistics(team_id, league=league_id, season=season)
                if stats:
                    team_context["сезонная_статистика"] = self._compact_team_stats(stats[0])
            except Exception as exc:
                team_context["ошибка_статистики"] = type(exc).__name__

            try:
                history = await self.client.hockey_games(team=team_id, league=league_id, season=season)
                completed = [g for g in history if self._is_completed(g)]
                completed.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
                team_context["последние_матчи"] = [self._compact_game(g, team_id) for g in completed[:10]]
                team_context["форма_последних_10"] = self._form_summary(completed[:10], team_id)
            except Exception as exc:
                team_context["ошибка_истории"] = type(exc).__name__

            context[label] = team_context

        return context

    @staticmethod
    def _is_completed(game: dict[str, Any]) -> bool:
        status = str((game.get("status") or {}).get("short") or (game.get("status") or {}).get("long") or "").lower()
        if any(word in status for word in ("not started", "scheduled", "postponed", "cancelled", "canceled")):
            return False
        score = game.get("scores") or game.get("score") or {}
        return bool(score)

    @staticmethod
    def _score_value(game: dict[str, Any], side: str) -> int | None:
        scores = game.get("scores") or game.get("score") or {}
        try:
            value = (scores.get(side) or {}).get("goals")
            if value is None:
                value = (scores.get(side) or {}).get("score")
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _compact_game(cls, game: dict[str, Any], team_id: int) -> dict[str, Any]:
        teams = game.get("teams") or {}
        home = teams.get("home") or {}
        away = teams.get("away") or {}
        home_id = home.get("id")
        side = "home" if str(home_id) == str(team_id) else "away"
        hs = cls._score_value(game, "home")
        aws = cls._score_value(game, "away")
        result = "неизвестно"
        if hs is not None and aws is not None:
            team_score = hs if side == "home" else aws
            opp_score = aws if side == "home" else hs
            result = "победа" if team_score > opp_score else "поражение" if team_score < opp_score else "ничья"
        return {
            "дата": str(game.get("date") or "")[:10],
            "соперник": str((away if side == "home" else home).get("name") or ""),
            "счёт": f"{hs}:{aws}" if hs is not None and aws is not None else "нет",
            "результат": result,
        }

    @classmethod
    def _form_summary(cls, games: list[dict[str, Any]], team_id: int) -> dict[str, Any]:
        wins = losses = draws = scored = conceded = 0
        for game in games:
            teams = game.get("teams") or {}
            side = "home" if str((teams.get("home") or {}).get("id")) == str(team_id) else "away"
            hs = cls._score_value(game, "home")
            aws = cls._score_value(game, "away")
            if hs is None or aws is None:
                continue
            scored += hs if side == "home" else aws
            conceded += aws if side == "home" else hs
            team_score = hs if side == "home" else aws
            opp_score = aws if side == "home" else hs
            if team_score > opp_score:
                wins += 1
            elif team_score < opp_score:
                losses += 1
            else:
                draws += 1
        count = wins + losses + draws
        return {
            "матчей": count,
            "побед": wins,
            "поражений": losses,
            "ничьих": draws,
            "забито": scored,
            "пропущено": conceded,
            "среднее_забито": round(scored / count, 2) if count else None,
            "среднее_пропущено": round(conceded / count, 2) if count else None,
        }

    @staticmethod
    def _compact_team_stats(raw: Any) -> dict[str, Any]:
        """Оставляет только числовые/структурные показатели, полезные ИИ."""
        if not isinstance(raw, dict):
            return {}
        response = raw.get("response") if isinstance(raw.get("response"), dict) else raw
        if not isinstance(response, dict):
            return {}
        result: dict[str, Any] = {}
        for key, value in response.items():
            if key in {"team", "league"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[str(key)] = value
            elif isinstance(value, dict):
                compact = {str(k): v for k, v in value.items() if isinstance(v, (str, int, float, bool)) or v is None}
                if compact:
                    result[str(key)] = compact
        return result

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
