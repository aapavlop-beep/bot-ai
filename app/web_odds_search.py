from __future__ import annotations

import re
from urllib.parse import quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

ALLOWED_COMPARISON = {"prognozai.ru", "sportmail.ru", "vprognoze.kz", "vprognoze.ru", "sport.mail.ru"}
TARGET_BOOKMAKER_DOMAINS = {
    "winline.ru", "fon.bet", "fonbet.ru", "betboom.ru", "parimatch.com", "parimatch.ru",
}
BOOKMAKERS = {
    "winline": "Winline", "винлайн": "Winline",
    "fonbet": "Fonbet", "фонбет": "Fonbet",
    "betboom": "BetBoom", "бетбум": "BetBoom",
    "parimatch": "Parimatch", "pari": "Parimatch",
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


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е")).strip()


def _parse_rows(text: str) -> list[tuple[str, tuple[float, float, float]]]:
    """Parse bookmaker + nearby 1X2 numbers from search snippets/pages.

    Search engines often put the bookmaker name and odds in different text
    fragments. Do not require an exact table layout; inspect a small window
    around every bookmaker mention instead.
    """
    text = _normalize(text)
    result: list[tuple[str, tuple[float, float, float]]] = []
    book_pattern = r"Winline|Винлайн|Fonbet|Фонбет|BetBoom|БетБум|Parimatch|PARI|Pari"
    num_pattern = r"[0-9]{1,3}[.,][0-9]{1,3}"

    for match in re.finditer(book_pattern, text, flags=re.IGNORECASE):
        raw = match.group(0).lower().replace("ё", "е")
        bookmaker = BOOKMAKERS.get(raw)
        if not bookmaker:
            continue
        left = max(0, match.start() - 100)
        right = min(len(text), match.end() + 220)
        window = text[left:right]
        values: list[float] = []
        for value in re.findall(num_pattern, window):
            odd = _odds(value)
            if odd is not None:
                values.append(odd)
        # Prefer the first three plausible 1X2 prices. Ignore years/dates
        # because they normally contain no decimal point in this format.
        if len(values) >= 3:
            triplet = tuple(values[:3])
            result.append((bookmaker, triplet))
    return result


def _add_best(markets: dict[str, float], sources: dict[str, str], values: tuple[float, float, float], bookmaker: str) -> None:
    for market, odd in zip(("П1", "X", "П2"), values):
        if market not in markets or odd > markets[market]:
            markets[market] = odd
            sources[market] = bookmaker


def _decode_url(href: str) -> str:
    href = unquote(href)
    if "url=" in href and "yandex" in _host(href):
        try:
            href = href.split("url=", 1)[1].split("&", 1)[0]
        except Exception:
            pass
    return href


async def _yandex_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    """Search Yandex and return visible snippets plus relevant result URLs.

    ya.ru is used first because it is the lightweight Yandex search frontend;
    yandex.ru is the fallback. No Google/Bing request is made.
    """
    endpoints = (
        "https://ya.ru/search/?text=" + quote_plus(query),
        "https://yandex.ru/search/?text=" + quote_plus(query) + "&lr=1",
    )
    last_exc: Exception | None = None
    for url in endpoints:
        try:
            response = await client.get(url, timeout=8.0)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            text = _normalize(soup.get_text(" ", strip=True))
            urls: list[str] = []
            for a in soup.select("a[href]"):
                href = _decode_url(str(a.get("href") or ""))
                if not href.startswith("http"):
                    continue
                host = _host(href)
                if host in ALLOWED_COMPARISON or host in TARGET_BOOKMAKER_DOMAINS:
                    if href not in urls:
                        urls.append(href)
            if text:
                return text, urls[:8]
        except Exception as exc:
            last_exc = exc
            print(f"KHL Yandex endpoint failed: {type(exc).__name__}: {exc}", flush=True)
    if last_exc:
        raise last_exc
    return "", []


async def _fetch_page(client: httpx.AsyncClient, url: str, timeout: float = 5.0) -> str:
    response = await client.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return _normalize(soup.get_text(" ", strip=True))


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find bookmaker odds using Yandex browser-style web search only.

    API-SPORT, SofaScore and Google are deliberately absent. Search-engine
    result text is parsed before any page is opened, which avoids the previous
    timeout on comparison pages.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    markets: dict[str, float] = {}
    sources: dict[str, str] = {}
    date_iso = date[:10]

    # One compact query per match. The query asks for all four requested
    # bookmakers so Yandex can return an aggregator snippet containing the table.
    query = f'"{home}" "{away}" {date_iso} коэффициенты Winline Fonbet BetBoom Parimatch'

    async with httpx.AsyncClient(
        timeout=9,
        follow_redirects=True,
        headers=headers,
        limits=httpx.Limits(max_connections=3, max_keepalive_connections=1),
    ) as client:
        try:
            search_text, result_urls = await _yandex_search(client, query)
            parsed = _parse_rows(search_text)
            for bookmaker, values in parsed:
                _add_best(markets, sources, values, bookmaker)
            print(
                f"KHL Yandex odds search: rows={len(parsed)} result_pages={len(result_urls)}",
                flush=True,
            )
            if all(k in markets for k in ("П1", "X", "П2")):
                print(
                    "KHL Yandex bookmaker odds confirmed: "
                    + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
                    flush=True,
                )
                return markets, sources
        except Exception as exc:
            print(f"KHL Yandex odds search failed: {type(exc).__name__}: {exc}", flush=True)
            result_urls = []

        # Only open Yandex-selected comparison/bookmaker pages if snippets did
        # not contain a complete line. Limit to three pages and stop immediately
        # after a confirmed 1X2 line.
        for url in result_urls[:3]:
            try:
                text = await _fetch_page(client, url)
                for bookmaker, values in _parse_rows(text):
                    _add_best(markets, sources, values, bookmaker)
                if all(k in markets for k in ("П1", "X", "П2")):
                    print(
                        "KHL Yandex result bookmaker odds confirmed: "
                        + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
                        flush=True,
                    )
                    return markets, sources
            except Exception as exc:
                print(f"KHL Yandex result skipped: {_host(url)}: {type(exc).__name__}", flush=True)

    print("KHL bookmaker web research: no confirmed Winline/Fonbet/BetBoom/Parimatch line found", flush=True)
    return markets, sources
