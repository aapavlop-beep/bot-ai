from __future__ import annotations

import re
from urllib.parse import quote_plus, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

# Only public browser-readable sources. No sports APIs are used here.
ALLOWED_COMPARISON = {
    "prognozai.ru", "sportmail.ru", "sport.mail.ru", "vprognoze.kz", "vprognoze.ru",
    "x2sport.ru", "sports.ru", "championat.com",
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


def _triplets(text: str) -> list[tuple[float, float, float]]:
    """Extract plausible 1X2 decimal triplets without treating dates as odds."""
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
        # Hockey 1X2 odds normally have all three prices in a compact range.
        if 1.10 <= a <= 20 and 1.10 <= b <= 20 and 1.10 <= c <= 20:
            result.append((a, b, c))
    return result


def _parse_rows(text: str, home: str = "", away: str = "") -> list[tuple[str, tuple[float, float, float]]]:
    """Find bookmaker-labelled 1X2 lines near the requested match."""
    text = _normalize(text)
    if not text:
        return []
    result: list[tuple[str, tuple[float, float, float]]] = []
    book_pattern = r"Winline|Винлайн|Fonbet|Фонбет|BetBoom|БетБум|Parimatch|PARI|Pari"
    match_tokens = [x for x in (_normalize(home), _normalize(away)) if x]

    for match in re.finditer(book_pattern, text, flags=re.IGNORECASE):
        bookmaker = _bookmaker_from_text(match.group(0))
        if not bookmaker:
            continue
        left = max(0, match.start() - 500)
        right = min(len(text), match.end() + 900)
        window = text[left:right]
        # Prefer windows containing both teams; otherwise use the local bookmaker window.
        if match_tokens and not all(token.lower() in window.lower() for token in match_tokens):
            continue
        for triplet in _triplets(window):
            result.append((bookmaker, triplet))
            break
    return result


def _parse_source_without_bookmaker(text: str, home: str, away: str) -> list[tuple[str, tuple[float, float, float]]]:
    """Use a comparison page only when its text explicitly contains a target bookmaker."""
    bookmaker = _bookmaker_from_text(text)
    if not bookmaker:
        return []
    normalized = _normalize(text)
    low = normalized.lower()
    if home and home.lower() not in low:
        return []
    if away and away.lower() not in low:
        return []
    trips = _triplets(normalized)
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


async def _yandex_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    """Return Yandex visible result text and relevant result URLs."""
    endpoints = (
        "https://ya.ru/search/?text=" + quote_plus(query),
        "https://yandex.ru/search/?text=" + quote_plus(query) + "&lr=1",
    )
    last_exc: Exception | None = None
    for url in endpoints:
        try:
            response = await client.get(url, timeout=10.0)
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
                return text, urls[:10]
        except Exception as exc:
            last_exc = exc
            print(f"KHL Yandex endpoint failed: {type(exc).__name__}: {exc}", flush=True)
    if last_exc:
        raise last_exc
    return "", []


async def _fetch_page(client: httpx.AsyncClient, url: str, timeout: float = 7.0) -> str:
    response = await client.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for node in soup.select("script,style,noscript,svg,nav,footer,header,form"):
        node.decompose()
    return _normalize(soup.get_text(" ", strip=True))


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find current 1X2 odds using Yandex web research only.

    Search is intentionally conservative: a line is accepted only when a target
    bookmaker name is present next to a plausible 1X2 triplet and the requested
    teams are present in the same evidence window/page.
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

    # Multiple short Yandex searches are more reliable than one huge query.
    queries = [
        f'"{home}" "{away}" {date_ru} коэффициенты Winline Fonbet BetBoom Parimatch',
        f'"{home}" "{away}" {date_ru} линия Winline Fonbet BetBoom Parimatch',
        f'"{home}" "{away}" {date_ru} 1X2 Winline Fonbet BetBoom Parimatch',
        f'"{home}" "{away}" {date_ru} коэффициенты букмекер',
    ]

    async with httpx.AsyncClient(
        timeout=12,
        follow_redirects=True,
        headers=headers,
        limits=httpx.Limits(max_connections=3, max_keepalive_connections=1),
    ) as client:
        seen_urls: list[str] = []
        for query in queries:
            try:
                search_text, result_urls = await _yandex_search(client, query)
            except Exception as exc:
                print(f"KHL Yandex odds search failed: {type(exc).__name__}: {exc}", flush=True)
                continue

            parsed = _parse_rows(search_text, home, away)
            print(f"KHL Yandex odds query: bookmaker_rows={len(parsed)} urls={len(result_urls)}", flush=True)
            for bookmaker, values in parsed:
                _add_best(markets, sources, values, bookmaker)
            for url in result_urls:
                if url not in seen_urls:
                    seen_urls.append(url)
            if all(k in markets for k in ("П1", "X", "П2")):
                break

        # If snippets do not contain the complete line, open only pages selected by Yandex.
        for url in seen_urls[:8]:
            if all(k in markets for k in ("П1", "X", "П2")):
                break
            try:
                text = await _fetch_page(client, url)
                parsed = _parse_rows(text, home, away)
                if not parsed and _host(url) in ALLOWED_COMPARISON:
                    parsed = _parse_source_without_bookmaker(text, home, away)
                for bookmaker, values in parsed:
                    _add_best(markets, sources, values, bookmaker)
                if parsed:
                    print(f"KHL Yandex source parsed: {_host(url)} rows={len(parsed)}", flush=True)
            except Exception as exc:
                print(f"KHL Yandex source skipped: {_host(url)}: {type(exc).__name__}", flush=True)

    if all(k in markets for k in ("П1", "X", "П2")):
        print(
            "KHL Yandex bookmaker odds confirmed: "
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
