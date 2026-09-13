from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

ALLOWED_COMPARISON = {"prognozai.ru"}
BOOKMAKERS = {
    "winline": "Winline",
    "винлайн": "Winline",
    "fonbet": "Fonbet",
    "фонбет": "Fonbet",
    "betboom": "BetBoom",
    "бетбум": "BetBoom",
    "parimatch": "Parimatch",
    "pari": "Parimatch",
}


def _host(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _odds(value: str) -> float | None:
    try:
        x = float(value.replace(",", "."))
        return x if 1.01 <= x <= 100 else None
    except (TypeError, ValueError):
        return None


def _parse_rows(text: str) -> list[tuple[str, tuple[float, float, float]]]:
    text = re.sub(r"\s+", " ", text.replace("ё", "е"))
    name = r"(?:Winline|Винлайн|Fonbet|Фонбет|BetBoom|БетБум|Parimatch|PARI|Pari)"
    num = r"([0-9]+[.,][0-9]+)"
    sep = r"(?:\s*[|;/]\s*|\s+)"
    pattern = rf"(?P<book>{name}){sep}{num}{sep}{num}{sep}{num}(?!\d)"
    result: list[tuple[str, tuple[float, float, float]]] = []
    for m in re.finditer(pattern, text, flags=re.IGNORECASE):
        key = m.group("book").lower().replace("ё", "е")
        book = BOOKMAKERS.get(key)
        values = tuple(_odds(m.group(i)) for i in (2, 3, 4))
        if book and all(v is not None for v in values):
            result.append((book, values))  # type: ignore[arg-type]
    return result


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find an explicitly labelled Winline/Fonbet/BetBoom/Parimatch row on public web.

    This is a web-search fallback, not a bookmaker API. It is deliberately strict:
    unlabeled aggregator numbers are ignored.
    """
    queries = [
        f'"{home}" "{away}" {date} Winline Fonbet BetBoom Parimatch',
        f'site:prognozai.ru "{home}" "{away}" {date}',
        f'site:prognozai.ru {home} {away} коэффициенты Winline Fonbet',
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
        for query in queries:
            for engine_url in (
                "https://www.google.com/search?q=" + quote_plus(query) + "&hl=ru&num=10",
                "https://www.bing.com/search?q=" + quote_plus(query) + "&setlang=ru",
            ):
                try:
                    response = await client.get(engine_url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    links = []
                    for a in soup.select("a[href]"):
                        href = str(a.get("href") or "")
                        if href.startswith("/url?q="):
                            href = href.split("/url?q=", 1)[1].split("&", 1)[0]
                        if href.startswith("http"):
                            links.append(href)
                    seen: set[str] = set()
                    for url in links:
                        if url in seen:
                            continue
                        seen.add(url)
                        host = _host(url)
                        if host not in ALLOWED_COMPARISON:
                            continue
                        try:
                            page = await client.get(url)
                            page.raise_for_status()
                            page_soup = BeautifulSoup(page.text, "html.parser")
                            text = page_soup.get_text(" ", strip=True)
                        except Exception:
                            continue
                        rows = _parse_rows(text)
                        if not rows:
                            continue
                        markets: dict[str, float] = {}
                        sources: dict[str, str] = {}
                        for bookmaker, values in rows:
                            for market, odd in zip(("П1", "X", "П2"), values):
                                if market not in markets or odd > markets[market]:
                                    markets[market] = odd
                                    sources[market] = bookmaker
                        if markets:
                            return markets, sources
                except Exception as exc:
                    print(f"KHL dedicated bookmaker web search failed: {type(exc).__name__}: {exc}", flush=True)
    return {}, {}
