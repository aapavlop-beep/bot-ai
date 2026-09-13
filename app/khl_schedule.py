from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .team_names import display_team_name
from .web_research import KHLWebResearcher

MSK = ZoneInfo("Europe/Moscow")

KHL_TEAMS = {
    "Авангард", "Автомобилист", "Адмирал", "Ак Барс", "Амур", "Барыс",
    "Витязь", "Динамо Москва", "Динамо М", "Динамо Минск", "Динамо Мн",
    "Лада", "Локомотив", "Металлург Мг", "Металлург Магнитогорск",
    "Нефтехимик", "СКА", "Салават Юлаев", "Северсталь", "Сибирь", "Спартак",
    "Торпедо", "Трактор", "Шанхайские Драконы", "Шанхай Дрэгонс", "ХК Сочи", "Сочи", "ЦСКА",
    "Куньлунь Ред Стар", "Куньлунь",
}

ALIASES = {
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
    "шанхай дрэгонс": "Шанхайские Драконы",
    "куньлунь ред стар": "Куньлунь Ред Стар",
}


def _canonical_team(value: str) -> str:
    value = re.sub(r"[«»\"()]", "", value or "")
    value = re.sub(r"\s+", " ", value).strip(" -–—|·")
    return ALIASES.get(value.lower(), display_team_name(value))


def _game_id(date_value: str, home: str, away: str) -> int:
    raw = f"khl-browser|{date_value}|{home}|{away}".encode("utf-8")
    return 1_000_000_000 + (zlib.crc32(raw) & 0x3FFFFFFF)


def _build_game(date_value: str, time_value: str, raw_home: str, raw_away: str) -> dict[str, Any] | None:
    home = _canonical_team(raw_home)
    away = _canonical_team(raw_away)
    known = {_canonical_team(x).lower() for x in KHL_TEAMS}
    if home.lower() not in known or away.lower() not in known or home == away:
        return None
    return {
        "id": _game_id(date_value, home, away),
        "date": date_value,
        "datetime": f"{date_value}T{time_value}:00+03:00",
        "teams": {"home": {"name": display_team_name(home)}, "away": {"name": display_team_name(away)}},
        "league": {"name": "КХЛ"},
        "__web_research": True,
    }


def _team_pattern() -> str:
    names = sorted(KHL_TEAMS | set(ALIASES.keys()), key=len, reverse=True)
    return "(?:" + "|".join(re.escape(x) for x in names) + ")"


def _extract_fixtures(text: str, requested_date: str) -> list[dict[str, Any]]:
    """Extract only fixtures whose explicit date equals requested_date.

    Handles browser/search text from Sports.ru and Championat, including score
    placeholders between the two team names and duplicated date/time columns.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []

    date_full = datetime.fromisoformat(requested_date).strftime("%d.%m.%Y")
    team = _team_pattern()
    # Examples handled:
    # 13.09.2026 13:30 Сибирь - - - Автомобилист
    # 13.09.2026 13:30 13.09.2026 13:30 Сибирь – Автомобилист
    # 13.09.2026 17:00 ЦСКА - - - Локомотив
    pattern = re.compile(
        rf"({re.escape(date_full)})\s+(\d{{1,2}}:\d{{2}})"
        rf"(?:\s+{re.escape(date_full)}\s+\d{{1,2}}:\d{{2}})?\s+"
        rf"({team})(?:\s*[-–—]\s*){{1,4}}({team})",
        re.I,
    )

    found: list[dict[str, Any]] = []
    for match in pattern.finditer(text):
        date_raw, time_value, home, away = match.groups()
        game = _build_game(requested_date, time_value, home, away)
        if game and not any(x["id"] == game["id"] for x in found):
            found.append(game)
    return found


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup.select("script,style,noscript,svg,nav,footer,header,form"):
        node.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


async def _web_games_for_date(date_value: str) -> list[dict[str, Any]]:
    """Get KHL fixtures exclusively from public web pages/search results.

    No sports APIs, API-SPORT, KHL Mobile API or SofaScore are used.
    Sports.ru is the primary browser-readable calendar because its HTML contains
    the date/time/home/away rows directly; Championat is the secondary source.
    """
    games: list[dict[str, Any]] = []
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=8.0),
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        },
    )
    direct_urls = [
        "https://www.sports.ru/hockey/tournament/khl/calendar/",
        "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/",
        "https://www.championat.com/hockey/_superleague.html",
    ]

    try:
        for url in direct_urls:
            try:
                text = await _fetch_text(client, url)
                parsed = _extract_fixtures(text, date_value)
                if parsed:
                    for game in parsed:
                        if not any(x["id"] == game["id"] for x in games):
                            games.append(game)
                    print(f"KHL browser calendar parsed: {url} -> {len(parsed)} games for {date_value}", flush=True)
                    # One complete calendar is enough; do not hammer sites.
                    break
                print(f"KHL browser calendar had no matching fixtures: {url}", flush=True)
            except Exception as exc:
                print(f"KHL browser calendar failed: {url}: {type(exc).__name__}: {exc}", flush=True)

        if not games:
            # Small sequential browser-search fallback. Search results themselves
            # are treated as web evidence; no sports API is queried.
            researcher = KHLWebResearcher()
            try:
                date_full = datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
                queries = [
                    f'site:sports.ru/hockey/tournament/khl/calendar "{date_full}" КХЛ',
                    f'site:championat.com/hockey "{date_full}" КХЛ расписание',
                    f'КХЛ "{date_full}" расписание Сибирь Автомобилист Авангард Металлург',
                ]
                for query in queries:
                    try:
                        results = await researcher.search(query)
                    except Exception as exc:
                        print(f"KHL schedule web search failed: {type(exc).__name__}: {exc}", flush=True)
                        continue
                    for result in results:
                        payload = " ".join((result.title, result.snippet, result.text))
                        for game in _extract_fixtures(payload, date_value):
                            if not any(x["id"] == game["id"] for x in games):
                                games.append(game)
                    if games:
                        break
            finally:
                try:
                    await researcher._client.aclose()
                except Exception:
                    pass
    finally:
        await client.aclose()

    games.sort(key=lambda x: x["datetime"])
    return games


async def verified_games_for_date(date_value: str) -> list[dict[str, Any]]:
    games = await _web_games_for_date(date_value)
    print(f"KHL schedule source: BROWSER WEB RESEARCH ({len(games)} games) date={date_value}", flush=True)
    return games


async def verified_today_games() -> list[dict[str, Any]]:
    return await verified_games_for_date(datetime.now(MSK).date().isoformat())


def upcoming_moscow_dates(days: int = 3) -> list[str]:
    today = datetime.now(MSK).date()
    return [(today + timedelta(days=i)).isoformat() for i in range(max(1, days))]
