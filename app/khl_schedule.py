from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .team_names import display_team_name
from .web_research import KHLWebResearcher, SearchResult

MSK = ZoneInfo("Europe/Moscow")

KHL_TEAMS = {
    "Авангард", "Автомобилист", "Адмирал", "Ак Барс", "Амур", "Барыс",
    "Витязь", "Динамо Москва", "Динамо М", "Динамо Минск", "Динамо Мн",
    "Лада", "Локомотив", "Металлург Мг", "Металлург Магнитогорск",
    "Нефтехимик", "СКА", "Салават Юлаев", "Северсталь", "Сибирь", "Спартак",
    "Торпедо", "Трактор", "Шанхайские Драконы", "ХК Сочи", "Сочи", "ЦСКА",
    "Куньлунь Ред Стар", "Куньлунь",
}


def _canonical_team(value: str) -> str:
    value = re.sub(r"[«»\"()]", "", value or "")
    value = re.sub(r"\s+", " ", value).strip(" -–—|")
    low = value.lower()
    aliases = {
        "динамо м": "Динамо Москва",
        "динамо москва": "Динамо Москва",
        "динамо мн": "Динамо Минск",
        "динамо минск": "Динамо Минск",
        "металлург мг": "Металлург Мг",
        "металлург магнитогорск": "Металлург Мг",
        "хк сочи": "ХК Сочи",
        "сочи": "ХК Сочи",
        "цска москва": "ЦСКА",
        "цска": "ЦСКА",
        "шд": "Шанхайские Драконы",
    }
    return aliases.get(low, display_team_name(value))


def _game_id(date_value: str, home: str, away: str) -> int:
    raw = f"khl-web|{date_value}|{home}|{away}".encode("utf-8")
    return 1_000_000_000 + (zlib.crc32(raw) & 0x3FFFFFFF)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_khl_team(value: str) -> bool:
    value = _canonical_team(value)
    low = value.lower()
    return any(low == team.lower() or low in team.lower() or team.lower() in low for team in KHL_TEAMS)


def _extract_fixtures(text: str, date_value: str) -> list[dict[str, Any]]:
    """Extract KHL fixtures from browser-retrieved schedule text/snippets."""
    text = text.replace("–", "—").replace("−", "—")
    year = datetime.fromisoformat(date_value).year
    date_full = datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    date_short = datetime.fromisoformat(date_value).strftime("%d.%m")
    patterns = [
        re.compile(
            rf"{re.escape(date_full)}\s+(?:{re.escape(date_full)}\s+)?(\d{{1,2}}:\d{{2}})\s+(.{{2,60}}?)\s+—\s+(.{{2,60}}?)(?=\s*\||\s*-\s*:\s*-|\s+КХЛ|\s+регуляр|$)",
            re.I,
        ),
        re.compile(
            rf"{re.escape(date_short)}\.?\s*(?:{year}\s+)?(\d{{1,2}}:\d{{2}})\s+(.{{2,60}}?)\s+—\s+(.{{2,60}}?)(?=\s*\||\s*-\s*:\s*-|\s+КХЛ|\s+регуляр|$)",
            re.I,
        ),
        re.compile(
            rf"(\d{{1,2}}:\d{{2}})\s+(.{{2,60}}?)\s+—\s+(.{{2,60}}?)(?=\s*\||\s*-\s*:\s*-|\s+КХЛ|\s+регуляр|$)",
            re.I,
        ),
    ]
    found: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            time_value, raw_home, raw_away = [m.strip(" -–—|") for m in match.groups()]
            home = _canonical_team(raw_home)
            away = _canonical_team(raw_away)
            if not _is_khl_team(home) or not _is_khl_team(away) or home == away:
                continue
            item = {
                "id": _game_id(date_value, home, away),
                "date": date_value,
                "datetime": f"{date_value}T{time_value}:00+03:00",
                "teams": {
                    "home": {"name": display_team_name(home)},
                    "away": {"name": display_team_name(away)},
                },
                "league": {"name": "КХЛ"},
                "__web_research": True,
            }
            if not any(x["id"] == item["id"] for x in found):
                found.append(item)
    return found


async def _web_games_for_date(date_value: str) -> list[dict[str, Any]]:
    researcher = KHLWebResearcher()
    unique: dict[str, SearchResult] = {}

    # Direct browser-page seeds. These are normal public webpages, not sports
    # APIs, and they prevent a temporary Google/Bing rate-limit from making the
    # daily schedule empty.
    direct_urls = [
        "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/",
        "https://www.championat.com/hockey/_superleague.html",
        f"https://x2sport.ru/calendar?from={date_value}",
    ]
    for url in direct_urls:
        unique[url] = SearchResult("KHL browser schedule", url, "")

    # Search is supplemental: if a search engine is available it can discover
    # newer calendar pages or another schedule source. A 429 is non-fatal.
    queries = [
        f'КХЛ {date_value} расписание матчи',
        f'КХЛ {date_value} календарь игр',
        f'site:championat.com/hockey/_superleague {date_value} КХЛ расписание',
    ]
    for query in queries:
        try:
            results = await researcher.search(query)
        except Exception as exc:
            print(f"KHL schedule web search failed: {type(exc).__name__}: {exc}", flush=True)
            continue
        for result in results:
            if result.url not in unique:
                unique[result.url] = result

    results = list(unique.values())
    results.sort(key=lambda r: (
        2 if "championat.com/hockey/_superleague/tournament/7092/calendar" in r.url else
        1 if "championat.com" in r.url else 0,
        1 if "x2sport.ru" in r.url else 0,
    ), reverse=True)

    games: list[dict[str, Any]] = []
    for result in results[:15]:
        texts = [result.snippet]
        try:
            page = await researcher._extract_page(result)
            if page.text:
                texts.insert(0, page.text)
        except Exception as exc:
            print(f"KHL schedule page failed: {result.url}: {type(exc).__name__}", flush=True)
        for text in texts:
            for game in _extract_fixtures(_normalize_text(text), date_value):
                if not any(x["id"] == game["id"] for x in games):
                    games.append(game)

    games.sort(key=lambda x: x.get("datetime", ""))
    return games


async def verified_games_for_date(date_value: str) -> list[dict[str, Any]]:
    """Return KHL schedule from browser web research only."""
    try:
        games = await _web_games_for_date(date_value)
        print(f"KHL schedule source: BROWSER WEB RESEARCH ({len(games)} games) date={date_value}", flush=True)
        return games
    except Exception as exc:
        print(f"KHL browser schedule research failed date={date_value}: {type(exc).__name__}: {exc}", flush=True)
        return []


async def verified_today_games() -> list[dict[str, Any]]:
    today = datetime.now(MSK).date().isoformat()
    return await verified_games_for_date(today)


def upcoming_moscow_dates(days: int = 3) -> list[str]:
    today = datetime.now(MSK).date()
    return [(today + timedelta(days=i)).isoformat() for i in range(max(1, days))]
