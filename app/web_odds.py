from __future__ import annotations

import re
from typing import Any

from .models import Market


# Public pages that commonly expose the current KHL line in HTML/search snippets.
ODDS_DOMAINS = {
    "fonbet.kz", "fonbet.ru", "olimp.bet", "bettery.ru", "legalbet.ru", "legalbet.kz",
    "legalbet.tj", "vprognoze.kz", "vprognoze.ru", "sportsmotret.online", "leon.ru",
    "bookmaker-ratings.ru",
}


def _host(url: str) -> str:
    return re.sub(r"^www\.", "", (url or "").split("/", 3)[2].lower()) if "://" in (url or "") else ""


def _num(value: str) -> float | None:
    try:
        value = value.replace(",", ".")
        number = float(value)
        return number if 1.01 <= number <= 100 else None
    except (TypeError, ValueError):
        return None


def _first_1x2(text: str) -> tuple[float, float, float] | None:
    # Typical bookmaker HTML/search text: "П1 2.65 Х 4.20 П2 2.30".
    patterns = [
        r"П1\s*([0-9]+[.,][0-9]+)\s*Х\s*([0-9]+[.,][0-9]+)\s*П2\s*([0-9]+[.,][0-9]+)",
        r"\b1\s+([0-9]+[.,][0-9]+)\s+X\s+([0-9]+[.,][0-9]+)\s+2\s+([0-9]+[.,][0-9]+)",
        r"\b1\s+([0-9]+[.,][0-9]+).*?\bX\s+([0-9]+[.,][0-9]+).*?\b2\s+([0-9]+[.,][0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        values = tuple(_num(x) for x in match.groups())
        if all(v is not None for v in values):
            return values  # type: ignore[return-value]
    return None


def _total_55(text: str) -> tuple[float, float] | None:
    patterns = [
        r"ТБ\s*5[.,]5\s*([0-9]+[.,][0-9]+)\s*ТМ\s*5[.,]5\s*([0-9]+[.,][0-9]+)",
        r"ТБ\s*5[.,]5\s*([0-9]+[.,][0-9]+).*?ТМ\s*5[.,]5\s*([0-9]+[.,][0-9]+)",
        r"Тотал\s*5[.,]5\s*(?:Больше|больше)\s*([0-9]+[.,][0-9]+).*?(?:Меньше|меньше)\s*([0-9]+[.,][0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            over, under = (_num(x) for x in match.groups())
            if over is not None and under is not None:
                return over, under
    return None


def _source_score(source: dict[str, Any]) -> int:
    host = _host(str(source.get("url") or ""))
    if host in {"fonbet.kz", "fonbet.ru", "olimp.bet", "bettery.ru"}:
        return 100
    if host in {"legalbet.ru", "legalbet.kz", "legalbet.tj", "vprognoze.kz", "vprognoze.ru"}:
        return 70
    if host in {"sportsmotret.online", "leon.ru", "bookmaker-ratings.ru"}:
        return 50
    return 0


def markets_from_web_research(web_context: dict[str, Any]) -> tuple[tuple[Market, ...], dict[str, Any]]:
    """Turn freshly fetched public-web bookmaker lines into safe Market objects.

    Odds are accepted only from a source page that contains the complete market
    in its extracted text. The source URL and extraction timestamp are returned
    separately so the bot can show where the line came from.
    """
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

    candidates.sort(key=lambda item: item[0], reverse=True)
    best = candidates[0]
    markets_by_name: dict[str, Market] = {}
    line_sources: dict[str, dict[str, Any]] = {}

    # Prefer a direct bookmaker page over aggregators. If several direct pages
    # exist, keep the best available price for each market.
    for score, source, one_x_two, total in candidates:
        host = _host(str(source.get("url") or ""))
        if one_x_two:
            names = ("П1", "X", "П2")
            for name, odds in zip(names, one_x_two):
                current = markets_by_name.get(name)
                if current is None or odds > current.odds:
                    # For 1X2, probabilities are normalized below after all
                    # three best prices have been collected.
                    markets_by_name[name] = Market(name, odds, 1 / odds)
                    line_sources[name] = {"домен": host, "url": source.get("url"), "приоритет": score}
        if total:
            over, under = total
            for name, odds in (("ТБ 5.5", over), ("ТМ 5.5", under)):
                current = markets_by_name.get(name)
                if current is None or odds > current.odds:
                    markets_by_name[name] = Market(name, odds, 1 / odds)
                    line_sources[name] = {"домен": host, "url": source.get("url"), "приоритет": score}

    # Normalize bookmaker margin for mutually exclusive markets.
    one_x_two_names = ("П1", "X", "П2")
    if all(name in markets_by_name for name in one_x_two_names):
        inv_sum = sum(1 / markets_by_name[name].odds for name in one_x_two_names)
        for name in one_x_two_names:
            old = markets_by_name[name]
            markets_by_name[name] = Market(name, old.odds, (1 / old.odds) / inv_sum)
    for name_pair in (("ТБ 5.5", "ТМ 5.5"),):
        if all(name in markets_by_name for name in name_pair):
            inv_sum = sum(1 / markets_by_name[name].odds for name in name_pair)
            for name in name_pair:
                old = markets_by_name[name]
                markets_by_name[name] = Market(name, old.odds, (1 / old.odds) / inv_sum)

    meta = {
        "статус": "найдено",
        "источники_линии": line_sources,
        "основной_источник": {
            "домен": _host(str(best[1].get("url") or "")),
            "url": best[1].get("url"),
        },
        "правило": "Линия извлечена из свежего публичного веб-источника; API букмекера не использовался.",
    }
    return tuple(markets_by_name.values()), meta
