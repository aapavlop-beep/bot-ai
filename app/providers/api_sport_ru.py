from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Market


class ApiSportRuError(RuntimeError):
    pass


class ApiSportRuClient:
    """Client for API-SPORT.ru Sport Events API (v2)."""

    BASE_URL = "https://api.api-sport.ru/v2"

    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        url = f"{self.BASE_URL}/{path.lstrip('/')}"
        safe_params = {k: v for k, v in params.items() if v is not None}
        print(f"API-SPORT.ru REQUEST: GET {url} params={safe_params}", flush=True)
        headers = {"Authorization": self.api_key, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
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

    @classmethod
    def _teams(cls, match: dict[str, Any]) -> tuple[str, str]:
        teams = match.get("teams") or {}
        home = match.get("homeTeam") or match.get("home") or teams.get("home")
        away = match.get("awayTeam") or match.get("away") or teams.get("away")
        return cls._name(home), cls._name(away)

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
        home, away = cls._teams(match)
        tournament = match.get("tournament") or match.get("league") or {}
        tournament_name = tournament.get("name") if isinstance(tournament, dict) else str(tournament or "КХЛ")
        return {
            "id": match.get("id"),
            "date": cls._match_date(match),
            "datetime": cls._match_date(match),
            "teams": {"home": {"name": home}, "away": {"name": away}},
            "league": {"name": str(tournament_name or "КХЛ"), "id": tournament.get("id") if isinstance(tournament, dict) else None},
            "__api_sport_ru": True,
            "__raw_api_sport_ru": match,
        }

    async def khl_matches(self, date: str | None = None) -> list[dict[str, Any]]:
        payload = await self.get("ice-hockey/matches", date=date, limit=100)
        matches = self._items(payload)
        khl = [item for item in matches if self._is_khl(item)]
        return khl or matches

    async def match_by_id(self, match_id: int | str) -> dict[str, Any]:
        payload = await self.get(f"ice-hockey/matches/{int(match_id)}")
        return self._one(payload)

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
            market_name = str(market.get("name") or market.get("group") or "Рынок")
            values = market.get("choices") or market.get("outcomes") or market.get("values") or []
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
            result: dict[str, Any] = {}
            for key, item in value.items():
                compact = ApiSportRuClient._compact(item, depth + 1)
                if compact is not None or item is None:
                    result[str(key)] = compact
            return result
        return str(value)

    @classmethod
    def _count_history(cls, value: Any) -> int:
        if isinstance(value, list):
            dicts = [x for x in value if isinstance(x, dict)]
            if dicts and any(any(k in x for k in ("dateEvent", "startTimestamp", "homeTeam", "awayTeam", "homeScore", "awayScore", "homeScoreDisplay", "awayScoreDisplay")) for x in dicts):
                return len(dicts)
            return max((cls._count_history(x) for x in value), default=0)
        if isinstance(value, dict):
            for key in ("matches", "games", "results", "history", "lastMatches", "previousMatches", "items", "events", "form"):
                if key in value:
                    count = cls._count_history(value[key])
                    if count:
                        return count
            return max((cls._count_history(x) for x in value.values()), default=0)
        return 0

    @classmethod
    def _quality(cls, pregame: Any) -> dict[str, int | bool]:
        empty = {"история_хозяев": 0, "история_гостей": 0, "h2h": 0, "есть_таблица": False, "есть_сезонная_статистика": False}
        if not isinstance(pregame, dict):
            return empty
        form = pregame.get("form") or pregame.get("teamForm") or {}
        h2h = pregame.get("h2h") or pregame.get("headToHead") or {}
        home_form = form.get("home") or form.get("homeTeam") or form.get("host") if isinstance(form, dict) else None
        away_form = form.get("away") or form.get("awayTeam") or form.get("guest") if isinstance(form, dict) else None
        home_count = cls._count_history(home_form) if home_form is not None else 0
        away_count = cls._count_history(away_form) if away_form is not None else 0
        return {
            "история_хозяев": home_count,
            "история_гостей": away_count,
            "h2h": cls._count_history(h2h),
            "есть_таблица": bool(pregame.get("standings") or pregame.get("table")),
            "есть_сезонная_статистика": bool(form or pregame.get("teamStreaks") or pregame.get("seasonStats")),
        }

    async def find_khl_match(self, home: str, away: str, start_time: str = "") -> dict[str, Any] | None:
        date = start_time[:10] if len(start_time) >= 10 else datetime.now(timezone.utc).date().isoformat()
        payload = await self.get("ice-hockey/matches", date=date, with_pregame="true", limit=100)
        matches = self._items(payload)
        candidates = [item for item in matches if self._is_khl(item)]
        found = self._find_match(candidates, home, away) or self._find_match(matches, home, away)
        if found:
            return found
        for query in (f"{home} {away}", home, away):
            try:
                search_payload = await self.get("ice-hockey/matches", q=query, limit=50, with_pregame="true")
                found = self._find_match(self._items(search_payload), home, away)
                if found:
                    return found
            except Exception:
                continue
        return None

    async def build_context(self, home: str, away: str, start_time: str = "") -> dict[str, Any]:
        match = await self.find_khl_match(home, away, start_time)
        if not match:
            return {"источник_статистики": "API-SPORT.ru", "матч_найден": False, "качество_данных": {"история_хозяев": 0, "история_гостей": 0, "h2h": 0, "есть_таблица": False, "есть_сезонная_статистика": False}}
        match_id = match.get("id")
        detail = match
        if match_id is not None:
            try:
                detail = await self.match_by_id(match_id)
            except Exception:
                detail = match
        pregame = detail.get("pregame") or match.get("pregame") or {}
        return {
            "источник_статистики": "API-SPORT.ru",
            "матч_найден": True,
            "match_id": match_id,
            "турнир": self._compact(detail.get("tournament") or match.get("tournament")),
            "сезон": self._compact(detail.get("season") or match.get("season")),
            "команды": {"хозяева": self._teams(detail)[0], "гости": self._teams(detail)[1]},
            "статус": detail.get("status"),
            "счёт": self._compact(detail.get("homeScore")),
            "счёт_гостей": self._compact(detail.get("awayScore")),
            "форма_и_серии": self._compact(pregame),
            "статистика_матча": self._compact(detail.get("matchStatistics")),
            "коэффициенты": self._compact(detail.get("oddsBase")),
            "букмекерские_коэффициенты": self._compact(detail.get("oddsBk")),
            "качество_данных": self._quality(pregame),
        }
