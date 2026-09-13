from __future__ import annotations

import re
from typing import Any

from .models import Market


# Direct bookmaker domains are preferred. Comparison pages are accepted only
# when they explicitly label the row as one of the four requested bookmakers.
TARGET_BOOKMAKER_DOMAINS = {
    "winline.ru",
    "fon.bet",
    "fonbet.ru",
    "fonbet.kz",
    "betboom.ru",
    "parimatch.com",
    "parimatch.ru",
}
ODDS_COMPARISON_DOMAINS = {"prognozai.ru"}
ODDS_DOMAINS = TARGET_BOOKMAKER_DOMAINS.copy()

BOOKMAKER_NAMES = {
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
        host = (url or "").split("/", 3)[2].lower()
        return re.sub(r"^www\.", "", host)
    except (IndexError, AttributeError):
        return ""


def _num(value: str) -> float | None:
    try:
        number = float(value.replace(",", "."))
        return number if 1.01 <= number <= 100 else None
    except (TypeError, ValueError):
        return None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("ё", "е")).strip()


def _first_1x2(text: str) -> tuple[float, float, float] | None:
    normalized = _normalize(text)
    patterns = [
        r"(?:П1|Победа\s*1|Хозяева|1)\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s+(?:X|Х|Ничья|Draw)\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s+(?:П2|Победа\s*2|Гости|2)\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
        r"\b1\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s*[|/]?\s*(?:X|Х)\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s*[|/]?\s*2\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
        r"\b([0-9]+[.,][0-9]+)\s+(?:X|Х)\s+([0-9]+[.,][0-9]+)\s+([0-9]+[.,][0-9]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            values = tuple(_num(x) for x in match.groups())
            if all(v is not None for v in values):
                return values  # type: ignore[return-value]
    return None


def _named_bookmaker_rows(text: str) -> list[tuple[str, tuple[float, float, float]]]:
    """Parse explicitly labelled bookmaker rows from comparison pages.

    Example: ``Winline | 2.35 | 4.20 | 2.60``.
    This never treats an unlabeled number as a bookmaker quote.
    """
    normalized = _normalize(text)
    names = r"(?:Winline|Винлайн|Fonbet|Фонбет|BetBoom|БетБум|Parimatch|PARI|Pari)"
    number = r"([0-9]+[.,][0-9]+)"
    separators = r"(?:\s*[|;/]\s*|\s+)"
    pattern = rf"(?P<book>{names}){separators}{number}{separators}{number}{separators}{number}(?!\d)"
    rows: list[tuple[str, tuple[float, float, float]]] = []
    for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
        raw_name = match.group("book").lower().replace("ё", "е")
        bookmaker = BOOKMAKER_NAMES.get(raw_name)
        values = tuple(_num(match.group(i)) for i in (2, 3, 4))
        if bookmaker and all(v is not None for v in values):
            rows.append((bookmaker, values))  # type: ignore[arg-type]
    return rows


def _total_55(text: str) -> tuple[float, float] | None:
    normalized = _normalize(text)
    patterns = [
        r"ТБ\s*5[.,]5\s*[:\-]?\s*([0-9]+[.,][0-9]+)\s+(?:ТМ|М)\s*5[.,]5\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
        r"(?:Тотал|Total)\s*5[.,]5.*?(?:Больше|Б|Over)\s*[:\-]?\s*([0-9]+[.,][0-9]+).*?(?:Меньше|М|Under)\s*[:\-]?\s*([0-9]+[.,][0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if match:
            over, under = (_num(x) for x in match.groups())
            if over is not None and under is not None:
                return over, under
    return None


def _is_direct(host: str) -> bool:
    return host in TARGET_BOOKMAKER_DOMAINS


def _is_comparison(host: str) -> bool:
    return host in ODDS_COMPARISON_DOMAINS


def markets_from_web_research(web_context: dict[str, Any]) -> tuple[tuple[Market, ...], dict[str, Any]]:
    """Extract KHL markets from the four requested bookmakers.

    Direct bookmaker pages are preferred. If a bookmaker blocks automated page
    access, an explicit bookmaker-labelled row on an approved comparison page
    may be used. Unlabelled aggregator odds are never accepted.
    """
    sources = web_context.get("источники") or []
    candidates: list[tuple[int, dict[str, Any], tuple[float, float, float] | None, tuple[float, float] | None, list[tuple[str, tuple[float, float, float]]]]] = []

    for source in sources:
        if not isinstance(source, dict):
            continue
        host = _host(str(source.get("url") or ""))
        if not (_is_direct(host) or _is_comparison(host)):
            continue
        text = " ".join(str(source.get(key) or "") for key in ("заголовок", "сниппет", "текст"))
        one_x_two = _first_1x2(text) if _is_direct(host) else None
        named_rows = _named_bookmaker_rows(text) if _is_comparison(host) else []
        total = _total_55(text)
        if one_x_two or named_rows or total:
            score = 100 if _is_direct(host) else 80
            candidates.append((score, source, one_x_two, total, named_rows))

    if not candidates:
        return (), {
            "статус": "не найдено",
            "источники_линии": [],
            "разрешенные_БК": ["Winline", "Фонбет", "BetBoom", "Parimatch"],
        }

    markets_by_name: dict[str, Market] = {}
    line_sources: dict[str, dict[str, Any]] = {}

    def put_market(name: str, odds: float, bookmaker: str, source: dict[str, Any]) -> None:
        current = markets_by_name.get(name)
        if current is None or odds > current.odds:
            markets_by_name[name] = Market(name, odds, 1 / odds)
            line_sources[name] = {
                "БК": bookmaker,
                "url": source.get("url"),
                "источник_тип": source.get("тип_источника", "bookmaker"),
            }

    for _score, source, one_x_two, total, named_rows in candidates:
        host = _host(str(source.get("url") or ""))
        if one_x_two and _is_direct(host):
            bookmaker = next((name for key, name in BOOKMAKER_NAMES.items() if key in host), host)
            for name, odds in zip(("П1", "X", "П2"), one_x_two):
                put_market(name, odds, bookmaker, source)
        for bookmaker, odds_set in named_rows:
            for name, odds in zip(("П1", "X", "П2"), odds_set):
                put_market(name, odds, bookmaker, source)
        if total and _is_direct(host):
            over, under = total
            bookmaker = next((name for key, name in BOOKMAKER_NAMES.items() if key in host), host)
            put_market("ТБ 5.5", over, bookmaker, source)
            put_market("ТМ 5.5", under, bookmaker, source)

    for name_group in (("П1", "X", "П2"), ("ТБ 5.5", "ТМ 5.5")):
        if all(name in markets_by_name for name in name_group):
            inv_sum = sum(1 / markets_by_name[name].odds for name in name_group)
            for name in name_group:
                old = markets_by_name[name]
                markets_by_name[name] = Market(name, old.odds, (1 / old.odds) / inv_sum)

    primary = next((s for s in candidates if _is_direct(_host(str(s[1].get("url") or "")))), candidates[0])
    meta = {
        "статус": "найдено",
        "источники_линии": line_sources,
        "основной_источник": {
            "БК": line_sources[next(iter(line_sources))]["БК"] if line_sources else "",
            "url": primary[1].get("url"),
        },
        "разрешенные_БК": ["Winline", "Фонбет", "BetBoom", "Parimatch"],
        "правило": (
            "Линия принимается только с публичной веб-страницы Winline, Фонбет, BetBoom или Parimatch, "
            "либо из явно подписанной строки одного из этих БК на утверждённой странице сравнения. "
            "API букмекеров и неидентифицированные агрегаторные коэффициенты не используются."
        ),
    }
    return tuple(markets_by_name.values()), meta
