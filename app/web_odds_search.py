from __future__ import annotations

import re
from urllib.parse import urlparse

from .web_research import KHLWebResearcher


BOOKMAKER_DOMAINS = {
    "Winline": ("winline.ru",),
    "Fonbet": ("fon.bet", "fonbet.ru"),
    "BetBoom": ("betboom.ru",),
    "Parimatch": ("parimatch.com", "parimatch.ru"),
}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е")).strip()


def _odds(value: str) -> float | None:
    try:
        odd = float(value.replace(",", "."))
        return odd if 1.01 <= odd <= 100 else None
    except (TypeError, ValueError):
        return None


def _triplets(text: str) -> list[tuple[float, float, float]]:
    text = _normalize(text)
    values: list[float] = []
    for raw in re.findall(r"(?<!\d)(?:1[.,]\d{1,3}|[2-9][.,]\d{1,3}|1\d[.,]\d{1,3}|20[.,]\d{1,3})(?!\d)", text):
        odd = _odds(raw)
        if odd is not None:
            values.append(odd)
    return [values[i:i + 3] for i in range(len(values) - 2) if all(1.10 <= x <= 20 for x in values[i:i + 3])]


def _contains_team(text: str, team: str) -> bool:
    text = _normalize(text).lower()
    team = _normalize(team).lower()
    if not team:
        return True
    if team in text:
        return True
    aliases = {
        "динамо москва": ("динамо м", "dynamo moscow"),
        "динамо минск": ("динамо мн", "dynamo minsk"),
        "металлург мг": ("металлург магнитогорск", "metallurg magnitogorsk"),
        "цска": ("цска москва", "cska"),
        "хк сочи": ("сочи", "sochi"),
        "куньлунь ред стар": ("куньлунь", "kunlun"),
        "шанхайские драконы": ("шанхай дрэгонс", "шанхай", "shanghai dragons"),
    }
    return any(alias in text for alias in aliases.get(team, ()))


def _bookmaker_from_host(url: str) -> str | None:
    host = _host(url)
    for bookmaker, domains in BOOKMAKER_DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return bookmaker
    return None


def _parse_evidence(text: str, home: str, away: str, bookmaker: str) -> tuple[float, float, float] | None:
    text = _normalize(text)
    if not _contains_team(text, home) or not _contains_team(text, away):
        return None
    marker = re.search(r"winline|винлайн|fonbet|фонбет|fon\.bet|betboom|бетбум|parimatch|пари матч", text, re.I)
    windows = [text]
    if marker:
        windows.insert(0, text[max(0, marker.start() - 1200): min(len(text), marker.end() + 2500)])
    for window in windows:
        trips = _triplets(window)
        if trips:
            return tuple(trips[0])
    return None


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find 1X2 odds using real Chromium browser search only.

    A line is accepted only when the opened page itself belongs to Winline,
    Fonbet, BetBoom or Parimatch. Aggregators are never treated as bookmaker
    confirmation.
    """
    researcher = KHLWebResearcher()
    date_ru = f"{date[8:10]}.{date[5:7]}.{date[:4]}" if len(date) >= 10 else date
    queries = [
        f'"{home}" "{away}" {date_ru} Winline коэффициенты 1X2',
        f'"{home}" "{away}" {date_ru} Fonbet коэффициенты 1X2',
        f'"{home}" "{away}" {date_ru} BetBoom коэффициенты 1X2',
        f'"{home}" "{away}" {date_ru} Parimatch коэффициенты 1X2',
        f'"{home}" "{away}" {date_ru} Winline Fonbet BetBoom Parimatch линия',
    ]

    markets: dict[str, float] = {}
    sources: dict[str, str] = {}
    try:
        for query in queries:
            results = await researcher.search(query)
            for result in results:
                bookmaker = _bookmaker_from_host(result.url)
                if not bookmaker:
                    continue
                evidence = _parse_evidence(result.title + " " + result.snippet, home, away, bookmaker)
                if evidence is None:
                    page = await researcher.fetch_page(result.url)
                    evidence = _parse_evidence(page.title + " " + page.text, home, away, bookmaker)
                if evidence is None:
                    continue
                for name, odd in zip(("П1", "X", "П2"), evidence):
                    if name not in markets or odd > markets[name]:
                        markets[name] = odd
                        sources[name] = bookmaker
                if len(markets) == 3:
                    return markets, sources
    finally:
        await researcher.close()
    return markets, sources
