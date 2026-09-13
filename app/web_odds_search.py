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


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    return _normalize(soup.get_text(" ", strip=True))


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find public bookmaker odds without hammering search engines.

    Important: this is deliberately browser/web research only. There are no
    bookmaker APIs. We first open known public comparison pages directly and
    only use ONE lightweight search request as a fallback. Google is excluded
    completely because Railway repeatedly receives HTTP 429 from it.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    markets: dict[str, float] = {}
    sources: dict[str, str] = {}

    home_slug = _slug(home)
    away_slug = _slug(away)
    date_iso = date[:10]

    # Deterministic public pages. For the KHL these pages expose bookmaker
    # rows such as "Pari | 2.35 | 4.20 | 2.60" and are much more reliable
    # than repeatedly querying Google/Bing/DDG.
    direct_urls = [
        f"https://prognozai.ru/matches/hockey/russia-fonbet-khl/{home_slug}-vs-{away_slug}-{date_iso}/",
        f"https://prognozai.ru/matches/hockey/russia-fonbet-khl/{home_slug}-{away_slug}-{date_iso}/",
    ]

    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=headers) as client:
        for url in direct_urls:
            try:
                text = await _fetch_page(client, url)
                for bookmaker, values in _parse_rows(text):
                    _add_best(markets, sources, values, bookmaker)
                if all(k in markets for k in ("П1", "X", "П2")):
                    print(
                        "KHL browser bookmaker odds confirmed: "
                        + ", ".join(f"{k}={markets[k]} ({sources[k]})" for k in ("П1", "X", "П2")),
                        flush=True,
                    )
                    return markets, sources
            except Exception as exc:
                print(f"KHL public odds page skipped: {url}: {type(exc).__name__}", flush=True)

        # One fallback search only. Never fan out to Google + Bing + DDG for
        # every bookmaker; that caused the repeated HTTP 429 storm seen in Railway.
        query = f'"{home}" "{away}" {date_iso} коэффициенты Winline Fonbet BetBoom Pari'
        fallback_url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
        try:
            response = await client.get(fallback_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            candidates: list[str] = []
            for block in soup.select(".result"):
                a = block.select_one(".result__a[href]")
                if a:
                    href = str(a.get("href") or "")
                    if href.startswith("http"):
                        candidates.append(href)

            for url in candidates[:8]:
                host = _host(url)
                if host not in ALLOWED_COMPARISON and host not in TARGET_BOOKMAKER_DOMAINS:
                    continue
                try:
                    text = await _fetch_page(client, url)
                except Exception as exc:
                    print(f"KHL odds result skipped: {host}: {type(exc).__name__}", flush=True)
                    continue
                for bookmaker, values in _parse_rows(text):
                    _add_best(markets, sources, values, bookmaker)
                if all(k in markets for k in ("П1", "X", "П2")):
                    print(
                        "KHL browser bookmaker odds confirmed: "
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
