from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup

ALLOWED_COMPARISON = {"prognozai.ru"}
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
    """Parse explicitly labelled bookmaker rows from any public web page.

    The page itself must identify the bookmaker next to the three 1X2 odds.
    We never accept an unlabeled three-number sequence as a bookmaker quote.
    """
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
            number_groups = [i for i in range(1, 5) if i != m.re.groupindex.get("book")]
            # The named group is always called 'book'; collect all numeric groups directly.
            nums = [g for g in m.groups() if g and g != m.group("book")]
            values = tuple(_odds(g) for g in nums[:3])
            if len(values) == 3 and all(v is not None for v in values):
                result.append((book, values))  # type: ignore[arg-type]
    return result


def _parse_1x2(text: str) -> tuple[float, float, float] | None:
    text = _normalize(text)
    patterns = [
        r"(?:П1|Победа\s*1|Хозяева|1)\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s+(?:X|Х|Ничья|Draw)\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s+(?:П2|Победа\s*2|Гости|2)\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
        r"\b1\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s*[|/]?\s*(?:X|Х)\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s*[|/]?\s*2\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
        r"\b([0-9]+[.,][0-9]+)\s+(?:X|Х)\s+([0-9]+[.,][0-9]+)\s+([0-9]+[.,][0-9]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            values = tuple(_odds(x) for x in match.groups())
            if all(v is not None for v in values):
                return values  # type: ignore[return-value]
    return None


def _parse_total_55(text: str) -> tuple[float, float] | None:
    text = _normalize(text)
    patterns = [
        r"ТБ\s*5[.,]5\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s+(?:ТМ|М)\s*5[.,]5\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
        r"(?:Тотал|Total)\s*5[.,]5.*?(?:Больше|Б|Over)\s*[:\-]?\s*([0-9]+[.,][0-9]+).*?(?:Меньше|М|Under)\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            values = tuple(_odds(x) for x in match.groups())
            if all(v is not None for v in values):
                return values  # type: ignore[return-value]
    return None


def _add_best(markets: dict[str, float], sources: dict[str, str], values: tuple[float, float, float], bookmaker: str) -> None:
    for market, odd in zip(("П1", "X", "П2"), values):
        if market not in markets or odd > markets[market]:
            markets[market] = odd
            sources[market] = bookmaker


async def search_bookmaker_web(home: str, away: str, date: str) -> tuple[dict[str, float], dict[str, str]]:
    """Find current 1X2 odds using public browser-style web research only.

    Direct bookmaker pages are preferred. If they block automated access, a
    public comparison/article page is accepted only when it explicitly labels
    Winline/Fonbet/BetBoom/Parimatch next to the odds. No bookmaker API is used.
    """
    queries = [
        f'"{home}" "{away}" {date} Winline',
        f'"{home}" "{away}" {date} Фонбет',
        f'"{home}" "{away}" {date} BetBoom',
        f'"{home}" "{away}" {date} Parimatch',
        f'"{home}" "{away}" {date} Winline Fonbet BetBoom Parimatch',
        f'site:prognozai.ru/matches/hockey "{home}" "{away}" {date} коэффициенты',
        f'site:prognozai.ru "{home}" "{away}" {date} Winline Fonbet BetBoom Parimatch',
    ]
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
    }
    markets: dict[str, float] = {}
    sources: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
        for query in queries:
            search_urls = (
                "https://www.bing.com/search?q=" + quote_plus(query) + "&setlang=ru",
                "https://html.duckduckgo.com/html/?q=" + quote_plus(query),
                "https://www.google.com/search?q=" + quote_plus(query) + "&hl=ru&num=10",
            )
            for engine_url in search_urls:
                try:
                    response = await client.get(engine_url)
                    response.raise_for_status()
                    soup = BeautifulSoup(response.text, "html.parser")
                    candidates: list[tuple[str, str]] = []
                    for block in soup.select("li.b_algo"):
                        a = block.select_one("h2 a[href]")
                        if a:
                            snippet = block.select_one(".b_caption p")
                            candidates.append((str(a.get("href") or ""), _normalize(snippet.get_text(" ", strip=True)) if snippet else ""))
                    for block in soup.select(".result"):
                        a = block.select_one(".result__a[href]")
                        if a:
                            snippet = block.select_one(".result__snippet")
                            candidates.append((str(a.get("href") or ""), _normalize(snippet.get_text(" ", strip=True)) if snippet else ""))
                    for block in soup.select("div.MjjYud"):
                        a = block.select_one("a[href]")
                        if a:
                            href = str(a.get("href") or "")
                            if href.startswith("/url?q="):
                                href = href.split("/url?q=", 1)[1].split("&", 1)[0]
                            snippet_node = block.select_one(".VwiC3b") or block.select_one("div[data-sncf]")
                            candidates.append((href, _normalize(snippet_node.get_text(" ", strip=True)) if snippet_node else ""))

                    seen: set[str] = set()
                    for url, snippet in candidates:
                        if not url.startswith("http") or url in seen:
                            continue
                        seen.add(url)
                        host = _host(url)
                        is_direct = host in TARGET_BOOKMAKER_DOMAINS
                        is_comparison = host in ALLOWED_COMPARISON

                        # Search snippets may already contain an explicitly labelled
                        # bookmaker row. Parse it regardless of the host.
                        for bookmaker, values in _parse_rows(snippet):
                            _add_best(markets, sources, values, bookmaker)

                        # Do not trust an unlabeled aggregator line. We only open
                        # arbitrary search results to look for explicit bookmaker labels.
                        if not (is_direct or is_comparison) and not any(
                            name.lower() in snippet.lower() for name in ("winline", "винлайн", "fonbet", "фонбет", "betboom", "бетбум", "parimatch", "pari")
                        ):
                            continue

                        try:
                            page = await client.get(url)
                            page.raise_for_status()
                            page_soup = BeautifulSoup(page.text, "html.parser")
                            page_text = _normalize(page_soup.get_text(" ", strip=True))
                        except Exception as exc:
                            print(f"KHL bookmaker page skipped: {host}: {type(exc).__name__}", flush=True)
                            continue

                        for bookmaker, values in _parse_rows(page_text):
                            _add_best(markets, sources, values, bookmaker)

                        if is_direct:
                            bookmaker = next((name for key, name in BOOKMAKERS.items() if key in host), "")
                            values = _parse_1x2(page_text)
                            if values and bookmaker:
                                _add_best(markets, sources, values, bookmaker)
                            total = _parse_total_55(page_text)
                            if total and bookmaker:
                                for market, odd in zip(("ТБ 5.5", "ТМ 5.5"), total):
                                    if market not in markets or odd > markets[market]:
                                        markets[market] = odd
                                        sources[market] = bookmaker

                    if all(key in markets for key in ("П1", "X", "П2")):
                        print(
                            "KHL browser bookmaker odds confirmed: "
                            + ", ".join(f"{k}={markets[k]} ({sources.get(k, '')})" for k in ("П1", "X", "П2")),
                            flush=True,
                        )
                        return markets, sources
                except Exception as exc:
                    print(f"KHL dedicated bookmaker web search failed: {type(exc).__name__}: {exc}", flush=True)

    return markets, sources
