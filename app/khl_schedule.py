from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

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


def _build_game(date_value: str, time_value: str, raw_home: str, raw_away: str) -> dict[str, Any] | None:
    home = _canonical_team(raw_home)
    away = _canonical_team(raw_away)
    if not _is_khl_team(home) or not _is_khl_team(away) or home == away:
        return None
    return {
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


def _extract_fixtures(text: str, date_value: str) -> list[dict[str, Any]]:
    text = text.replace("–", "—").replace("−", "—")
    date_full = datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    date_short = datetime.fromisoformat(date_value).strftime("%d.%m")
    patterns = [
        re.compile(rf"{re.escape(date_full)}\s+(?:{re.escape(date_full)}\s+)?(\d{{1,2}}:\d{{2}})\s+(.{{2,60}}?)\s+—\s+(.{{2,60}}?)(?=\s*\||\s*-\s*:\s*-|\s+КХЛ|\s+регуляр|$)", re.I),
        re.compile(rf"{re.escape(date_short)}\.?\s*(?:\d{{4}}\s+)?(\d{{1,2}}:\d{{2}})\s+(.{{2,60}}?)\s+—\s+(.{{2,60}}?)(?=\s*\||\s*-\s*:\s*-|\s+КХЛ|\s+регуляр|$)", re.I),
    ]
    found: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            game = _build_game(date_value, *[m.strip(" -–—|") for m in match.groups()])
            if game and not any(x["id"] == game["id"] for x in found):
                found.append(game)
    return found


async def _extract_direct_calendar(researcher: KHLWebResearcher, url: str, date_value: str) -> list[dict[str, Any]]:
    try:
        response = await researcher._client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # Keep table text; do not limit extraction to <article>/<main>, because
        # Championat's calendar is rendered in a table outside those containers.
        text = _normalize_text(soup.get_text(" ", strip=True))
        return _extract_fixtures(text, date_value)
    except Exception as exc:
        print(f"KHL direct calendar failed: {url}: {type(exc).__name__}: {exc}", flush=True)
        return []


async def _web_games_for_date(date_value: str) -> list[dict[str, Any]]:
    researcher = KHLWebResearcher()
    games: list[dict[str, Any]] = []

    # Primary browser page. It contains the complete 2026/27 KHL calendar.
    direct_urls = [
        "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/",
        "https://www.championat.com/hockey/_superleague.html",
        f"https://x2sport.ru/calendar?from={date_value}",
    ]
    for url in direct_urls:
        for game in await _extract_direct_calendar(researcher, url, date_value):
            if not any(x["id"] == game["id"] for x in games):
                games.append(game)
        if len(games) >= 4:
            break

    # Search is supplemental only. A Google 429 must never erase the direct
    # browser-page results.
    queries = [
        f'КХЛ {date_value} расписание матчи',
        f'site:championat.com/hockey/_superleague {date_value} КХЛ расписание',
    ]
    for query in queries:
        try:
            results = await researcher.search(query)
        except Exception as exc:
            print(f"KHL schedule web search failed: {type(exc).__name__}: {exc}", flush=True)
            continue
        for result in results[:6]:
            text = result.snippet
            if text:
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
