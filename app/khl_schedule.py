from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .team_names import display_team_name
from .web_research import KHLWebResearcher, SearchResult

MSK = ZoneInfo("Europe/Moscow")

# Names used to recognize KHL fixtures in search-result/page text. The list is
# deliberately broad so the parser survives transliteration and short names.
KHL_TEAMS = {
    "Авангард", "Автомобилист", "Адмирал", "Ак Барс", "Амур", "Барыс",
    "Витязь", "Динамо Москва", "Динамо М", "Динамо Минск", "Динамо Мн",
    "Динамо СПб", "Лада", "Локомотив", "Металлург Мг", "Металлург Магнитогорск",
    "Нефтехимик", "СКА", "Салават Юлаев", "Северсталь", "Сибирь", "Спартак",
    "Торпедо", "Трактор", "Шанхайские Драконы", "Шанхайские Драконы",
    "ХК Сочи", "Сочи", "ЦСКА", "Куньлунь Ред Стар", "Куньлунь",
}


def _canonical_team(value: str) -> str:
    value = re.sub(r"[«»\"()]", "", value or "")
    value = re.sub(r"\s+", " ", value).strip()
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
    # Positive, stable Telegram callback id. It is not an external provider id.
    return 1_000_000_000 + (zlib.crc32(raw) & 0x3FFFFFFF)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_fixtures(text: str, date_value: str) -> list[dict[str, Any]]:
    """Extract KHL fixtures from a browser-retrieved schedule/article page."""
    text = text.replace("–", "—").replace("−", "—")
    date_short = datetime.fromisoformat(date_value).strftime("%d.%m")
    # Examples: 13.09.2026 13:30 Сибирь — Автомобилист
    patterns = [
        re.compile(rf"(?:{re.escape(date_short)}\.?\s*{datetime.fromisoformat(date_value).year}\s*)?(\d{{1,2}}:\d{{2}})\s+([^—|•\n]{{2,45}}?)\s+—\s+([^|•\n]{{2,45}}?)(?=\s+(?:КХЛ|регуляр|Фонбет|$))", re.I),
        re.compile(rf"{re.escape(date_value)}\s+(\d{{1,2}}:\d{{2}})\s+([^—|•\n]{{2,45}}?)\s+—\s+([^|•\n]{{2,45}}?)", re.I),
        re.compile(rf"{re.escape(date_short)}\s+(\d{{1,2}}:\d{{2}})\s+([^—|•\n]{{2,45}}?)\s+—\s+([^|•\n]{{2,45}}?)", re.I),
    ]
    found: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            time_value, raw_home, raw_away = [m.strip() for m in match.groups()]
            home = _canonical_team(raw_home)
            away = _canonical_team(raw_away)
            # A fixture is accepted only if both sides look like KHL teams.
            home_ok = any(home.lower() == t.lower() or home.lower() in t.lower() or t.lower() in home.lower() for t in KHL_TEAMS)
            away_ok = any(away.lower() == t.lower() or away.lower() in t.lower() or t.lower() in away.lower() for t in KHL_TEAMS)
            if not home_ok or not away_ok or home == away:
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
    queries = [
        f'КХЛ {date_value} расписание матчи',
        f'КХЛ {date_value} календарь игр',
        f'КХЛ {date_value} результаты расписание кто играет',
        f'"КХЛ" "{datetime.fromisoformat(date_value).strftime("%d.%m.%Y")}" матчи',
        f'site:championat.com/hockey/_superleague {date_value} КХЛ расписание',
        f'site:khl.ru {date_value} КХЛ матчи',
    ]
    unique: dict[str, SearchResult] = {}
    for query in queries:
        for result in await researcher.search(query):
            if result.url not in unique:
                unique[result.url] = result

    # Fetch the most relevant schedule pages first. The page extractor is part
    # of the same browser-style research layer; no sports API is used.
    results = list(unique.values())
    results.sort(key=lambda r: (
        1 if "championat.com" in r.url else 0,
        1 if "khl.ru" in r.url else 0,
        1 if "x2sport.ru" in r.url else 0,
    ), reverse=True)
    games: list[dict[str, Any]] = []
    for result in results[:12]:
        page = await researcher._extract_page(result)
        text = _normalize_text(page.text or page.snippet)
        if not text:
            continue
        for game in _extract_fixtures(text, date_value):
            if not any(x["id"] == game["id"] for x in games):
                games.append(game)
        if len(games) >= 2:
            # Continue through more pages: a full KHL day can have 4+ games.
            pass
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
