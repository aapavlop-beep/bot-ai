from __future__ import annotations

import re
from typing import Any

from .models import Market


ODDS_DOMAINS = {
    "fonbet.kz", "fonbet.ru", "fon.bet", "olimp.bet", "bettery.ru",
    "legalbet.ru", "legalbet.kz", "legalbet.tj", "vprognoze.kz", "vprognoze.ru",
    "sportsmotret.online", "leon.ru", "bookmaker-ratings.ru", "betboom.ru",
    "betcity.ru", "winline.ru", "ligastavok.ru",
}


def _host(url: str) -> str:
    return re.sub(r"^www\.", "", (url or "").split("/", 3)[2].lower()) if "://" in (url or "") else ""


def _num(value: str) -> float | None:
    try:
        number = float(value.replace(",", "."))
        return number if 1.01 <= number <= 100 else None
    except (TypeError, ValueError):
        return None


def _first_1x2(text: str) -> tuple[float, float, float] | None:
    normalized = re.sub(r"\s+", " ", text.replace("ё", "е")).strip()
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


def _total_55(text: str) -> tuple[float, float] | None:
    normalized = re.sub(r"\s+", " ", text.replace("ё", "е")).strip()
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


def _source_score(source: dict[str, Any]) -> int:
    host = _host(str(source.get("url") or ""))
    if host in {"fonbet.kz", "fonbet.ru", "fon.bet", "olimp.bet", "bettery.ru", "betboom.ru", "betcity.ru", "winline.ru", "ligastavok.ru"}:
        return 100
    if host in {"legalbet.ru", "legalbet.kz", "legalbet.tj", "vprognoze.kz", "vprognoze.ru"}:
        return 75
    if host in {"sportsmotret.online", "leon.ru", "bookmaker-ratings.ru"}:
        return 60
    return 0


def markets_from_web_research(web_context: dict[str, Any]) -> tuple[tuple[Market, ...], dict[str, Any]]:
    """Extract current public-web bookmaker markets; never call a bookmaker API."""
    sources = web_context.get("источники") or []
    candidates: list[tuple[int, dict[str, Any], tuple[float, float, float] | None, tuple[float, float] | None]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        score = _source_score(source)
        if score <= 0:
            continue
        text = " ".join(str(source.get(key) or "") for key in ("заголовок", "сниппет", "текст"))
        one_x_two = _first_1x2(text)
        total = _total_55(text)
        if one_x_two or total:
            candidates.append((score, source, one_x_two, total))

    if not candidates:
        return (), {"статус": "не найдено", "источников_линии": []}

    candidates.sort(key=lambda item: (item[0], bool(item[1].get("сниппет"))), reverse=True)
    best = candidates[0]
    markets_by_name: dict[str, Market] = {}
    line_sources: dict[str, dict[str, Any]] = {}

    for score, source, one_x_two, total in candidates:
        host = _host(str(source.get("url") or ""))
        if one_x_two:
            for name, odds in zip(("П1", "X", "П2"), one_x_two):
                current = markets_by_name.get(name)
                if current is None or odds > current.odds:
                    markets_by_name[name] = Market(name, odds, 1 / odds)
                    line_sources[name] = {"домен": host, "url": source.get("url"), "приоритет": score}
        if total:
            over, under = total
            for name, odds in (("ТБ 5.5", over), ("ТМ 5.5", under)):
                current = markets_by_name.get(name)
                if current is None or odds > current.odds:
                    markets_by_name[name] = Market(name, odds, 1 / odds)
                    line_sources[name] = {"домен": host, "url": source.get("url"), "приоритет": score}

    one_x_two_names = ("П1", "X", "П2")
    if all(name in markets_by_name for name in one_x_two_names):
        inv_sum = sum(1 / markets_by_name[name].odds for name in one_x_two_names)
        for name in one_x_two_names:
            old = markets_by_name[name]
            markets_by_name[name] = Market(name, old.odds, (1 / old.odds) / inv_sum)
    if all(name in markets_by_name for name in ("ТБ 5.5", "ТМ 5.5")):
        inv_sum = sum(1 / markets_by_name[name].odds for name in ("ТБ 5.5", "ТМ 5.5"))
        for name in ("ТБ 5.5", "ТМ 5.5"):
            old = markets_by_name[name]
            markets_by_name[name] = Market(name, old.odds, (1 / old.odds) / inv_sum)

    meta = {
        "статус": "найдено",
        "источники_линии": line_sources,
        "основной_источник": {"домен": _host(str(best[1].get("url") or "")), "url": best[1].get("url")},
        "правило": "Линия извлечена из свежего публичного веб-источника; спортивные и букмекерские API не используются.",
    }
    return tuple(markets_by_name.values()), meta
