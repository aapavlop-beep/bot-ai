from __future__ import annotations

from urllib.parse import urlparse

from playwright.async_api import Page

from .web_odds import _first_1x2, _normalize
from .web_research import KHLWebResearcher, SearchResult


BOOKMAKER_DOMAINS = {
    "Winline": ("winline.ru",),
    "Fonbet": ("fon.bet", "fonbet.ru"),
    "BetBoom": ("betboom.ru",),
    "Parimatch": ("parimatch.com", "parimatch.ru"),
}


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _bookmaker_from_host(url: str) -> str | None:
    host = _host(url)
    for bookmaker, domains in BOOKMAKER_DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return bookmaker
    return None


def _contains_team(text: str, team: str) -> bool:
    text = _normalize(text).lower()
    team = _normalize(team).lower()
    if not team:
        return True
    if team in text:
        return True
    aliases = {
        "динамо москва": ("динамо м", "dynamo moscow", "dynamo moskva"),
        "динамо минск": ("динамо мн", "dynamo minsk"),
        "металлург мг": ("металлург магнитогорск", "metallurg magnitogorsk"),
        "цска": ("цска москва", "cska"),
        "хк сочи": ("сочи", "sochi"),
        "куньлунь ред стар": ("куньлунь", "kunlun"),
        "шанхайские драконы": ("шанхай дрэгонс", "шанхай", "shanghai dragons"),
    }
    return any(alias in text for alias in aliases.get(team, ()))


def _evidence_1x2(text: str, home: str, away: str) -> tuple[float, float, float] | None:
    normalized = _normalize(text)
    if not _contains_team(normalized, home) or not _contains_team(normalized, away):
        return None

    # Prefer a block containing both team names so numbers from another event
    # on the same bookmaker page are not mistaken for this match's line.
    low = normalized.lower()
    home_pos = low.find(_normalize(home).lower())
    away_pos = low.find(_normalize(away).lower())
    if home_pos >= 0 and away_pos >= 0:
        start = max(0, min(home_pos, away_pos) - 900)
        end = min(len(normalized), max(home_pos, away_pos) + 2500)
        parsed = _first_1x2(normalized[start:end])
        if parsed:
            return parsed

    return _first_1x2(normalized)


async def _search_all_engines(
    researcher: KHLWebResearcher,
    page: Page,
    query: str,
) -> list[SearchResult]:
    """Collect results from Yandex, Bing and Google in Chromium.

    The generic researcher intentionally stops at the first engine with
    results. That is fine for ordinary research, but bookmaker pages are often
    absent from one engine. Odds discovery therefore queries all three.
    """
    results_by_url: dict[str, SearchResult] = {}
    engines = (
        ("Yandex", researcher._search_yandex),
        ("Bing", researcher._search_bing),
        ("Google", researcher._search_google),
    )
    for name, engine in engines:
        try:
            results = await engine(page, query)
            print(
                f"KHL bookmaker browser search: {name} -> {len(results)} results | {query}",
                flush=True,
            )
            for result in results:
                if result.url.startswith("http"):
                    results_by_url.setdefault(result.url, result)
        except Exception as exc:
            print(
                f"KHL bookmaker browser search failed ({name}): {type(exc).__name__}: {exc}",
                flush=True,
            )
    return list(results_by_url.values())


async def search_bookmaker_web(
    home: str,
    away: str,
    date: str,
) -> tuple[dict[str, float], dict[str, str]]:
    """Find current 1X2 odds through Chromium browser research only.

    A quote is accepted only from a Winline, Fonbet, BetBoom or Parimatch
    domain and only after the opened page contains both teams and a parsed
    1-X-2 market. Search-engine snippets alone are never accepted as odds.
    """
    researcher = KHLWebResearcher()
    date_ru = f"{date[8:10]}.{date[5:7]}.{date[:4]}" if len(date) >= 10 else date
    queries = [
        f'"{home}" "{away}" {date_ru} Winline 1X2',
        f'"{home}" "{away}" {date_ru} Fonbet 1X2',
        f'"{home}" "{away}" {date_ru} BetBoom 1X2',
        f'"{home}" "{away}" {date_ru} Parimatch 1X2',
        f'site:winline.ru "{home}" "{away}" {date_ru}',
        f'site:fon.bet "{home}" "{away}" {date_ru}',
        f'site:betboom.ru "{home}" "{away}" {date_ru}',
        f'site:parimatch.com "{home}" "{away}" {date_ru}',
    ]

    markets: dict[str, float] = {}
    sources: dict[str, str] = {}
    checked_urls: set[str] = set()
    page = await researcher._new_page()
    try:
        for query in queries:
            results = await _search_all_engines(researcher, page, query)
            direct_results = [
                result for result in results if _bookmaker_from_host(result.url)
            ]
            print(
                f"KHL bookmaker candidates: direct={len(direct_results)} query={query}",
                flush=True,
            )

            for result in direct_results:
                bookmaker = _bookmaker_from_host(result.url)
                if not bookmaker or result.url in checked_urls:
                    continue
                checked_urls.add(result.url)

                evidence = _evidence_1x2(
                    f"{result.title} {result.snippet}", home, away
                )
                if evidence is None:
                    loaded = await researcher.fetch_page(result.url)
                    evidence = _evidence_1x2(
                        f"{loaded.title} {loaded.text} {result.snippet}",
                        home,
                        away,
                    )

                if evidence is None:
                    print(
                        f"KHL bookmaker page rejected: {bookmaker} {result.url} "
                        "(no team-matched 1X2 market)",
                        flush=True,
                    )
                    continue

                print(
                    f"KHL bookmaker odds confirmed: {bookmaker} {result.url} "
                    f"P1={evidence[0]} X={evidence[1]} P2={evidence[2]}",
                    flush=True,
                )
                for name, odd in zip(("П1", "X", "П2"), evidence):
                    if name not in markets or odd > markets[name]:
                        markets[name] = odd
                        sources[name] = bookmaker

            if len(markets) == 3:
                break
    finally:
        await page.context.close()
        await researcher.close()

    if markets:
        print(
            "KHL browser bookmaker line ready: "
            + ", ".join(
                f"{name}={odd} ({sources.get(name, '')})"
                for name, odd in markets.items()
            ),
            flush=True,
        )
    else:
        print(
            "KHL browser bookmaker research: no confirmed Winline/Fonbet/BetBoom/Parimatch line found",
            flush=True,
        )
    return markets, sources
