from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .team_names import display_team_name
from .web_research import KHLWebResearcher

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
        "динамо м": "Динамо Москва", "динамо москва": "Динамо Москва",
        "динамо мн": "Динамо Минск", "динамо минск": "Динамо Минск",
        "металлург мг": "Металлург Мг", "металлург магнитогорск": "Металлург Мг",
        "хк сочи": "ХК Сочи", "сочи": "ХК Сочи",
        "цска москва": "ЦСКА", "цска": "ЦСКА",
        "шд": "Шанхайские Драконы",
    }
    return aliases.get(low, display_team_name(value))


def _game_id(date_value: str, home: str, away: str) -> int:
    raw = f"khl-web|{date_value}|{home}|{away}".encode("utf-8")
    return 1_000_000_000 + (zlib.crc32(raw) & 0x3FFFFFFF)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _is_khl_team(value: str) -> bool:
    low = _canonical_team(value).lower()
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
    """Extract fixtures from public calendar/search HTML.

    Championat duplicates date/time columns and represents an empty score as
    "– : –". The old parser only accepted ASCII hyphens, which caused a valid
    browser result to become zero games. Keep the parser deliberately strict
    about team names but tolerant about dash/score formatting.
    """
    text = _normalize_text(text).replace("−", "—").replace("–", "—")
    date_full = datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    date_short = datetime.fromisoformat(date_value).strftime("%d.%m")
    score_marker = r"—\s*:\s*—"
    patterns = [
        re.compile(
            rf"{re.escape(date_full)}\s+(\d{{1,2}}:\d{{2}})\s+"
            rf"{re.escape(date_full)}\s+\1\s+(.{{2,80}}?)\s+—\s+(.{{2,80}}?)(?=\s*(?:\||{score_marker}|\bКХЛ\b|\bрегуляр|$))",
            re.I,
        ),
        re.compile(
            rf"{re.escape(date_full)}\s+(\d{{1,2}}:\d{{2}})\s+"
            rf"(.{{2,80}}?)\s+—\s+(.{{2,80}}?)(?=\s*(?:\||{score_marker}|\bКХЛ\b|\bрегуляр|$))",
            re.I,
        ),
        re.compile(
            rf"{re.escape(date_short)}\.?\s*(?:\d{{4}}\s+)?(\d{{1,2}}:\d{{2}})\s+"
            rf"(.{{2,80}}?)\s+—\s+(.{{2,80}}?)(?=\s*(?:\||{score_marker}|\bКХЛ\b|\bрегуляр|$))",
            re.I,
        ),
    ]
    found: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            time_value, raw_home, raw_away = [m.strip(" -–—|") for m in match.groups()]
            game = _build_game(date_value, time_value, raw_home, raw_away)
            if game and not any(x["id"] == game["id"] for x in found):
                found.append(game)
    return found


async def _extract_direct_calendar(researcher: KHLWebResearcher, url: str, date_value: str) -> list[dict[str, Any]]:
    try:
        response = await researcher._client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = _normalize_text(soup.get_text(" ", strip=True))
        games = _extract_fixtures(text, date_value)
        if games:
            print(f"KHL browser page parsed: {url} -> {len(games)} games", flush=True)
        return games
    except Exception as exc:
        print(f"KHL direct calendar failed: {url}: {type(exc).__name__}: {exc}", flush=True)
        return []


async def _web_games_for_date(date_value: str) -> list[dict[str, Any]]:
    researcher = KHLWebResearcher()
    games: list[dict[str, Any]] = []
    direct_urls = [
        "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/",
        "https://www.championat.com/hockey/_superleague.html",
    ]

    # First use real public calendar pages, not sports APIs.
    for url in direct_urls:
        for game in await _extract_direct_calendar(researcher, url, date_value):
            if not any(x["id"] == game["id"] for x in games):
                games.append(game)
        if len(games) >= 4:
            break

    # Search snippets are a second browser-only fallback. This also works when
    # the calendar HTML is blocked by a hosting provider.
    for query in (
        f'КХЛ {date_value} расписание матчи',
        f'"{datetime.fromisoformat(date_value).strftime("%d.%m.%Y")}" КХЛ матчи',
        f'site:championat.com/hockey {_normalize_text(date_value)} КХЛ расписание',
    ):
        try:
            results = await researcher.search(query)
        except Exception as exc:
            print(f"KHL schedule web search failed: {type(exc).__name__}: {exc}", flush=True)
            continue
        for result in results[:10]:
            payload = " ".join((result.title, result.snippet, result.text))
            for game in _extract_fixtures(_normalize_text(payload), date_value):
                if not any(x["id"] == game["id"] for x in games):
                    games.append(game)
        if len(games) >= 4:
            break

    games.sort(key=lambda x: x.get("datetime", ""))
    return games


async def verified_games_for_date(date_value: str) -> list[dict[str, Any]]:
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
