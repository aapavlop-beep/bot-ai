from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Market


class ApiSportRuError(RuntimeError):
    pass


class ApiSportRuClient:
    """API-SPORT.ru client with request caching and KHL-oriented data shaping.

    Important design rule: one logical KHL prediction must not fan out into a
    request per market, per statistic, per H2H record, etc. The /matches feed
    already contains the event, teams and usually odds. Historical form is
    loaded with one team_id request per unique team and cached.
    """

    BASE_URL = "https://api.api-sport.ru/v2"
    MATCH_CACHE_TTL = 300.0
    TEAM_CACHE_TTL = 1800.0
    DETAIL_CACHE_TTL = 300.0

    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self.request_count = 0

    @staticmethod
    def _cache_key(path: str, params: dict[str, Any]) -> str:
        parts = [path]
        for key in sorted(params):
            parts.append(f"{key}={params[key]}")
        return "?".join([parts[0], "&".join(parts[1:])]) if len(parts) > 1 else parts[0]

    async def get(self, path: str, cache_ttl: float = 0.0, **params: Any) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        safe_params = {k: v for k, v in params.items() if v is not None}
        key = self._cache_key(path, safe_params)
        now = time.monotonic()

        if cache_ttl > 0:
            cached = self._cache.get(key)
            if cached and now - cached[0] < cache_ttl:
                print(f"API-SPORT.ru CACHE HIT: GET {url} params={safe_params}", flush=True)
                return cached[1]

        async with self._lock:
            # Re-check after waiting for another coroutine that may have filled it.
            now = time.monotonic()
            if cache_ttl > 0:
                cached = self._cache.get(key)
                if cached and now - cached[0] < cache_ttl:
                    print(f"API-SPORT.ru CACHE HIT: GET {url} params={safe_params}", flush=True)
                    return cached[1]

            self.request_count += 1
            print(f"API-SPORT.ru REQUEST #{self.request_count}: GET {url} params={safe_params}", flush=True)
            headers = {"Authorization": self.api_key, "Accept": "application/json"}
            try:
                async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                    response = await client.get(url, headers=headers, params=safe_params)
                    print(
                        f"API-SPORT.ru RESPONSE: GET {url} status={response.status_code} bytes={len(response.content)}",
                        flush=True,
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception as exc:
                print(f"API-SPORT.ru ERROR: GET {url} {type(exc).__name__}: {exc}", flush=True)
                raise

            if not isinstance(payload, dict):
                raise ApiSportRuError("API-SPORT.ru returned a non-object response")
            if cache_ttl > 0:
                self._cache[key] = (time.monotonic(), payload)
            return payload

    @staticmethod
    def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("matches", "data", "items", "results", "response"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                for nested_key in ("matches", "data", "items", "results", "response"):
                    nested = value.get(nested_key)
                    if isinstance(nested, list):
                        return [item for item in nested if isinstance(item, dict)]
        return []

    @staticmethod
    def _one(payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("match", "data", "result", "response"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value[0]
        return payload

    @staticmethod
    def _name(team: Any) -> str:
        if not isinstance(team, dict):
            return str(team or "")
        translations = team.get("translations") or {}
        if isinstance(translations, dict) and translations.get("ru"):
            return str(translations["ru"])
        return str(team.get("name") or team.get("fullName") or team.get("shortName") or "")

    @staticmethod
    def _id(value: Any) -> int | None:
        if isinstance(value, dict):
            value = value.get("id")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _team_obj(cls, match: dict[str, Any], side: str) -> dict[str, Any]:
        teams = match.get("teams") or {}
        value = match.get("homeTeam" if side == "home" else "awayTeam")
        if not isinstance(value, dict):
            value = teams.get(side)
        return value if isinstance(value, dict) else {}

    @classmethod
    def _teams(cls, match: dict[str, Any]) -> tuple[str, str]:
        return cls._name(cls._team_obj(match, "home")), cls._name(cls._team_obj(match, "away"))

    @classmethod
    def _team_id(cls, match: dict[str, Any], side: str) -> int | None:
        return cls._id(cls._team_obj(match, side))

    @staticmethod
    def _match_date(match: dict[str, Any]) -> str:
        for key in ("dateEvent", "startTimestamp", "startTime", "date", "datetime"):
            value = match.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
                except (OverflowError, OSError, ValueError):
                    pass
            return str(value)
        return ""

    @staticmethod
    def _norm(value: str) -> str:
        value = value.lower().replace("ё", "е")
        replacements = {
            "мск": "москва", "moscow": "москва", "dinamo": "динамо",
            "dynamo": "динамо", "spartak": "спартак", "severstal": "северсталь",
            "barys": "барыс", "amur": "амур",
        }
        for src, dst in replacements.items():
            value = value.replace(src, dst)
        for char in "—–-.,:()[]{}'\"":
            value = value.replace(char, " ")
        return " ".join(value.split())

    @classmethod
    def _same_team(cls, left: str, right: str) -> bool:
        a, b = cls._norm(left), cls._norm(right)
        if not a or not b:
            return False
        if a == b or a in b or b in a:
            return True
        at, bt = set(a.split()), set(b.split())
        return len(at & bt) >= max(1, min(len(at), len(bt)) - 1)

    @classmethod
    def _find_match(cls, matches: list[dict[str, Any]], home: str, away: str) -> dict[str, Any] | None:
        for item in matches:
            mh, ma = cls._teams(item)
            if cls._same_team(mh, home) and cls._same_team(ma, away):
                return item
        return None

    @classmethod
    def _is_khl(cls, match: dict[str, Any]) -> bool:
        tournament = match.get("tournament") or match.get("league") or {}
        if isinstance(tournament, dict):
            values = [tournament.get("name"), tournament.get("shortName"), tournament.get("slug")]
        else:
            values = [tournament]
        text = cls._norm(" ".join(str(v or "") for v in values))
        return "кхл" in text or "khl" in text or "kontinental" in text

    @classmethod
    def normalize_match(cls, match: dict[str, Any]) -> dict[str, Any]:
        home_obj = cls._team_obj(match, "home")
        away_obj = cls._team_obj(match, "away")
        home, away = cls._teams(match)
        tournament = match.get("tournament") or match.get("league") or {}
        tournament_name = tournament.get("name") if isinstance(tournament, dict) else str(tournament or "КХЛ")
        return {
            "id": match.get("id"),
            "date": cls._match_date(match),
            "datetime": cls._match_date(match),
            "teams": {
                "home": {"id": cls._id(home_obj), "name": home},
                "away": {"id": cls._id(away_obj), "name": away},
            },
            "league": {
                "name": str(tournament_name or "КХЛ"),
                "id": tournament.get("id") if isinstance(tournament, dict) else None,
            },
            "__api_sport_ru": True,
            "__raw_api_sport_ru": match,
        }

    async def khl_matches(self, date: str | None = None) -> list[dict[str, Any]]:
        payload = await self.get("ice-hockey/matches", date=date, limit=100, cache_ttl=self.MATCH_CACHE_TTL)
        matches = self._items(payload)
        return [item for item in matches if self._is_khl(item)] or matches

    async def match_by_id(self, match_id: int | str) -> dict[str, Any]:
        payload = await self.get(f"ice-hockey/matches/{int(match_id)}", cache_ttl=self.DETAIL_CACHE_TTL)
        return self._one(payload)

    async def team_matches(
        self,
        team_id: int,
        tournament_id: int | None = None,
        season_id: int | None = None,
    ) -> list[dict[str, Any]]:
        payload = await self.get(
            "ice-hockey/matches",
            team_id=team_id,
            status="finished",
            tournament_id=tournament_id,
            season_id=season_id,
            limit=30,
            cache_ttl=self.TEAM_CACHE_TTL,
        )
        return self._items(payload)

    @staticmethod
    def markets_from_match(match: dict[str, Any]) -> tuple[Market, ...]:
        odds = match.get("oddsBase") or match.get("oddsBk") or []
        if isinstance(odds, dict):
            odds = odds.get("markets") or odds.get("items") or []
        if not isinstance(odds, list):
            return ()
        markets: list[Market] = []
        for market in odds:
            if not isinstance(market, dict):
                continue
            market_name = str(market.get("name") or market.get("group") or market.get("key") or "Рынок")
            values = market.get("choices") or market.get("outcomes") or market.get("values") or []
            if isinstance(values, dict):
                values = values.get("choices") or values.get("outcomes") or values.get("values") or []
            if not isinstance(values, list):
                continue
            parsed: list[tuple[str, float]] = []
            for choice in values:
                if not isinstance(choice, dict):
                    continue
                name = str(choice.get("name") or choice.get("value") or choice.get("label") or "").strip()
                raw_odd = choice.get("decimal") or choice.get("odds") or choice.get("price") or choice.get("odd")
                try:
                    odd = float(raw_odd)
                except (TypeError, ValueError):
                    continue
                if name and odd > 1:
                    parsed.append((name, odd))
            if len(parsed) < 2:
                continue
            denominator = sum(1.0 / odd for _, odd in parsed)
            if denominator <= 0:
                continue
            for name, odd in parsed:
                probability = (1.0 / odd) / denominator
                markets.append(Market(name=f"{market_name}: {name}", odds=odd, probability=probability))
        best: dict[str, Market] = {}
        for market in markets:
            previous = best.get(market.name)
            if previous is None or market.odds > previous.odds:
                best[market.name] = market
        return tuple(sorted(best.values(), key=lambda m: m.value_percent, reverse=True)[:50])

    @staticmethod
    def _compact(value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return None
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [ApiSportRuClient._compact(item, depth + 1) for item in value[:30]]
        if isinstance(value, dict):
            return {
                str(key): ApiSportRuClient._compact(item, depth + 1)
                for key, item in value.items()
            }
        return str(value)

    @classmethod
    def _score(cls, match: dict[str, Any], side: str) -> int | None:
        key = "homeScore" if side == "home" else "awayScore"
        value = match.get(key)
        if isinstance(value, dict):
            value = value.get("current") or value.get("display") or value.get("goals") or value.get("score")
        if value is None:
            scores = match.get("scores") or match.get("score") or {}
            side_value = scores.get(side) if isinstance(scores, dict) else None
            if isinstance(side_value, dict):
                value = side_value.get("current") or side_value.get("goals") or side_value.get("score")
            else:
                value = side_value
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _history_row(cls, game: dict[str, Any], team_id: int) -> dict[str, Any] | None:
        home, away = cls._teams(game)
        home_id = cls._team_id(game, "home")
        side = "home" if home_id == team_id else "away"
        hs, aws = cls._score(game, "home"), cls._score(game, "away")
        if hs is None or aws is None:
            return None
        team_score = hs if side == "home" else aws
        opp_score = aws if side == "home" else hs
        return {
            "дата": cls._match_date(game)[:10],
            "хозяева": home,
            "гости": away,
            "счёт": f"{hs}:{aws}",
            "результат": "победа" if team_score > opp_score else "поражение" if team_score < opp_score else "ничья",
            "забито": team_score,
            "пропущено": opp_score,
            "дом": side == "home",
        }

    @classmethod
    def _form(cls, games: list[dict[str, Any]], team_id: int, limit: int = 10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = []
        for game in sorted(games, key=cls._match_date, reverse=True):
            row = cls._history_row(game, team_id)
            if row:
                rows.append(row)
            if len(rows) >= limit:
                break
        wins = sum(row["результат"] == "победа" for row in rows)
        draws = sum(row["результат"] == "ничья" for row in rows)
        losses = len(rows) - wins - draws
        scored = sum(row["забито"] for row in rows)
        conceded = sum(row["пропущено"] for row in rows)
        count = len(rows)
        summary = {
            "матчей": count,
            "побед": wins,
            "ничьих": draws,
            "поражений": losses,
            "забито": scored,
            "пропущено": conceded,
            "среднее_забито": round(scored / count, 2) if count else None,
            "среднее_пропущено": round(conceded / count, 2) if count else None,
        }
        return rows, summary

    @classmethod
    def _h2h(cls, games: list[dict[str, Any]], home_id: int, away_id: int, limit: int = 10) -> list[dict[str, Any]]:
        result = []
        for game in sorted(games, key=cls._match_date, reverse=True):
            ids = {cls._team_id(game, "home"), cls._team_id(game, "away")}
            if home_id not in ids or away_id not in ids:
                continue
            hs, aws = cls._score(game, "home"), cls._score(game, "away")
            if hs is None or aws is None:
                continue
            result.append({
                "дата": cls._match_date(game)[:10],
                "хозяева": cls._teams(game)[0],
                "гости": cls._teams(game)[1],
                "счёт": f"{hs}:{aws}",
            })
            if len(result) >= limit:
                break
        return result

    async def build_context(self, game: dict[str, Any]) -> dict[str, Any]:
        """Build useful pre-match context with at most two history requests.

        The daily /matches response is reused as the primary event payload. If
        it already contains pregame data we keep it; otherwise two cached
        team-history requests provide recent form and H2H locally.
        """
        raw = game.get("__raw_api_sport_ru") if isinstance(game, dict) else None
        raw = raw if isinstance(raw, dict) else game
        home, away = self._teams(raw)
        home_id, away_id = self._team_id(raw, "home"), self._team_id(raw, "away")
        tournament = raw.get("tournament") or raw.get("league") or {}
        tournament_id = self._id(tournament)
        season = raw.get("season") or {}
        season_id = self._id(season)
        pregame = raw.get("pregame") or {}

        context: dict[str, Any] = {
            "источники": ["API-SPORT.ru"],
            "главный_источник_статистики": "API-SPORT.ru",
            "активный_источник_статистики": "API-SPORT.ru",
            "режим_источника": "API-SPORT.ru (единственный источник)",
            "матч_найден": bool(raw.get("id") or game.get("id")),
            "match_id": raw.get("id") or game.get("id"),
            "турнир": self._compact(tournament),
            "сезон": self._compact(season),
            "команды": {"хозяева": home, "гости": away},
            "статус": self._compact(raw.get("status")),
            "форма_и_серии": self._compact(pregame),
            "статистика_матча": self._compact(raw.get("matchStatistics")),
            "события": self._compact(raw.get("liveEvents")),
            "коэффициенты": self._compact(raw.get("oddsBase")),
            "букмекерские_коэффициенты": self._compact(raw.get("oddsBk")),
        }

        home_games: list[dict[str, Any]] = []
        away_games: list[dict[str, Any]] = []
        if home_id is not None:
            home_games = await self.team_matches(home_id, tournament_id, season_id)
        if away_id is not None and away_id != home_id:
            away_games = await self.team_matches(away_id, tournament_id, season_id)

        home_rows, home_form = self._form(home_games, home_id or -1)
        away_rows, away_form = self._form(away_games, away_id or -1)
        h2h_games = home_games + [g for g in away_games if g.get("id") not in {x.get("id") for x in home_games}]
        h2h = self._h2h(h2h_games, home_id or -1, away_id or -1)

        context["форма_хозяев"] = home_rows
        context["форма_гостей"] = away_rows
        context["итоги_формы_хозяев"] = home_form
        context["итоги_формы_гостей"] = away_form
        context["очные_встречи_api_sport_ru"] = h2h
        context["качество_активных_данных"] = {
            "история_хозяев": len(home_rows),
            "история_гостей": len(away_rows),
            "h2h": len(h2h),
            "есть_таблица": False,
            "есть_сезонная_статистика": bool(raw.get("matchStatistics") or pregame or home_rows or away_rows),
        }
        context["статистика_для_ии"] = {
            "турнир": context["турнир"],
            "сезон": context["сезон"],
            "команды": context["команды"],
            "форма_хозяев": home_rows,
            "форма_гостей": away_rows,
            "итоги_формы_хозяев": home_form,
            "итоги_формы_гостей": away_form,
            "очные_встречи_api_sport_ru": h2h,
            "статистика_матча": context["статистика_матча"],
            "коэффициенты": context["коэффициенты"],
            "качество_активных_данных": context["качество_активных_данных"],
        }
        q = context["качество_активных_данных"]
        context["диагностика_статистики"] = (
            f"Источник: API-SPORT.ru; последние матчи: хозяева {q['история_хозяев']}, "
            f"гости {q['история_гостей']}; H2H: {q['h2h']}; "
            f"таблица: нет; расширенные данные: {'да' if q['есть_сезонная_статистика'] else 'нет'}."
        )
        return context
