from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    text: str = ""
    published_at: str = ""


class KHLWebResearcher:
    """Build a source-attributed KHL research dossier from public web pages."""

    CACHE_TTL = 30 * 60
    MAX_RESULTS_PER_QUERY = 6
    MAX_PAGES = 36
    MAX_TEXT_PER_PAGE = 6500
    REQUEST_TIMEOUT = 15.0

    OFFICIAL = {"khl.ru", "fhr.ru"}
    SPORTS_MEDIA = {
        "sports.ru", "championat.com", "matchtv.ru", "allhockey.ru",
        "sport-express.ru", "metaratings.ru", "rsport.ria.ru",
    }
    # Only the four requested bookmakers are treated as bookmaker line sources.
    BOOKMAKER_HINTS = {
        "winline.ru",
        "fon.bet", "fonbet.ru", "fonbet.kz",
        "betboom.ru",
        "parimatch.com", "parimatch.ru",
    }

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.REQUEST_TIMEOUT, connect=8.0),
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36"
                ),
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            },
        )

    @staticmethod
    def _clean(value: str) -> str:
        return re.sub(r"\s+", " ", value or "").strip()

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")

    @classmethod
    def _source_type(cls, url: str) -> str:
        host = cls._host(url)
        if host in cls.OFFICIAL or any(host.endswith("." + d) for d in cls.OFFICIAL):
            return "official"
        if host in cls.SPORTS_MEDIA or any(host.endswith("." + d) for d in cls.SPORTS_MEDIA):
            return "sports_media"
        if host in cls.BOOKMAKER_HINTS or any(host.endswith("." + d) for d in cls.BOOKMAKER_HINTS):
            return "bookmaker"
        return "other"

    @classmethod
    def _source_priority(cls, url: str) -> int:
        return {"official": 4, "sports_media": 3, "bookmaker": 2, "other": 1}[cls._source_type(url)]

    @staticmethod
    def _extract_published_at(soup: BeautifulSoup) -> str:
        selectors = [
            ("meta", {"property": "article:published_time"}),
            ("meta", {"property": "og:article:published_time"}),
            ("meta", {"name": "date"}),
            ("meta", {"name": "pubdate"}),
            ("meta", {"itemprop": "datePublished"}),
        ]
        for tag, attrs in selectors:
            node = soup.find(tag, attrs=attrs)
            if node and node.get("content"):
                return str(node.get("content")).strip()
        node = soup.find("time")
        if node:
            return str(node.get("datetime") or node.get_text(" ", strip=True) or "").strip()
        return ""

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
            href = str(link.get("href") or "")
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
            published_at = self._extract_published_at(soup)
            for node in soup.select("script,style,noscript,svg,nav,footer,header,form"):
                node.decompose()
            root = soup.select_one("article") or soup.select_one("main") or soup.body
            if root is None:
                return SearchResult(result.title, result.url, result.snippet, "", published_at)
            text = self._clean(root.get_text(" ", strip=True))
            return SearchResult(result.title, result.url, result.snippet, text[: self.MAX_TEXT_PER_PAGE], published_at)
        except Exception as exc:
            print(f"KHL web page failed: {result.url}: {type(exc).__name__}: {exc}", flush=True)
            return result

    @staticmethod
    def _queries(home: str, away: str, date: str) -> list[str]:
        pair = f'"{home}" "{away}" КХЛ'
        return [
            f"{pair} {date} состав травмы дисквалификация",
            f"{pair} {date} стартовый состав вратарь",
            f"{pair} последние 5 матчей результаты форма",
            f"{pair} последние 10 матчей результаты статистика",
            f"{pair} очные встречи H2H",
            f"{home} КХЛ состав травмы дисквалификация {date}",
            f"{away} КХЛ состав травмы дисквалификация {date}",
            f"{home} КХЛ вероятный вратарь {date}",
            f"{away} КХЛ вероятный вратарь {date}",
            f"КХЛ таблица 2026 2027 турнирная таблица",
            f"{pair} коэффициенты Winline Фонбет BetBoom Parimatch {date}",
            f"{pair} линия П1 X П2 Winline Фонбет BetBoom Parimatch {date}",
            f"{pair} коэффициенты 1 X 2 Winline Фонбет BetBoom Parimatch {date}",
            f"site:winline.ru {home} {away} {date}",
            f"site:fon.bet {home} {away} {date}",
            f"site:fonbet.ru {home} {away} {date}",
            f"site:betboom.ru {home} {away} {date}",
            f"site:parimatch.com {home} {away} {date}",
            f"site:parimatch.ru {home} {away} {date}",
            f"site:khl.ru {home} {away} {date}",
            f"site:khl.ru {home} травма состав {date}",
            f"site:khl.ru {away} травма состав {date}",
            f"site:khl.ru {home} {away} вратарь {date}",
        ]

    @staticmethod
    def _bucket(text: str) -> list[str]:
        low = text.lower()
        buckets: list[str] = []
        patterns = {
            "match": ("матч", "начало", "расписан", "сегодня", "завтра"),
            "lineups": ("состав", "звено", "заявк", "линия"),
            "injuries": ("травм", "поврежд", "не сыгра", "больн", "в лазарете"),
            "suspensions": ("дисквалифик", "штраф", "дисциплинар"),
            "goalies": ("вратар", "голкипер", "стартов", "ворота"),
            "form": ("последн", "побед", "пораж", "серия", "результат"),
            "h2h": ("личн", "очная", "h2h", "встреч"),
            "standings": ("таблиц", "место", "очки", "турнирн"),
            "travel": ("перелет", "перелёт", "выезд", "дорог", "часов"),
            "odds": ("коэффициент", "коэф", "кф", "ставк", "линия", "п1", "п2", "x ", "1 x 2"),
            "news": ("новост", "интервью", "тренер", "пресс-конференц"),
        }
        for bucket, words in patterns.items():
            if any(word in low for word in words):
                buckets.append(bucket)
        return buckets or ["other"]

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
                host = self._host(item.url)
                if not item.url.startswith("http") or host.endswith("google.com") or host.endswith("bing.com"):
                    continue
                if item.url not in unique:
                    unique[item.url] = item

        all_results = list(unique.values())
        bookmaker_results = [x for x in all_results if self._source_type(x.url) == "bookmaker"]
        other_results = [x for x in all_results if self._source_type(x.url) != "bookmaker"]
        bookmaker_results.sort(key=lambda x: bool(x.snippet), reverse=True)
        other_results.sort(key=lambda x: (self._source_priority(x.url), bool(x.snippet)), reverse=True)
        # Force bookmaker evidence into the fetched set.
        candidates = (bookmaker_results[:12] + other_results[: max(0, self.MAX_PAGES - min(12, len(bookmaker_results)))])[: self.MAX_PAGES]

        pages = await asyncio.gather(*(self._extract_page(item) for item in candidates), return_exceptions=True)
        sources: list[dict] = []
        for page in pages:
            if not isinstance(page, SearchResult):
                continue
            text = page.text or page.snippet
            if not text:
                continue
            sources.append({
                "заголовок": page.title,
                "url": page.url,
                "домен": self._host(page.url),
                "тип_источника": self._source_type(page.url),
                "приоритет": self._source_priority(page.url),
                "дата_публикации": page.published_at,
                "категории": self._bucket(page.title + " " + text),
                "сниппет": page.snippet,
                "текст": text,
            })

        categories: dict[str, list[dict]] = {}
        for source in sources:
            for category in source["категории"]:
                categories.setdefault(category, []).append(source)
        evidence = {category: items[:6] for category, items in categories.items()}
        now = datetime.now(timezone.utc).isoformat()
        result = {
            "собрано_в_utc": now,
            "матч": f"{home} — {away}",
            "дата_матча": date,
            "метод": "Google/Bing web search + HTML page extraction",
            "запросов": queries,
            "источников_всего": len(sources),
            "источников_линии": sum(1 for x in sources if x.get("тип_источника") == "bookmaker"),
            "источников": sources,
            "структурированные_доказательства": evidence,
            "правило_достоверности": (
                "Факт считается подтверждённым только при наличии текста или сниппета источника. "
                "Не считать отсутствие игрока травмой без явного подтверждения. "
                "Для кадровых новостей и коэффициентов учитывать свежесть и источник. "
                "Коэффициент из поисковой выдачи является кандидатом, а не подтверждённой линией. "
                "При конфликте источников приоритет: официальный KHL/клуб, затем крупное спортивное СМИ, затем прочие источники."
            ),
        }
        self._cache[key] = (time.time(), result)
        return result

    async def aclose(self) -> None:
        await self._client.aclose()
