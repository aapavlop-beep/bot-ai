from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx


class ApiSportRuError(RuntimeError):
    pass


class ApiSportRuClient:
    """Client for API-SPORT.ru Sport Events API (v2)."""

    BASE_URL = "https://api.api-sport.ru/v2"

    def __init__(self, api_key: str, timeout: float = 20.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        headers = {"Authorization": self.api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.BASE_URL}/{path.lstrip('/')}",
                headers=headers,
                params={k: v for k, v in params.items() if v is not None},
            )
            response.raise_for_status()
            payload = response.json()
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
                for nested_key in ("matches", "data", "items", "results"):
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
    def _norm(value: str) -> str:
        value = value.lower().replace("ё", "е")
        replacements = {
            "мск": "москва",
            "moscow": "москва",
            "dinamo": "динамо",
            "dynamo": "динамо",
            "минск": "минск",
            "minsk": "минск",
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
        common = at & bt
        return len(common) >= max(1, min(len(at), len(bt)) - 1)

    @classmethod
    def _find_match(cls, matches: list[dict[str, Any]], home: str, away: str) -> dict[str, Any] | None:
        for item in matches:
            mh, ma = cls._teams(item)
            if cls._same_team(mh, home) and cls._same_team(ma, away):
                return item
        return None

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
            if dicts and any(
                any(k in x for k in ("dateEvent", "startTimestamp", "homeTeam", "awayTeam", "homeScore", "awayScore"))
                for x in dicts
            ):
                return len(dicts)
            return max((cls._count_history(x) for x in value), default=0)
        if isinstance(value, dict):
            for key in ("form", "home", "away", "matches", "games", "results", "history", "lastMatches", "previousMatches"):
                if key in value:
                    count = cls._count_history(value[key])
                    if count:
                        return count
            return max((cls._count_history(x) for x in value.values()), default=0)
        return 0

    @classmethod
    def _quality(cls, pregame: Any) -> dict[str, int | bool]:
        if not isinstance(pregame, dict):
            return {"история_хозяев": 0, "история_гостей": 0, "h2h": 0, "есть_таблица": False, "есть_сезонная_статистика": False}
        form = pregame.get("form") or pregame.get("teamForm") or {}
        h2h = pregame.get("h2h") or pregame.get("headToHead") or {}
        home_form = form.get("home") if isinstance(form, dict) else None
        away_form = form.get("away") if isinstance(form, dict) else None
        return {
            "история_хозяев": cls._count_history(home_form if home_form is not None else form),
            "история_гостей": cls._count_history(away_form if away_form is not None else form),
            "h2h": cls._count_history(h2h),
            "есть_таблица": bool(pregame.get("standings") or pregame.get("table")),
            "есть_сезонная_статистика": bool(form or pregame.get("teamStreaks") or pregame.get("seasonStats")),
        }

    async def find_khl_match(self, home: str, away: str, start_time: str = "") -> dict[str, Any] | None:
        date = start_time[:10] if len(start_time) >= 10 else datetime.now(timezone.utc).date().isoformat()

        # API-SPORT.ru documents date as a filter for /matches. Do not send
        # undocumented parameters here: an invalid optional parameter can make
        # the entire request fail and silently remove this provider from the
        # source ranking.
        payload = await self.get("ice-hockey/matches", date=date)
        matches = self._items(payload)

        # First try KHL-labelled tournaments, then the full daily feed. Team
        # matching is deliberately independent of the tournament name because
        # providers may return localized names such as KHL / КХЛ / Kontinental.
        candidates = []
        for item in matches:
            tournament = item.get("tournament") or item.get("league") or {}
            tournament_name = tournament.get("name") if isinstance(tournament, dict) else str(tournament)
            normalized_tournament = self._norm(str(tournament_name or ""))
            if "кхл" in normalized_tournament or "khl" in normalized_tournament or "kontinental" in normalized_tournament:
                candidates.append(item)

        return self._find_match(candidates, home, away) or self._find_match(matches, home, away)

    async def build_context(self, home: str, away: str, start_time: str = "") -> dict[str, Any]:
        match = await self.find_khl_match(home, away, start_time)
        if not match:
            return {
                "источник_статистики": "API-SPORT.ru",
                "матч_найден": False,
                "качество_данных": {"история_хозяев": 0, "история_гостей": 0, "h2h": 0, "есть_таблица": False, "есть_сезонная_статистика": False},
            }

        match_id = match.get("id")
        detail = match
        if match_id is not None:
            try:
                detail_payload = await self.get(f"ice-hockey/matches/{int(match_id)}")
                detail = self._one(detail_payload)
            except Exception:
                detail = match

        pregame = detail.get("pregame") or match.get("pregame") or {}
        quality = self._quality(pregame)
        return {
            "источник_статистики": "API-SPORT.ru",
            "матч_найден": True,
            "match_id": match_id,
            "турнир": self._compact(detail.get("tournament") or match.get("tournament")),
            "сезон": self._compact(detail.get("season") or match.get("season")),
            "команды": {
                "хозяева": self._teams(detail)[0],
                "гости": self._teams(detail)[1],
            },
            "статус": detail.get("status"),
            "счёт": self._compact(detail.get("homeScore")),
            "счёт_гостей": self._compact(detail.get("awayScore")),
            "форма_и_серии": self._compact(pregame),
            "статистика_матча": self._compact(detail.get("matchStatistics")),
            "коэффициенты": self._compact(detail.get("oddsBase")),
            "букмекерские_коэффициенты": self._compact(detail.get("oddsBk")),
            "качество_данных": quality,
        }
