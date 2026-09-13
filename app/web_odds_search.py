from __future__ import annotations

import re
from urllib.parse import quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

# Public web sources only. No sports APIs are used here.
ALLOWED_COMPARISON = {
    "prognozai.ru", "sportmail.ru", "sport.mail.ru", "vprognoze.kz", "vprognoze.ru",
    "x2sport.ru", "sports.ru", "championat.com", "bookmaker-ratings.ru", "bettery.ru",
    "zaidet.online", "collabtok.online",
}
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


def _bookmaker_from_text(text: str) -> str | None:
    low = _normalize(text).lower()
    for key, value in BOOKMAKERS.items():
        if re.search(rf"(?<![a-zа-я]){re.escape(key)}(?![a-zа-я])", low, re.I):
            return value
    return None


def _bookmaker_from_host(url: str) -> str | None:
    host = _host(url)
    if host.endswith("winline.ru"):
        return "Winline"
    if host.endswith("fon.bet") or host.endswith("fonbet.ru"):
        return "Fonbet"
    if host.endswith("betboom.ru"):
        return "BetBoom"
    if host.endswith("parimatch.com") or host.endswith("parimatch.ru"):
        return "Parimatch"
    return None


def _triplets(text: str) -> list[tuple[float, float, float]]:
    """Extract plausible decimal 1X2 triplets from visible web text."""
    text = _normalize(text)
    number = r"(?:[1-9][0-9]?)[.,][0-9]{1,3}"
    values: list[float] = []
    for raw in re.findall(number, text):
        odd = _odds(raw)
        if odd is not None:
            values.append(odd)
    result: list[tuple[float, float, float]] = []
    for i in range(len(values) - 2):
        a, b, c = values[i:i + 3]
        if 1.10 <= a <= 20 and 1.10 <= b <= 20 and 1.10 <= c <= 20:
            result.append((a, b, c))
    return result


def _contains_team(text: str, team: str) -> bool:
    normalized = _normalize(text).lower()
    team = _normalize(team).lower()
    if not team:
        return True
    if team in normalized:
        return True
    # Search engines and bookmaker pages sometimes abbreviate Dynamo/Metallurg.
    aliases = {
        "динамо москва": ("динамо м", "динамо москов", "dynamo moscow"),
        "динамо минск": ("динамо мн", "dynamo minsk"),
        "металлург мг": ("металлург магнитогорск", "metallurg magnitogorsk", "metallurg mg"),
        "цска": ("цска москва", "cska"),
        "хк сочи": ("сочи", "sochi"),
        "куньлунь ред стар": ("куньлунь", "kunlun"),
        "шанхайские драконы": ("шанхай дрэгонс", "шанхай", "shanghai dragons"),
    }
    return any(alias in normalized for alias in aliases.get(team, ()))


def _parse_rows(text: str, home: str = "", away: str = "", bookmaker_hint: str | None = None) -> list[tuple[str, tuple[float, float, float]]]:
    """Find bookmaker-labelled 1X2 lines near the requested match."""
    text = _normalize(text)
    if not text:
        return []

    bookmaker = bookmaker_hint or _bookmaker_from_text(text)
    if not bookmaker:
        return []

    # First try a compact evidence window around the bookmaker name.
    book_pattern = r"Winline|Винлайн|Fonbet|Фонбет|BetBoom|БетБум|Parimatch|PARI|Pari"
    matches = list(re.finditer(book_pattern, text, flags=re.IGNORECASE))
    windows: list[str] = []
    if matches:
        for match in matches:
            left = max(0, match.start() - 700)
            right = min(len(text), match.end() + 1200)
            windows.append(text[left:right])
    else:
        windows.append(text)

    for window in windows:
        if home and not _contains_team(window, home):
            continue
        if away and not _contains_team(window, away):
            continue
        trips = _triplets(window)
        if trips:
            return [(bookmaker, trips[0])]
    return []


def _parse_source_without_bookmaker(text: str, home: str, away: str, bookmaker: str | None = None) -> list[tuple[str, tuple[float, float, float]]]:
    """Parse an aggregator only when the requested bookmaker is explicit."""
    bookmaker = bookmaker or _bookmaker_from_text(text)
    if not bookmaker:
        return []
    if home and not _contains_team(text, home):
        return []
    if away and not _contains_team(text, away):
        return []
    trips = _triplets(text)
    return [(bookmaker, trips[0])] if trips else []


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


async def _bing_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    url = "https://www.bing.com/search?q=" + quote_plus(query) + "&setlang=ru&count=10"
    response = await client.get(url, timeout=12.0)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    chunks: list[str] = []
    urls: list[str] = []
    for block in soup.select("li.b_algo"):
        link = block.select_one("h2 a[href]")
        if not link:
            continue
        href = _decode_url(str(link.get("href") or ""))
        if not href.startswith("http"):
            continue
        title = link.get_text(" ", strip=True)
        snippet = block.select_one(".b_caption p")
        snippet_text = snippet.get_text(" ", strip=True) if snippet else ""
        chunks.append(f"{title} {snippet_text}")
        if href not in urls:
            urls.append(href)
    return _normalize(" ".join(chunks)), urls[:12]


async def _duckduckgo_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    response = await client.get(url, timeout=12.0)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    chunks: list[str] = []
    urls: list[str] = []
    for block in soup.select(".result"):
        link = block.select_one(".result__a[href]")
        if not link:
            continue
        href = _decode_url(str(link.get("href") or ""))
        if not href.startswith("http"):
            continue
        snippet = block.select_one(".result__snippet")
        chunks.append(f"{link.get_text(' ', strip=True)} {snippet.get_text(' ', strip=True) if snippet else ''}")
        if href not in urls:
            urls.append(href)
    return _normalize(" ".join(chunks)), urls[:12]


async def _google_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    url = "https://www.google.com/search?q=" + quote_plus(query) + "&hl=ru&num=10"
    response = await client.get(url, timeout=12.0)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    chunks: list[str] = []
    urls: list[str] = []
    for block in soup.select("div.MjjYud"):
        link = block.select_one("a[href]")
        title_node = block.select_one("h3")
        if not link or not title_node:
            continue
        href = _decode_url(str(link.get("href") or ""))
        if not href.startswith("http"):
            continue
        snippet_node = block.select_one(".VwiC3b") or block.select_one("div[data-sncf]")
        chunks.append(f"{title_node.get_text(' ', strip=True)} {snippet_node.get_text(' ', strip=True) if snippet_node else ''}")
        if href not in urls:
            urls.append(href)
    return _normalize(" ".join(chunks)), urls[:12]


async def _yandex_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    """Last-resort search engine. Yandex markup changes frequently, so it is not primary."""
    endpoints = (
        "https://ya.ru/search/?text=" + quote_plus(query),
        "https://yandex.ru/search/?text=" + quote_plus(query) + "&lr=1",
    )
    last_exc: Exception | None = None
    for url in endpoints:
        try:
            response = await client.get(url, timeout=12.0)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            chunks: list[str] = []
            urls: list[str] = []
            for block in soup.select("li.serp-item, div.organic, .Organic"):
                link = block.select_one("a[href]")
                if not link:
                    continue
                href = _decode_url(str(link.get("href") or ""))
                title = link.get_text(" ", strip=True)
                block_text = block.get_text(" ", strip=True)
                if href.startswith("http"):
                    chunks.append(block_text)
                    if href not in urls:
                        urls.append(href)
            if chunks:
                return _normalize(" ".join(chunks)), urls[:12]
            # Even if Yandex changed its result cards, return visible text so
            # bookmaker names/odds can still be parsed from snippets.
            text = _normalize(soup.get_text(" ", strip=True))
            if text:
                return text, urls[:12]
        except Exception as exc:
            last_exc = exc
            print(f"KHL Yandex endpoint failed: {type(exc).__name__}: {exc}", flush=True)
    if last_exc:
        raise last_exc
    return "", []


async def _search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str], str]:
    """Search the public web without any sports API."""
    for name, engine in (("Bing", _bing_search), ("DuckDuckGo", _duckduckgo_search), ("Yandex", _yandex_search), ("Google", _google_search)):
        try:
            text, urls = await engine(client, query)
            if text or urls:
                return text, urls, name
        except Exception as exc:
            print(f"KHL {name} web search failed: {type(exc).__name__}: {exc}", flush=True)
    return "", [], "none"


async def _fetch_page(client: httpx.AsyncClient, url: str, timeout: float = 8.0) -> str:
    response = await client.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup.select("script,style,noscript,svg,nav,footer,header,form"):
        node.decompose()
    return _normalize(soup.get_text(" ", strip=True))


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find current 1X2 odds using public browser/search research only.

    The function deliberately does not call API-SPORT, SofaScore, KHL Mobile API,
    The Odds API, or any other sports data API. It uses ordinary public search
    result pages and opens only the pages returned by those searches.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    markets: dict[str, float] = {}
    sources: dict[str, str] = {}
    date_iso = date[:10]
    date_ru = f"{date_iso[8:10]}.{date_iso[5:7]}.{date_iso[:4]}" if len(date_iso) == 10 else date_iso

    # Separate queries are important: one large query often hides bookmaker
    # snippets behind generic prediction pages.
    queries: list[tuple[str, str]] = []
    for bookmaker in ("Winline", "Fonbet", "BetBoom", "Parimatch"):
        queries.extend([
            (bookmaker, f'"{home}" "{away}" {date_ru} {bookmaker} коэффициенты 1X2'),
            (bookmaker, f'"{home}" "{away}" {date_ru} {bookmaker} линия'),
            (bookmaker, f'"{home}" "{away}" {date_ru} {bookmaker} П1 X П2'),
        ])
    queries.extend([
        ("any", f'"{home}" "{away}" {date_ru} коэффициенты Winline Fonbet BetBoom Parimatch'),
        ("any", f'"{home}" "{away}" {date_ru} линия букмекер'),
    ])

    async with httpx.AsyncClient(
        timeout=14,
        follow_redirects=True,
        headers=headers,
        limits=httpx.Limits(max_connections=3, max_keepalive_connections=1),
    ) as client:
        seen_urls: list[str] = []
        seen_queries: set[str] = set()

        for bookmaker_hint, query in queries:
            if query in seen_queries:
                continue
            seen_queries.add(query)
            try:
                search_text, result_urls, engine = await _search(client, query)
            except Exception as exc:
                print(f"KHL bookmaker web search failed: {type(exc).__name__}: {exc}", flush=True)
                continue

            parsed: list[tuple[str, tuple[float, float, float]]] = []
            # Search result snippets are valid evidence only when the requested
            # teams and bookmaker are present in the same visible text.
            if bookmaker_hint != "any":
                parsed = _parse_rows(search_text, home, away, bookmaker_hint)
            else:
                parsed = _parse_rows(search_text, home, away)

            print(
                f"KHL bookmaker search: engine={engine} bookmaker={bookmaker_hint} "
                f"rows={len(parsed)} urls={len(result_urls)}",
                flush=True,
            )
            for bookmaker, values in parsed:
                _add_best(markets, sources, values, bookmaker)

            for url in result_urls:
                if url not in seen_urls:
                    seen_urls.append(url)

            # Once all three basic 1X2 prices are confirmed, stop searching.
            if all(k in markets for k in ("П1", "X", "П2")):
                break

        # Open a limited number of search-selected pages. This is the important
        # second stage: many bookmaker pages do not expose odds in search snippets.
        for url in seen_urls[:16]:
            if all(k in markets for k in ("П1", "X", "П2")):
                break
            try:
                text = await _fetch_page(client, url)
                host = _host(url)
                bookmaker = _bookmaker_from_host(url) or _bookmaker_from_text(text)
                if bookmaker:
                    parsed = _parse_rows(text, home, away, bookmaker)
                    if not parsed and host in ALLOWED_COMPARISON:
                        parsed = _parse_source_without_bookmaker(text, home, away, bookmaker)
                    for bookmaker_name, values in parsed:
                        _add_best(markets, sources, values, bookmaker_name)
                    if parsed:
                        print(f"KHL bookmaker page parsed: {host} rows={len(parsed)}", flush=True)
            except Exception as exc:
                print(f"KHL bookmaker page skipped: {_host(url)}: {type(exc).__name__}: {exc}", flush=True)

    if all(k in markets for k in ("П1", "X", "П2")):
        print(
            "KHL bookmaker odds confirmed by web research: "
            + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
            flush=True,
        )
        return markets, sources

    print(
        "KHL bookmaker web research: no confirmed Winline/Fonbet/BetBoom/Parimatch line found "
        f"for {home} — {away} {date_iso}",
        flush=True,
    )
    return markets, sources
