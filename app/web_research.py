from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import Browser, Page, async_playwright


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    text: str = ""
    published_at: str = ""


class KHLWebResearcher:
    """Real Chromium-based web research for KHL.

    Sports data is collected only by opening public web pages in Chromium.
    No sports API, API-SPORT, SofaScore or direct search-engine HTTP requests
    are used by this class.
    """

    CACHE_TTL = 20 * 60
    MAX_RESULTS_PER_QUERY = 8
    MAX_PAGES = 20
    MAX_TEXT_PER_PAGE = 8000
    NAV_TIMEOUT = 25_000
    SEARCH_DELAY = 0.8

    OFFICIAL = {"khl.ru", "fhr.ru"}
    SPORTS_MEDIA = {
        "sports.ru", "championat.com", "matchtv.ru", "allhockey.ru",
        "sport-express.ru", "metaratings.ru", "rsport.ria.ru", "ria.ru",
        "prosports.ru", "vprognoze.ru", "bookmaker-ratings.ru",
    }
    BOOKMAKER_HINTS = {
        "winline.ru", "fon.bet", "fonbet.ru", "betboom.ru",
        "parimatch.com", "parimatch.ru",
    }

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self._playwright = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

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
        if host in cls.BOOKMAKER_HINTS or any(host.endswith("." + d) for d in cls.BOOKMAKER_HINTS):
            return "bookmaker"
        if host in cls.SPORTS_MEDIA or any(host.endswith("." + d) for d in cls.SPORTS_MEDIA):
            return "sports_media"
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
        return str(node.get("datetime") or node.get_text(" ", strip=True) or "").strip() if node else ""

    async def _ensure_browser(self) -> Browser:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            print("KHL browser research: Chromium started", flush=True)
            return self._browser

    async def _new_page(self) -> Page:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            locale="ru-RU",
            timezone_id="Europe/Moscow",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7"},
        )
        return await context.new_page()

    async def close(self) -> None:
        async with self._lock:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None

    async def _search_yandex(self, page: Page, query: str) -> list[SearchResult]:
        url = "https://ya.ru/search/?text=" + quote_plus(query)
        await page.goto(url, wait_until="domcontentloaded", timeout=self.NAV_TIMEOUT)
        await page.wait_for_timeout(1200)
        results: list[SearchResult] = []
        cards = page.locator("li.serp-item, [data-cid], .Organic")
        for i in range(min(await cards.count(), 20)):
            card = cards.nth(i)
            try:
                href = await card.locator("a[href]").first.get_attribute("href", timeout=1200)
                title = self._clean(await card.locator("h2, .OrganicTitleContentSpan, [role='heading']").first.inner_text(timeout=1200))
                text = self._clean(await card.inner_text(timeout=1500))
            except Exception:
                continue
            if not href or not href.startswith("http") or not title:
                continue
            if self._host(href).endswith(("ya.ru", "yandex.ru")):
                continue
            results.append(SearchResult(title, href, text[:1800]))
        return results[: self.MAX_RESULTS_PER_QUERY]

    async def _search_bing(self, page: Page, query: str) -> list[SearchResult]:
        url = "https://www.bing.com/search?q=" + quote_plus(query) + "&setlang=ru&count=10"
        await page.goto(url, wait_until="domcontentloaded", timeout=self.NAV_TIMEOUT)
        await page.wait_for_timeout(700)
        results: list[SearchResult] = []
        cards = page.locator("li.b_algo")
        for i in range(min(await cards.count(), 12)):
            card = cards.nth(i)
            try:
                link = card.locator("h2 a[href]").first
                href = await link.get_attribute("href", timeout=1200)
                title = self._clean(await link.inner_text(timeout=1200))
                snippet = self._clean(await card.inner_text(timeout=1200))
            except Exception:
                continue
            if href and href.startswith("http") and title:
                results.append(SearchResult(title, href, snippet[:1800]))
        return results[: self.MAX_RESULTS_PER_QUERY]

    async def _search_google(self, page: Page, query: str) -> list[SearchResult]:
        url = "https://www.google.com/search?q=" + quote_plus(query) + "&hl=ru&num=10"
        await page.goto(url, wait_until="domcontentloaded", timeout=self.NAV_TIMEOUT)
        await page.wait_for_timeout(700)
        results: list[SearchResult] = []
        cards = page.locator("div.MjjYud")
        for i in range(min(await cards.count(), 12)):
            card = cards.nth(i)
            try:
                link = card.locator("a[href]").first
                title_node = card.locator("h3").first
                href = await link.get_attribute("href", timeout=1200)
                title = self._clean(await title_node.inner_text(timeout=1200))
                snippet = self._clean(await card.inner_text(timeout=1200))
            except Exception:
                continue
            if href and href.startswith("http") and title:
                results.append(SearchResult(title, href, snippet[:1800]))
        return results[: self.MAX_RESULTS_PER_QUERY]

    async def search(self, query: str) -> list[SearchResult]:
        """Search through a real Chromium tab. Yandex is primary."""
        page = await self._new_page()
        try:
            for name, engine in (("Yandex", self._search_yandex), ("Bing", self._search_bing), ("Google", self._search_google)):
                try:
                    results = await engine(page, query)
                    if results:
                        print(f"KHL browser search: {name} -> {len(results)} results | {query}", flush=True)
                        return results
                except Exception as exc:
                    print(f"KHL browser search failed ({name}): {type(exc).__name__}: {exc}", flush=True)
            return []
        finally:
            await page.context.close()

    async def fetch_page(self, url: str) -> SearchResult:
        page = await self._new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.NAV_TIMEOUT)
            await page.wait_for_timeout(900)
            html = await page.content()
            soup = BeautifulSoup(html, "html.parser")
            published_at = self._extract_published_at(soup)
            for node in soup.select("script,style,noscript,svg,nav,footer,header,form,iframe"):
                node.decompose()
            root = soup.select_one("article") or soup.select_one("main") or soup.body
            text = self._clean(root.get_text(" ", strip=True)) if root else ""
            title = self._clean(await page.title())
            return SearchResult(title, url, text[:1800], text[: self.MAX_TEXT_PER_PAGE], published_at)
        except Exception as exc:
            print(f"KHL browser page failed: {url}: {type(exc).__name__}: {exc}", flush=True)
            return SearchResult("", url, "")
        finally:
            await page.context.close()

    @staticmethod
    def _queries(home: str, away: str, date: str) -> list[str]:
        pair = f'"{home}" "{away}" КХЛ'
        return [
            f"{pair} {date} состав травмы дисквалификация",
            f"{pair} {date} стартовый состав вратарь",
            f"{pair} {date} вероятный состав",
            f"{pair} последние 5 матчей форма результаты",
            f"{home} КХЛ последние 5 матчей результаты {date}",
            f"{away} КХЛ последние 5 матчей результаты {date}",
            f"{pair} очные встречи H2H статистика",
            f"КХЛ турнирная таблица 2026 2027 {home} {away}",
            f"{home} КХЛ травмы новости {date}",
            f"{away} КХЛ травмы новости {date}",
            f"{pair} тренер пресс конференция новости {date}",
            f"site:khl.ru {pair} {date}",
            f"site:khl.ru {home} травма состав {date}",
            f"site:khl.ru {away} травма состав {date}",
            f"{pair} коэффициенты Winline Фонбет BetBoom Parimatch {date}",
            f"site:winline.ru {home} {away} {date}",
            f"site:fon.bet {home} {away} {date}",
            f"site:fonbet.ru {home} {away} {date}",
            f"site:betboom.ru {home} {away} {date}",
            f"site:parimatch.com {home} {away} {date}",
        ]

    @staticmethod
    def _bucket(text: str) -> list[str]:
        low = text.lower()
        patterns = {
            "match": ("матч", "начало", "расписан", "сегодня", "завтра"),
            "lineups": ("состав", "звено", "заявк", "линия"),
            "injuries": ("травм", "поврежд", "не сыгра", "больн", "лазарет"),
            "suspensions": ("дисквалифик", "штраф", "дисциплинар"),
            "goalies": ("вратар", "голкипер", "стартов", "ворота"),
            "form": ("последн", "побед", "пораж", "серия", "результат", "форма"),
            "h2h": ("личн", "очная", "h2h", "встреч"),
            "standings": ("таблиц", "место", "очки", "турнирн"),
            "travel": ("перелет", "перелёт", "выезд", "дорог", "часов"),
            "odds": ("коэффициент", "коэф", "кф", "ставк", "линия", "п1", "п2", "1 x 2"),
            "news": ("новост", "интервью", "тренер", "пресс-конференц"),
        }
        return [key for key, words in patterns.items() if any(word in low for word in words)] or ["other"]

    async def research_match(self, home: str, away: str, date: str) -> dict:
        key = f"{home}|{away}|{date}".lower()
        cached = self._cache.get(key)
        if cached and time.time() - cached[0] < self.CACHE_TTL:
            return cached[1]

        queries = self._queries(home, away, date)
        unique: dict[str, SearchResult] = {}
        page = await self._new_page()
        try:
            for query in queries:
                results: list[SearchResult] = []
                for name, engine in (("Yandex", self._search_yandex), ("Bing", self._search_bing), ("Google", self._search_google)):
                    try:
                        results = await engine(page, query)
                        if results:
                            print(f"KHL browser research search: {name} -> {len(results)} | {query}", flush=True)
                            break
                    except Exception as exc:
                        print(f"KHL browser research search failed ({name}): {type(exc).__name__}: {exc}", flush=True)
                for item in results:
                    host = self._host(item.url)
                    if host.endswith(("yandex.ru", "ya.ru", "bing.com", "google.com")):
                        continue
                    unique.setdefault(item.url, item)
                await page.wait_for_timeout(int(self.SEARCH_DELAY * 1000))
        finally:
            await page.context.close()

        all_results = list(unique.values())
        all_results.sort(key=lambda x: (self._source_priority(x.url), bool(x.snippet)), reverse=True)
        candidates = all_results[: self.MAX_PAGES]
        pages = await asyncio.gather(*(self.fetch_page(item.url) for item in candidates), return_exceptions=True)

        sources: list[dict] = []
        for original, loaded in zip(candidates, pages):
            page_result = loaded if isinstance(loaded, SearchResult) else original
            text = page_result.text or page_result.snippet
            if not text:
                continue
            sources.append({
                "заголовок": page_result.title or original.title,
                "url": page_result.url,
                "домен": self._host(page_result.url),
                "тип_источника": self._source_type(page_result.url),
                "приоритет": self._source_priority(page_result.url),
                "дата_публикации": page_result.published_at,
                "категории": self._bucket((page_result.title or original.title) + " " + text),
                "сниппет": original.snippet,
                "текст": text,
            })

        categories: dict[str, list[dict]] = {}
        for source in sources:
            for category in source["категории"]:
                categories.setdefault(category, []).append(source)
        evidence = {category: items[:8] for category, items in categories.items()}
        result = {
            "собрано_в_utc": datetime.now(timezone.utc).isoformat(),
            "матч": f"{home} — {away}",
            "дата_матча": date,
            "метод": "Chromium/Yandex+Bing+Google browser research + public page reading",
            "запросов": queries,
            "источников_всего": len(sources),
            "источников_линии": sum(1 for x in sources if x.get("тип_источника") == "bookmaker"),
            "источников": sources,
            "структурированные_доказательства": evidence,
            "правило_достоверности": (
                "Факт принимается только при наличии текста или сниппета публичного источника. "
                "Отсутствие игрока в составе не считается доказательством травмы. "
                "Свежие кадровые новости имеют приоритет над старыми."
            ),
        }
        self._cache[key] = (time.time(), result)
        print(f"KHL browser research complete: {home} — {away} sources={len(sources)}", flush=True)
        return result
