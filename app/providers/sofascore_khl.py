from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Market


class SofaScoreKHLClient:
    """Emergency KHL provider used when API-SPORT.ru is unavailable.

    SofaScore is used only as a reserve source. It supplies the KHL daily
    schedule, match odds when available, recent team results, H2H and
    standings. No API key is required for the public endpoints used here.
    """

    BASE_URL = "https://www.sofascore.com/api/v1"
    KHL_TOURNAMENT_ID = 268
    CACHE_TTL = 300.0
    TEAM_CACHE_TTL = 1800.0

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, path: str, cache_ttl: float = 0.0, **params: Any) -> dict[str, Any]:
        key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < cache_ttl:
            return cached[1]

        async with self._lock:
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] < cache_ttl:
                return cached[1]
            url = f"{self.BASE_URL}/{path.lstrip('/')}"
            print(f"SofaScore KHL REQUEST: GET {url} params={params}", flush=True)
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False, headers={"User-Agent": "Mozilla/5.0"}) as client:
                response = await client.get(url, params={k: v for k, v in params.items() if v is not None})
                print(f"SofaScore KHL RESPONSE: {response.status_code} {url}", flush=True)
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("SofaScore returned a non-object response")
            if cache_ttl > 0:
                self._cache[key] = (time.monotonic(), payload)
            return payload

    @staticmethod
    def _items(payload: dict[str, Any], key: str = "events") -> list[dict[str, Any]]:
        value = payload.get(key)
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

    @staticmethod
    def _team(event: dict[str, Any], side: str) -> dict[str, Any]:
        value = event.get(f"{side}Team") or event.get(f"{side}_team") or {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _score(event: dict[str, Any], side: str) -> int | None:
        score = event.get(f"{side}Score") or event.get(f"{side}_score") or {}
        if not isinstance(score, dict):
            try:
                return int(score)
            except (TypeError, ValueError):
                return None
        for key in ("current", "normaltime", "display", "period1", "goals"):
            value = score.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
        return None

    @staticmethod
    def _timestamp(event: dict[str, Any]) -> str:
        value = event.get("startTimestamp") or event.get("start_timestamp")
        if value is not None:
            try:
                return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                pass
        return str(event.get("startTime") or event.get("start_time") or "")

    @staticmethod
    def _is_khl(event: dict[str, Any]) -> bool:
        tournament = event.get("tournament") or {}
        unique = event.get("uniqueTournament") or tournament.get("uniqueTournament") if isinstance(tournament, dict) else {}
        if isinstance(unique, dict) and unique.get("id") == SofaScoreKHLClient.KHL_TOURNAMENT_ID:
            return True
        text = " ".join(str((tournament or {}).get(k, "")) for k in ("name", "slug")) if isinstance(tournament, dict) else str(tournament)
        return "khl" in text.lower() or "континент" in text.lower()

    async def today_games(self, date: str) -> list[dict[str, Any]]:
        payload = await self.get(f"sport/ice-hockey/scheduled-events/{date}", cache_ttl=self.CACHE_TTL)
        events = [event for event in self._items(payload) if self._is_khl(event)]
        result: list[dict[str, Any]] = []
        for event in sorted(events, key=self._timestamp):
            home = self._team(event, "home")
            away = self._team(event, "away")
            result.append({
                "id": event.get("id"),
                "date": self._timestamp(event),
                "datetime": self._timestamp(event),
                "teams": {
                    "home": {"id": home.get("id"), "name": home.get("name") or home.get("shortName") or ""},
                    "away": {"id": away.get("id"), "name": away.get("name") or away.get("shortName") or ""},
                },
                "league": {"name": "КХЛ", "id": self.KHL_TOURNAMENT_ID},
                "__sofascore": True,
                "__raw_sofascore": event,
            })
        return result

    @staticmethod
    def _collect_market_dicts(value: Any, result: list[dict[str, Any]]) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("choices"), list):
                result.append(value)
            for child in value.values():
                SofaScoreKHLClient._collect_market_dicts(child, result)
        elif isinstance(value, list):
            for child in value:
                SofaScoreKHLClient._collect_market_dicts(child, result)

    async def markets_for_event(self, event_id: int) -> tuple[Market, ...]:
        payload = await self.get(f"event/{int(event_id)}/odds/1/all", cache_ttl=self.CACHE_TTL)
        raw_markets: list[dict[str, Any]] = []
        self._collect_market_dicts(payload, raw_markets)
        markets: list[Market] = []
        for market in raw_markets:
            market_name = str(market.get("name") or market.get("marketName") or "Рынок")
            choices = market.get("choices") or []
            parsed: list[tuple[str, float]] = []
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                name = str(choice.get("name") or choice.get("label") or choice.get("value") or "").strip()
                raw_odd = choice.get("decimalValue") or choice.get("decimal") or choice.get("odds") or choice.get("price")
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
            if market.name not in best or market.odds > best[market.name].odds:
                best[market.name] = market
        return tuple(sorted(best.values(), key=lambda m: m.value_percent, reverse=True)[:50])

    async def _team_events(self, team_id: int) -> list[dict[str, Any]]:
        payload = await self.get(f"team/{int(team_id)}/events/last/0", cache_ttl=self.TEAM_CACHE_TTL)
        return self._items(payload)

    @staticmethod
    def _history(events: list[dict[str, Any]], team_id: int, limit: int = 10) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for event in sorted(events, key=SofaScoreKHLClient._timestamp, reverse=True):
            if event.get("status", {}).get("type") not in (None, "finished"):
                continue
            home = SofaScoreKHLClient._team(event, "home")
            away = SofaScoreKHLClient._team(event, "away")
            hs, aws = SofaScoreKHLClient._score(event, "home"), SofaScoreKHLClient._score(event, "away")
            if hs is None or aws is None:
                continue
            is_home = int(home.get("id", -1)) == team_id
            team_score, opp_score = (hs, aws) if is_home else (aws, hs)
            rows.append({
                "дата": SofaScoreKHLClient._timestamp(event)[:10],
                "хозяева": home.get("name", ""),
                "гости": away.get("name", ""),
                "счёт": f"{hs}:{aws}",
                "результат": "победа" if team_score > opp_score else "поражение" if team_score < opp_score else "ничья",
                "забито": team_score,
                "пропущено": opp_score,
                "дом": is_home,
            })
            if len(rows) >= limit:
                break
        return rows

    async def build_context(self, game: dict[str, Any]) -> dict[str, Any]:
        raw = game.get("__raw_sofascore") or {}
        home = self._team(raw, "home")
        away = self._team(raw, "away")
        home_id, away_id = home.get("id"), away.get("id")
        calls = await asyncio.gather(
            self._team_events(int(home_id)) if home_id else asyncio.sleep(0, result={}),
            self._team_events(int(away_id)) if away_id else asyncio.sleep(0, result={}),
            self.get(f"event/{int(game['id'])}/h2h", cache_ttl=self.TEAM_CACHE_TTL),
            self.get(f"event/{int(game['id'])}/standings", cache_ttl=self.TEAM_CACHE_TTL),
            return_exceptions=True,
        )
        home_events = calls[0] if isinstance(calls[0], dict) else {}
        away_events = calls[1] if isinstance(calls[1], dict) else {}
        h2h = calls[2] if isinstance(calls[2], dict) else {}
        standings = calls[3] if isinstance(calls[3], dict) else {}
        home_form = self._history(self._items(home_events), int(home_id), 10) if home_id else []
        away_form = self._history(self._items(away_events), int(away_id), 10) if away_id else []
        return {
            "главный_источник_статистики": "SofaScore",
            "активный_источник_статистики": "SofaScore (резерв)",
            "режим_источника": "Резерв после ошибки API-SPORT.ru",
            "диагностика_статистики": "API-SPORT.ru недоступен; использован резервный источник SofaScore.",
            "качество_активных_данных": {
                "источник": "SofaScore",
                "история_хозяев": len(home_form),
                "история_гостей": len(away_form),
                "h2h": len(self._items(h2h)),
                "есть_таблица": bool(standings),
            },
            "статистика_для_ии": {
                "последние_10_хозяев": home_form,
                "последние_10_гостей": away_form,
                "h2h": self._items(h2h),
                "таблица_кхл": standings,
            },
            "спортивный_контекст": {
                "матч_sofascore": raw,
                "последние_матчи_хозяев": home_form,
                "последние_матчи_гостей": away_form,
                "h2h": self._items(h2h),
                "таблица": standings,
            },
        }
