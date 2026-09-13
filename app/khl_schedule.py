from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
    "динамо м": "Динамо Москва", "динамо москва": "Динамо Москва",
    "динамо мн": "Динамо Минск", "динамо минск": "Динамо Минск",
    "металлург мг": "Металлург Мг", "металлург магнитогорск": "Металлург Мг",
    "хк сочи": "ХК Сочи", "сочи": "ХК Сочи", "цска москва": "ЦСКА", "цска": "ЦСКА",
    "шд": "Шанхайские Драконы", "шанхай дрэгонс": "Шанхайские Драконы",
    "куньлунь ред стар": "Куньлунь Ред Стар", "куньлунь": "Куньлунь",
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
    """Extract fixtures whose date is exactly requested_date from browser text."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    date_full = datetime.fromisoformat(requested_date).strftime("%d.%m.%Y")
    day = datetime.fromisoformat(requested_date).strftime("%-d") if False else str(datetime.fromisoformat(requested_date).day)
    month_names = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
        7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }
    long_date = f"{day} {month_names[datetime.fromisoformat(requested_date).month]} {datetime.fromisoformat(requested_date).year}"
    team = _team_pattern()
    patterns = [
        re.compile(rf"{re.escape(date_full)}\s+(\d{{1,2}}:\d{{2}})(?:\s+{re.escape(date_full)}\s+\d{{1,2}}:\d{{2}})?\s+({team})(?:\s*[-–—]\s*){{1,5}}({team})", re.I),
        re.compile(rf"{re.escape(long_date)}\s+(\d{{1,2}}:\d{{2}})\s+({team})(?:\s*[-–—]\s*){{1,5}}({team})", re.I),
        re.compile(rf"{re.escape(date_full)}\s+({team})\s+(?:[-–—]|vs|против)\s+({team})\s+(\d{{1,2}}:\d{{2}})", re.I),
    ]
    found: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            groups = match.groups()
            if len(groups) != 3:
                continue
            if re.fullmatch(r"\d{1,2}:\d{2}", groups[0]):
                time_value, home, away = groups
            else:
                home, away, time_value = groups
            game = _build_game(requested_date, time_value, home, away)
            if game and not any(x["id"] == game["id"] for x in found):
                found.append(game)
    return found


async def _web_games_for_date(date_value: str) -> list[dict[str, Any]]:
    """Get KHL fixtures only through a real Chromium browser."""
    researcher = KHLWebResearcher()
    games: list[dict[str, Any]] = []
    date_full = datetime.fromisoformat(date_value).strftime("%d.%m.%Y")
    try:
        direct_urls = [
            "https://www.sports.ru/hockey/tournament/khl/calendar/",
            "https://www.championat.com/hockey/_superleague/tournament/7092/calendar/",
        ]
        for url in direct_urls:
            try:
                result = await researcher.fetch_page(url)
                payload = " ".join((result.title, result.snippet, result.text))
                parsed = _extract_fixtures(payload, date_value)
                if parsed:
                    for game in parsed:
                        if not any(x["id"] == game["id"] for x in games):
                            games.append(game)
                    print(f"KHL browser calendar parsed: {url} -> {len(parsed)} games for {date_value}", flush=True)
                    break
                print(f"KHL browser calendar had no matching fixtures: {url}", flush=True)
            except Exception as exc:
                print(f"KHL browser calendar failed: {url}: {type(exc).__name__}: {exc}", flush=True)

        if not games:
            queries = [
                f'site:sports.ru/hockey/tournament/khl/calendar "{date_full}" КХЛ',
                f'site:championat.com/hockey "{date_full}" КХЛ расписание',
                f'КХЛ "{date_full}" расписание матчи',
            ]
            for query in queries:
                try:
                    results = await researcher.search(query)
                except Exception as exc:
                    print(f"KHL schedule browser search failed: {type(exc).__name__}: {exc}", flush=True)
                    continue
                for result in results:
                    payload = " ".join((result.title, result.snippet, result.text))
                    parsed = _extract_fixtures(payload, date_value)
                    for game in parsed:
                        if not any(x["id"] == game["id"] for x in games):
                            games.append(game)
                if games:
                    break
    finally:
        await researcher.close()

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
