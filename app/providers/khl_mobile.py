from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


class KHLMobileError(RuntimeError):
    pass


class KHLMobileClient:
    """Direct client for the KHL mobile/web API documented by shayypy/khl-api."""

    BASE_URL = "https://khl.api.webcaster.pro/api/khl_mobile"

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self._cache: dict[str, tuple[float, Any]] = {}

    async def _get(self, path: str, params: dict[str, Any] | None = None, ttl: int = 0) -> Any:
        params = dict(params or {})
        params.setdefault("application", "khl_web")
        params.setdefault("locale", "ru")
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and ttl > 0 and now - cached[0] < ttl:
            return cached[1]

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    follow_redirects=True,
                    trust_env=False,
                ) as client:
                    response = await client.get(f"{self.BASE_URL}/{path.lstrip('/')}", params=params)
                    response.raise_for_status()
                    payload = response.json()
                    if ttl > 0:
                        self._cache[key] = (now, payload)
                    return payload
            except Exception as exc:
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.25)
        raise KHLMobileError(f"KHL mobile API: {type(last_error).__name__}: {last_error}") from last_error

    async def common_data(self) -> dict[str, Any]:
        payload = await self._get("data.json", ttl=300)
        return payload.get("data", payload) if isinstance(payload, dict) else {}

    async def events(
        self,
        *,
        stage_id: int | None = None,
        team_id: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "order_direction": "asc",
            "page": page,
        }
        if stage_id is not None:
            params["stage_id"] = stage_id
        if team_id is not None:
            params["q[team_a_or_team_b_in][]"] = team_id
        if start is not None:
            params["q[start_at_gt_time_from_unixtime]"] = int(start.timestamp())
        if end is not None:
            params["q[start_at_lt_time_from_unixtime]"] = int(end.timestamp())
        payload = await self._get("events_v2.json", params, ttl=30)
        raw = payload.get("events", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            return []
        result: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("event"), dict):
                result.append(item["event"])
            elif isinstance(item, dict):
                result.append(item)
        return result

    async def event(self, event_id: int) -> dict[str, Any]:
        payload = await self._get("event_v2.json", {"id": event_id}, ttl=30)
        if isinstance(payload, dict) and isinstance(payload.get("event"), dict):
            return payload["event"]
        return payload if isinstance(payload, dict) else {}

    async def team(self, team_id: int, stage_id: int | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"id": team_id}
        if stage_id is not None:
            params["stage_id"] = stage_id
        payload = await self._get("team_v2.json", params, ttl=300)
        if isinstance(payload, dict) and isinstance(payload.get("team"), dict):
            return payload["team"]
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _norm(value: str) -> str:
        value = value.lower().replace("ё", "е")
        value = re.sub(r"[^a-zа-я0-9]+", " ", value)
        return " ".join(value.split())

    @classmethod
    def find_team_id(cls, teams: list[dict[str, Any]], name: str) -> int | None:
        needle = cls._norm(name)
        aliases = {
            "bars kazan": ["ак барс", "ак барс казань", "bars kazan"],
            "magnitogorsk": ["металлург", "металлург мг", "metallurg magnitogorsk"],
            "lada": ["лада", "лада тольятти"],
            "nizhny novgorod": ["торпедо", "торпедо нижний новгород"],
            "niznekamsk": ["нефтехимик", "нефтехимик нижнекамск"],
            "yekaterinburg": ["автомобилист", "автомобилист екатеринбург"],
            "cska moscow": ["цска", "цска москва"],
            "vladivostok": ["адмирал", "адмирал владивосток"],
        }
        candidates = [needle] + aliases.get(needle, [])
        for team in teams:
            team_name = cls._norm(str(team.get("name") or ""))
            if not team_name:
                continue
            if any(c == team_name or c in team_name or team_name in c for c in candidates):
                try:
                    return int(team.get("id"))
                except (TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _score(event: dict[str, Any]) -> tuple[int | None, int | None]:
        raw = str(event.get("score") or "")
        match = re.search(r"(\d+)\s*[:\-]\s*(\d+)", raw)
        if match:
            return int(match.group(1)), int(match.group(2))
        return None, None

    @classmethod
    def compact_event(cls, event: dict[str, Any], team_id: int | None = None) -> dict[str, Any]:
        a = event.get("team_a") or {}
        b = event.get("team_b") or {}
        sa, sb = cls._score(event)
        side = "home" if str(a.get("id")) == str(team_id) else "away"
        team_score = sa if side == "home" else sb
        opp_score = sb if side == "home" else sa
        if team_score is None or opp_score is None:
            result = "не сыгран"
        elif team_score > opp_score:
            result = "победа"
        elif team_score < opp_score:
            result = "поражение"
        else:
            result = "ничья"
        return {
            "id": event.get("id"),
            "дата": datetime.fromtimestamp(event.get("start_at", 0), tz=timezone.utc).strftime("%Y-%m-%d") if event.get("start_at") else "",
            "хозяева": a.get("name"),
            "гости": b.get("name"),
            "счёт": event.get("score") or "",
            "результат": result,
            "статус": event.get("game_state_key") or "",
        }

    async def build_match_context(self, home: str, away: str, match_start: str) -> dict[str, Any]:
        common = await self.common_data()
        stage_id = common.get("current_stage_id")
        teams = common.get("teams") or []
        if not isinstance(teams, list):
            teams = []

        home_id = self.find_team_id(teams, home)
        away_id = self.find_team_id(teams, away)
        try:
            start_dt = datetime.fromisoformat(match_start.replace("Z", "+00:00"))
        except ValueError:
            start_dt = datetime.now(timezone.utc)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        history_start = max(start_dt - timedelta(days=60), datetime.now(timezone.utc) - timedelta(days=120))
        history_end = start_dt + timedelta(hours=1)

        async def team_context(team_id: int | None, team_name: str) -> dict[str, Any]:
            if team_id is None:
                return {"команда": team_name, "ошибка": "Команда не найдена в официальном справочнике КХЛ"}
            team_payload, games = await asyncio.gather(
                self.team(team_id, stage_id),
                self.events(stage_id=stage_id, team_id=team_id, start=history_start, end=history_end),
            )
            finished = [g for g in games if g.get("game_state_key") == "finished"]
            finished.sort(key=lambda x: x.get("start_at", 0), reverse=True)
            recent = finished[:10]
            wins = losses = draws = gf = ga = 0
            for game in recent:
                a = game.get("team_a") or {}
                side = "home" if str(a.get("id")) == str(team_id) else "away"
                sa, sb = self._score(game)
                if sa is None or sb is None:
                    continue
                scored = sa if side == "home" else sb
                conceded = sb if side == "home" else sa
                gf += scored
                ga += conceded
                if scored > conceded:
                    wins += 1
                elif scored < conceded:
                    losses += 1
                else:
                    draws += 1

            stats = team_payload.get("stats") or []
            compact_stats: dict[str, Any] = {}
            if isinstance(stats, list):
                for item in stats:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("id") or item.get("title") or "")
                    if key:
                        compact_stats[key] = item.get("val")

            return {
                "команда": team_name,
                "id": team_id,
                "сезонная_статистика": compact_stats,
                "последние_10": [self.compact_event(g, team_id) for g in recent],
                "форма_10": {
                    "матчей": len(recent),
                    "побед": wins,
                    "поражений": losses,
                    "ничьих": draws,
                    "забито": gf,
                    "пропущено": ga,
                    "среднее_забито": round(gf / len(recent), 2) if recent else None,
                    "среднее_пропущено": round(ga / len(recent), 2) if recent else None,
                },
            }

        home_ctx, away_ctx = await asyncio.gather(
            team_context(home_id, home),
            team_context(away_id, away),
        )

        h2h: list[dict[str, Any]] = []
        if home_id is not None and away_id is not None:
            games_a = await self.events(stage_id=stage_id, team_id=home_id, start=start_dt - timedelta(days=730), end=start_dt)
            for game in games_a:
                a = game.get("team_a") or {}
                b = game.get("team_b") or {}
                ids = {str(a.get("id")), str(b.get("id"))}
                if ids == {str(home_id), str(away_id)} and game.get("game_state_key") == "finished":
                    h2h.append(self.compact_event(game))
            h2h = sorted(h2h, key=lambda x: x.get("дата", ""), reverse=True)[:10]

        pair_stat: dict[str, Any] = {}
        match_event = None
        if home_id is not None and away_id is not None:
            upcoming = await self.events(
                stage_id=stage_id,
                team_id=home_id,
                start=start_dt - timedelta(hours=12),
                end=start_dt + timedelta(hours=12),
            )
            for event in upcoming:
                a = event.get("team_a") or {}
                b = event.get("team_b") or {}
                if {str(a.get("id")), str(b.get("id"))} == {str(home_id), str(away_id)}:
                    match_event = event
                    break

        if match_event and match_event.get("id"):
            try:
                details = await self.event(int(match_event["id"]))
                pair_stat = details.get("this_pair_stat") or {}
                if details.get("bets"):
                    pair_stat["официальные_коэффициенты_кхл"] = details["bets"]
            except Exception:
                pass

        return {
            "источник_статистики": "Официальный KHL mobile API",
            "stage_id": stage_id,
            "сезон": next((s.get("name") for s in common.get("stages_v2", []) if isinstance(s, dict) and s.get("id") == stage_id), None),
            "хозяева": home_ctx,
            "гости": away_ctx,
            "очные_встречи": h2h,
            "очные_встречи_агрегат": pair_stat,
            "качество_данных": {
                "официальный_источник": True,
                "история_хозяев": len(home_ctx.get("последние_10", [])),
                "история_гостей": len(away_ctx.get("последние_10", [])),
                "h2h": len(h2h),
                "есть_сезонная_статистика": bool(home_ctx.get("сезонная_статистика") or away_ctx.get("сезонная_статистика")),
            },
        }
