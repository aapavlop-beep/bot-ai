from __future__ import annotations

from typing import Any

from .models import Market, Match, Sport
from .web_odds import markets_from_web_research
from .web_odds_search import search_bookmaker_web
from .web_research import KHLWebResearcher


class EnhancedKHLService:
    """KHL analysis using browser-style web research only.

    No sports API is consulted here. All match facts, team news and bookmaker
    lines must come from the web-research layer.
    """

    def __init__(self) -> None:
        self.web_research = KHLWebResearcher()

    async def markets_for_game(
        self,
        game_id: int,
        game: dict[str, Any] | None = None,
        analysis_data: dict[str, Any] | None = None,
    ) -> tuple[Market, ...]:
        if analysis_data:
            web_context = analysis_data.get("веб_исследование") or {}
            markets, meta = markets_from_web_research(web_context)
            analysis_data["веб_линии"] = meta
            if markets:
                analysis_data["режим_линии"] = "browser web research"
                print(f"KHL web odds: found {len(markets)} markets", flush=True)
                return markets

        if game:
            teams = game.get("teams") or {}
            home = str((teams.get("home") or {}).get("name") or "")
            away = str((teams.get("away") or {}).get("name") or "")
            date = str(game.get("date") or game.get("datetime") or "")[:10]
            try:
                odds, bookmakers = await search_bookmaker_web(home, away, date)
                if odds:
                    markets = tuple(Market(name, odd, 1 / odd) for name, odd in odds.items())
                    if analysis_data is not None:
                        analysis_data["режим_линии"] = "dedicated browser bookmaker research"
                        analysis_data["веб_линии"] = {
                            "статус": "найдено",
                            "разрешенные_БК": ["Winline", "Фонбет", "BetBoom", "Parimatch"],
                            "источники_линии": {
                                name: {"БК": bookmakers.get(name, ""), "тип": "browser web research"}
                                for name in odds
                            },
                        }
                    print(
                        "KHL browser bookmaker odds: "
                        + ", ".join(f"{name}={odd} ({bookmakers.get(name, '')})" for name, odd in odds.items()),
                        flush=True,
                    )
                    return markets
            except Exception as exc:
                print(f"KHL browser bookmaker research failed: {type(exc).__name__}: {exc}", flush=True)

        print("KHL web odds: no confirmed Winline/Fonbet/BetBoom/Parimatch line found", flush=True)
        return ()

    async def analysis_for_game(self, game: dict[str, Any]) -> dict[str, Any]:
        teams = game.get("teams") or {}
        home = str((teams.get("home") or {}).get("name") or "")
        away = str((teams.get("away") or {}).get("name") or "")
        start = str(game.get("date") or game.get("datetime") or "")
        date = start[:10]

        try:
            web_context = await self.web_research.research_match(home, away, date)
        except Exception as exc:
            print(f"KHL browser web research failed: {type(exc).__name__}: {exc}", flush=True)
            web_context = {
                "собрано_в_utc": "",
                "матч": f"{home} — {away}",
                "дата_матча": date,
                "метод": "Google/Bing web search + HTML page extraction",
                "источники": [],
                "ошибка": type(exc).__name__,
            }

        source_count = len(web_context.get("источники") or [])
        official_count = sum(1 for s in web_context.get("источники") or [] if s.get("тип_источника") == "official")
        analysis_data: dict[str, Any] = {
            "активный_источник_статистики": "browser web research",
            "резервный_источник": "browser web research",
            "режим_источника": "browser-only web research",
            "веб_исследование": web_context,
            "веб_источники": web_context.get("источники", []),
            "источники_проверки": [s.get("url") for s in web_context.get("источники", []) if s.get("url")][:20],
            "статистика_для_ии": web_context,
            "качество_активных_данных": {
                "история_хозяев": 0,
                "история_гостей": 0,
                "h2h": 0,
                "есть_сезонная_статистика": bool((web_context.get("структурированные_доказательства") or {}).get("standings")),
                "есть_таблица": bool((web_context.get("структурированные_доказательства") or {}).get("standings")),
                "web_sources": source_count,
                "official_sources": official_count,
            },
            "правило_травм_и_составов": (
                "Использовать только явно подтверждённые веб-источниками сведения. "
                "Отсутствие игрока в составе само по себе не означает травму."
            ),
            "правило_коэффициентов": (
                "Использовать только текущие коэффициенты Winline, Фонбет, BetBoom или Parimatch, "
                "полученные через браузерный web research. Не использовать API, SofaScore или неидентифицированные линии."
            ),
        }
        return analysis_data

    @staticmethod
    def to_match(game: dict[str, Any], markets: tuple[Market, ...] = (), analysis_data: dict[str, Any] | None = None) -> Match:
        teams = game.get("teams") or {}
        home_obj = teams.get("home") or {}
        away_obj = teams.get("away") or {}
        home = str(home_obj.get("name") or "Хозяева")
        away = str(away_obj.get("name") or "Гости")
        league = str((game.get("league") or {}).get("name") or "КХЛ")
        start_time = str(game.get("date") or game.get("datetime") or "")
        return Match(
            sport=Sport.KHL,
            league=league,
            home=home,
            away=away,
            start_time=start_time,
            markets=markets,
            analysis_data=analysis_data or {},
        )

    @staticmethod
    def format_game(match: Match) -> str:
        return f"🏒 <b>{match.home} — {match.away}</b>\n🕒 {match.start_time}\n🏆 {match.league}\n"
