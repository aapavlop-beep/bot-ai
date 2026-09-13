from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    text: str = ""


class KHLWebResearcher:
    """Browser-style web research without a sports-data API.

    Searches public web pages, follows relevant results, extracts readable text,
    and returns source-attributed evidence for the AI analyst. It deliberately
    does not invent missing facts or bookmaker odds.
    """

    CACHE_TTL = 20 * 60
    MAX_RESULTS_PER_QUERY = 3
    MAX_PAGES = 14
    MAX_TEXT_PER_PAGE = 4500
    REQUEST_TIMEOUT = 12.0

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.REQUEST_TIMEOUT, connect=8.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/139.0 Safari/537.36"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            },
        )

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _same_domain(url: str, allowed: set[str]) -> bool:
        host = urlparse(url).netloc.lower().removeprefix("www.")
        return host in allowed or any(host.endswith("." + d) for d in allowed)

    async def _search_google(self, query: str) -> list[SearchResult]:
        url = "https://www.google.com/search?q=" + quote_plus(query) + "&hl=ru&num=10"
        response = await self._client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for block in soup.select("div.MjjYud"):
            link = block.select_one("a[href]")
            title_node = block.select_one("h3")
            if not link or not title_node:
                continue
            href = link.get("href") or ""
            if href.startswith("/url?q="):
                href = href.split("/url?q=", 1)[1].split("&", 1)[0]
            if not href.startswith("http"):
                continue
            snippet_node = block.select_one(".VwiC3b") or block.select_one("div[data-sncf]")
            results.append(SearchResult(
                title=self._clean(title_node.get_text(" ", strip=True)),
                url=href,
                snippet=self._clean(snippet_node.get_text(" ", strip=True)) if snippet_node else "",
            ))
        return results

    async def _search_bing(self, query: str) -> list[SearchResult]:
        url = "https://www.bing.com/search?q=" + quote_plus(query) + "&setlang=ru"
        response = await self._client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for block in soup.select("li.b_algo"):
            link = block.select_one("h2 a[href]")
            if not link:
                continue
            snippet = block.select_one(".b_caption p")
            results.append(SearchResult(
                title=self._clean(link.get_text(" ", strip=True)),
                url=str(link.get("href") or ""),
                snippet=self._clean(snippet.get_text(" ", strip=True)) if snippet else "",
            ))
        return results

    async def search(self, query: str) -> list[SearchResult]:
        for engine in (self._search_google, self._search_bing):
            try:
                results = await engine(query)
                if results:
                    return results[: self.MAX_RESULTS_PER_QUERY]
            except Exception as exc:
                print(f"KHL web search failed ({engine.__name__}): {type(exc).__name__}: {exc}", flush=True)
        return []

    async def _extract_page(self, result: SearchResult) -> SearchResult:
        try:
            response = await self._client.get(result.url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return result
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup.select("script,style,noscript,svg,nav,footer,header,form"):
                node.decompose()
            root = soup.select_one("article") or soup.select_one("main") or soup.body
            if root is None:
                return result
            text = self._clean(root.get_text(" ", strip=True))
            return SearchResult(result.title, result.url, result.snippet, text[: self.MAX_TEXT_PER_PAGE])
        except Exception as exc:
            print(f"KHL web page failed: {result.url}: {type(exc).__name__}: {exc}", flush=True)
            return result

    @staticmethod
    def _queries(home: str, away: str, date: str) -> list[str]:
        pair = f'"{home}" "{away}" КХЛ'
        return [
            f"{pair} {date} состав травмы дисквалификация",
            f"{pair} последние матчи форма результаты",
            f"{pair} очные встречи H2H",
            f"{home} КХЛ последние матчи состав травмы {date}",
            f"{away} КХЛ последние матчи состав травмы {date}",
            f"{home} {away} КХЛ вратарь состав перед матчем {date}",
            f"КХЛ таблица 2026 2027 турнирная таблица",
            f"{pair} коэффициенты букмекеров {date}",
            f"site:khl.ru {home} {away} {date}",
            f"site:khl.ru {home} травма состав {date}",
            f"site:khl.ru {away} травма состав {date}",
        ]

    async def research_match(self, home: str, away: str, date: str) -> dict:
        key = f"{home}|{away}|{date}".lower()
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < self.CACHE_TTL:
            return cached[1]

        queries = self._queries(home, away, date)
        query_results = await asyncio.gather(*(self.search(q) for q in queries), return_exceptions=True)
        unique: dict[str, SearchResult] = {}
        for results in query_results:
            if not isinstance(results, list):
                continue
            for item in results:
                host = urlparse(item.url).netloc.lower()
                if not item.url.startswith("http") or host.endswith("google.com") or host.endswith("bing.com"):
                    continue
                unique.setdefault(item.url, item)

        selected = list(unique.values())[: self.MAX_PAGES]
        pages = await asyncio.gather(*(self._extract_page(item) for item in selected), return_exceptions=True)
        sources: list[dict] = []
        for page in pages:
            if not isinstance(page, SearchResult):
                continue
            sources.append({
                "заголовок": page.title,
                "url": page.url,
                "домен": urlparse(page.url).netloc,
                "сниппет": page.snippet,
                "текст": page.text,
            })

        now = datetime.now(timezone.utc).isoformat()
        result = {
            "собрано_в_utc": now,
            "метод": "web search + page extraction",
            "запросов": queries,
            "источников": sources,
            "правило_достоверности": (
                "Факт считается подтверждённым только при наличии текста/сниппета источника. "
                "Отсутствующие сведения нельзя додумывать. Для травм, составов и коэффициентов "
                "приоритет имеют свежие официальные источники; старые страницы используются только как контекст."
            ),
        }
        self._cache[key] = (time.time(), result)
        return result

    async def aclose(self) -> None:
        await self._client.aclose()
