from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
import asyncio
import httpx


class KHLScoreError(RuntimeError):
    pass


class KHLScoreClient:
    """Independent public-data fallback for KHL form, H2H and standings."""
    BASE = "https://www.sofascore.com/api/v1"

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout

    async def _get(self, path: str) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, trust_env=False) as client:
                r = await client.get(f"{self.BASE}/{path.lstrip('/')}")
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            raise KHLScoreError(f"SofaScore: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def norm(s: str) -> str:
        return " ".join(re.sub(r"[^a-zа-я0-9]+", " ", s.lower().replace("ё", "е")).split())

    @classmethod
    def same_team(cls, requested: str, actual: str) -> bool:
        a, b = cls.norm(requested), cls.norm(actual)
        aliases = {
            "dynamo moscow": ["dynamo moscow", "dinamo moscow", "динамо москва"],
            "dinamo minsk": ["dinamo minsk", "динамо минск"],
            "cska moscow": ["cska moscow", "цска москва", "цска"],
            "vladivostok": ["admiral", "адмирал", "admiral vladivostok"],
            "bars kazan": ["ak bars", "ак барс", "ak bars kazan"],
            "magnitogorsk": ["metallurg magnitogorsk", "metallurg mg", "металлург"],
            "lada": ["lada", "лада", "лада тольятти"],
            "nizhny novgorod": ["torpedo", "торпедо", "torpedo nizhny novgorod"],
            "niznekamsk": ["neftekhimik", "нефтехимик", "neftekhimik nizhnekamsk"],
            "yekaterinburg": ["avtomobilist", "автомобилист", "avtomobilist yekaterinburg"],
            "sochi": ["sochi", "hk sochi", "сочи", "хк сочи"],
            "ska st petersburg": ["ska", "ska st petersburg", "ska saint petersburg", "ска", "ска санкт петербург", "ска спб"],
            "spartak moscow": ["spartak", "spartak moscow", "спартак", "спартак москва"],
            "cherepovets": ["severstal", "severstal cherepovets", "северсталь", "северсталь череповец"],
            "omsk": ["avangard", "avangard omsk", "авангард", "авангард омск"],
            "novosibirsk": ["sibir", "sibir novosibirsk", "сибирь", "сибирь новосибирск"],
            "ufa": ["salavat yulaev", "salavat yulaev ufa", "салават юлаев", "салават юлаев уфа"],
            "yaroslavl": ["lokomotiv", "lokomotiv yaroslavl", "локомотив", "локомотив ярославль"],
        }
        candidates = aliases.get(a, [a])
        return b == a or a in b or b in a or any(c == b or c in b for c in candidates)

    @classmethod
    def is_khl(cls, e: dict[str, Any]) -> bool:
        t = e.get("tournament") or {}
        u = t.get("uniqueTournament") or {}
        text = cls.norm(str(u.get("name") or t.get("name") or u.get("slug") or ""))
        return "kontinental hockey league" in text or text in {"khl", "kontinental hockey league"}

    @staticmethod
    def score(e: dict[str, Any], side: str) -> int | None:
        s = e.get(f"{side}Score") or {}
        for k in ("normaltime", "current", "display"):
            if isinstance(s.get(k), (int, float)):
                return int(s[k])
        return None

    @classmethod
    def compact(cls, e: dict[str, Any], team_id: int | None = None) -> dict[str, Any]:
        h, a = e.get("homeTeam") or {}, e.get("awayTeam") or {}
        hs, aws = cls.score(e, "home"), cls.score(e, "away")
        home_side = str(h.get("id")) == str(team_id)
        ts, os = (hs, aws) if home_side else (aws, hs)
        result = "победа" if ts is not None and os is not None and ts > os else "поражение" if ts is not None and os is not None and ts < os else "ничья"
        return {"id": e.get("id"), "дата": datetime.fromtimestamp(int(e.get("startTimestamp", 0)), tz=timezone.utc).strftime("%Y-%m-%d") if e.get("startTimestamp") else "", "хозяева": h.get("name"), "гости": a.get("name"), "счет": f"{hs}:{aws}" if hs is not None and aws is not None else "", "результат": result}

    async def scheduled(self, day: str) -> list[dict[str, Any]]:
        p = await self._get(f"sport/ice-hockey/scheduled-events/{day}")
        return [e for e in p.get("events", []) if isinstance(e, dict) and self.is_khl(e)]

    async def find_match(self, home: str, away: str, start: datetime) -> dict[str, Any] | None:
        for e in await self.scheduled(start.date().isoformat()):
            if self.same_team(home, str((e.get("homeTeam") or {}).get("name", ""))) and self.same_team(away, str((e.get("awayTeam") or {}).get("name", ""))):
                return e
        return None

    async def team_events(self, team_id: int, pages: int = 3) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(pages):
            p = await self._get(f"team/{team_id}/events/last/{page}")
            events = [e for e in p.get("events", []) if isinstance(e, dict) and self.is_khl(e)]
            result.extend(events)
            if not p.get("hasNextPage") or not events:
                break
        return result

    async def standings(self, tournament_id: int, season_id: int) -> list[dict[str, Any]]:
        p = await self._get(f"unique-tournament/{tournament_id}/season/{season_id}/standings/total")
        rows = (p.get("standings") or [{}])[0].get("rows", [])
        return rows if isinstance(rows, list) else []

    async def build_context(self, home: str, away: str, match_start: str) -> dict[str, Any]:
        start = datetime.fromisoformat(match_start.replace("Z", "+00:00"))
        match = await self.find_match(home, away, start)
        if not match:
            raise KHLScoreError("Матч не найден в SofaScore")
        ht, at = match.get("homeTeam") or {}, match.get("awayTeam") or {}
        hid, aid = int(ht["id"]), int(at["id"])
        hg, ag = await asyncio.gather(self.team_events(hid), self.team_events(aid))
        cutoff = int(start.timestamp())
        hg = [e for e in hg if int(e.get("startTimestamp", 0)) < cutoff][:10]
        ag = [e for e in ag if int(e.get("startTimestamp", 0)) < cutoff][:10]

        def form(games: list[dict[str, Any]], tid: int) -> dict[str, Any]:
            w = l = gf = ga = 0
            for e in games:
                hs, aws = self.score(e, "home"), self.score(e, "away")
                if hs is None or aws is None: continue
                home_side = str((e.get("homeTeam") or {}).get("id")) == str(tid)
                s, o = (hs, aws) if home_side else (aws, hs)
                gf += s; ga += o
                if s > o: w += 1
                elif s < o: l += 1
            n = len(games)
            return {"матчей": n, "побед": w, "поражений": l, "забито": gf, "пропущено": ga, "среднее_забито": round(gf/n,2) if n else None, "среднее_пропущено": round(ga/n,2) if n else None}

        h2h = [e for e in hg if {str((e.get("homeTeam") or {}).get("id")), str((e.get("awayTeam") or {}).get("id"))} == {str(hid), str(aid)}][:10]
        tournament = (match.get("tournament") or {}).get("uniqueTournament") or {}
        season = match.get("season") or {}
        table = []
        if tournament.get("id") and season.get("id"):
            try: table = await self.standings(int(tournament["id"]), int(season["id"]))
            except Exception: pass
        return {
            "источник_статистики": "SofaScore",
            "хозяева": {"команда": home, "id": hid, "последние_10": [self.compact(e,hid) for e in hg], "форма_10": form(hg,hid)},
            "гости": {"команда": away, "id": aid, "последние_10": [self.compact(e,aid) for e in ag], "форма_10": form(ag,aid)},
            "очные_встречи": [self.compact(e) for e in h2h],
            "турнирная_таблица": [{"место": r.get("position"), "команда": (r.get("team") or {}).get("name"), "матчи": r.get("matches"), "победы": r.get("wins"), "поражения": r.get("losses"), "очки": r.get("points"), "забито": r.get("scoresFor"), "пропущено": r.get("scoresAgainst")} for r in table if isinstance(r,dict)],
            "качество_данных": {"источник": "SofaScore", "история_хозяев": len(hg), "история_гостей": len(ag), "h2h": len(h2h), "есть_таблица": bool(table), "есть_сезонная_статистика": bool(table)},
        }
