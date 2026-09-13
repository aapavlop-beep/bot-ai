from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

ALLOWED_COMPARISON = {"prognozai.ru", "sportmail.ru", "vprognoze.kz", "vprognoze.ru"}
TARGET_BOOKMAKER_DOMAINS = {
    "winline.ru", "fon.bet", "fonbet.ru", "betboom.ru", "parimatch.com", "parimatch.ru",
}
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


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("ё", "е")).strip()


def _parse_rows(text: str) -> list[tuple[str, tuple[float, float, float]]]:
    text = _normalize(text)
    name = r"(?:Winline|Винлайн|Fonbet|Фонбет|BetBoom|БетБум|Parimatch|PARI|Pari)"
    num = r"([0-9]+[.,][0-9]+)"
    sep = r"(?:\s*[|;/]\s*|\s+)"
    patterns = [
        rf"(?P<book>{name}){sep}{num}{sep}{num}{sep}{num}(?!\d)",
        rf"{num}{sep}{num}{sep}{num}{sep}(?P<book>{name})(?!\w)",
    ]
    result: list[tuple[str, tuple[float, float, float]]] = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            key = m.group("book").lower().replace("ё", "е")
            book = BOOKMAKERS.get(key)
            if not book:
                continue
            nums = [g for g in m.groups() if g and g != m.group("book")]
            values = tuple(_odds(g) for g in nums[:3])
            if len(values) == 3 and all(v is not None for v in values):
                result.append((book, values))  # type: ignore[arg-type]
    return result


def _add_best(markets: dict[str, float], sources: dict[str, str], values: tuple[float, float, float], bookmaker: str) -> None:
    for market, odd in zip(("П1", "X", "П2"), values):
        if market not in markets or odd > markets[market]:
            markets[market] = odd
            sources[market] = bookmaker


def _slug(text: str) -> str:
    table = str.maketrans({
        "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ж":"zh","з":"z","и":"i","й":"y",
        "к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t","у":"u","ф":"f",
        "х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ы":"y","э":"e","ю":"yu","я":"ya",
        "ё":"e","ь":"","ъ":""
    })
    value = text.lower().translate(table)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


async def _fetch_page(client: httpx.AsyncClient, url: str, timeout: float = 7.0) -> str:
    response = await client.get(url, timeout=timeout)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return _normalize(soup.get_text(" ", strip=True))


async def _yandex_search(client: httpx.AsyncClient, query: str) -> tuple[str, list[str]]:
    """Run one Yandex web search and return visible result text + result URLs.

    We intentionally use one search request per match. The returned HTML/snippets
    are enough to extract bookmaker tables on pages such as PrognozAI, Sport Mail
    and Vprognoze without opening a slow/blocked page.
    """
    url = "https://yandex.ru/search/?text=" + quote_plus(query) + "&lr=1"
    response = await client.get(url, timeout=10.0)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = _normalize(soup.get_text(" ", strip=True))
    urls: list[str] = []
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "")
        host = _host(href)
        if host in ALLOWED_COMPARISON or host in TARGET_BOOKMAKER_DOMAINS:
            if href not in urls:
                urls.append(href)
    return text, urls[:6]


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find current public bookmaker odds through browser-style web research.

    Priority:
      1. Yandex web search — one request, parse its visible result/snippet text.
      2. Open only the few relevant result URLs returned by Yandex, with a short timeout.
      3. One DuckDuckGo fallback if Yandex gives no confirmed line.

    No bookmaker API, API-SPORT, SofaScore or Google is used here.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    markets: dict[str, float] = {}
    sources: dict[str, str] = {}
    date_iso = date[:10]

    query = (
        f'"{home}" "{away}" "{date_iso}" '
        f'коэффициенты Winline Fonbet BetBoom Parimatch'
    )

    async with httpx.AsyncClient(
        timeout=10,
        follow_redirects=True,
        headers=headers,
        limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
    ) as client:
        # 1) Yandex first. Parse the search result page itself so a slow
        #    comparison page cannot block the whole odds lookup.
        try:
            search_text, result_urls = await _yandex_search(client, query)
            parsed = _parse_rows(search_text)
            for bookmaker, values in parsed:
                _add_best(markets, sources, values, bookmaker)
            if all(k in markets for k in ("П1", "X", "П2")):
                print(
                    "KHL Yandex bookmaker odds confirmed: "
                    + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
                    flush=True,
                )
                return markets, sources
            print(
                f"KHL Yandex search: parsed={len(parsed)} bookmaker rows, result_pages={len(result_urls)}",
                flush=True,
            )
        except Exception as exc:
            print(f"KHL Yandex odds search failed: {type(exc).__name__}: {exc}", flush=True)
            result_urls = []

        # 2) Open only URLs returned by Yandex. This fixes the previous
        #    15-second timeout on a hard-coded PrognozAI page while retaining
        #    browser-only research.
        for url in result_urls:
            try:
                text = await _fetch_page(client, url, timeout=5.0)
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

        # 3) One and only one DDG fallback. No Google/Bing fan-out.
        fallback_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            response = await client.get(fallback_url, timeout=8.0)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            candidates: list[str] = []
            for block in soup.select(".result"):
                a = block.select_one(".result__a[href]")
                if a:
                    href = str(a.get("href") or "")
                    if href.startswith("http") and _host(href) in ALLOWED_COMPARISON | TARGET_BOOKMAKER_DOMAINS:
                        candidates.append(href)

            # Search result snippets can themselves contain the odds.
            ddg_text = _normalize(soup.get_text(" ", strip=True))
            for bookmaker, values in _parse_rows(ddg_text):
                _add_best(markets, sources, values, bookmaker)
            if all(k in markets for k in ("П1", "X", "П2")):
                print(
                    "KHL DDG bookmaker odds confirmed: "
                    + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
                    flush=True,
                )
                return markets, sources

            for url in candidates[:5]:
                try:
                    text = await _fetch_page(client, url, timeout=5.0)
                except Exception as exc:
                    print(f"KHL DDG result skipped: {_host(url)}: {type(exc).__name__}", flush=True)
                    continue
                for bookmaker, values in _parse_rows(text):
                    _add_best(markets, sources, values, bookmaker)
                if all(k in markets for k in ("П1", "X", "П2")):
                    print(
                        "KHL DDG result bookmaker odds confirmed: "
                        + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
                        flush=True,
                    )
                    return markets, sources
        except Exception as exc:
            print(f"KHL fallback web search failed: {type(exc).__name__}: {exc}", flush=True)

    print(
        "KHL bookmaker web research: no confirmed Winline/Fonbet/BetBoom/Parimatch line found",
        flush=True,
    )
    return markets, sources
