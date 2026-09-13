from __future__ import annotations

import re
from typing import Any

from .models import Market


# Only these bookmaker sources are allowed for the KHL market.
TARGET_BOOKMAKER_DOMAINS = {
    "winline.ru",
    "fon.bet",
    "fonbet.ru",
    "fonbet.kz",
    "betboom.ru",
    "parimatch.com",
    "parimatch.ru",
}

ODDS_DOMAINS = TARGET_BOOKMAKER_DOMAINS.copy()


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
    return 100 if host in TARGET_BOOKMAKER_DOMAINS else 0


def markets_from_web_research(web_context: dict[str, Any]) -> tuple[tuple[Market, ...], dict[str, Any]]:
    """Extract current markets only from Winline, Fonbet, BetBoom or Parimatch.

    No bookmaker API is called. Aggregators, tipster sites and other bookmakers
    are deliberately rejected as line sources.
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
        return (), {
            "статус": "не найдено",
            "источники_линии": [],
            "разрешенные_БК": ["Winline", "Фонбет", "BetBoom", "Parimatch"],
        }

    candidates.sort(key=lambda item: bool(item[1].get("сниппет")), reverse=True)
    best = candidates[0]
    markets_by_name: dict[str, Market] = {}
    line_sources: dict[str, dict[str, Any]] = {}

    for _score, source, one_x_two, total in candidates:
        host = _host(str(source.get("url") or ""))
        if one_x_two:
            for name, odds in zip(("П1", "X", "П2"), one_x_two):
                current = markets_by_name.get(name)
                if current is None or odds > current.odds:
                    markets_by_name[name] = Market(name, odds, 1 / odds)
                    line_sources[name] = {"БК": host, "url": source.get("url")}
        if total:
            over, under = total
            for name, odds in (("ТБ 5.5", over), ("ТМ 5.5", under)):
                current = markets_by_name.get(name)
                if current is None or odds > current.odds:
                    markets_by_name[name] = Market(name, odds, 1 / odds)
                    line_sources[name] = {"БК": host, "url": source.get("url")}

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
        "основной_источник": {
            "БК": _host(str(best[1].get("url") or "")),
            "url": best[1].get("url"),
        },
        "разрешенные_БК": ["Winline", "Фонбет", "BetBoom", "Parimatch"],
        "правило": (
            "Линия принимается только с публичной веб-страницы Winline, Фонбет, "
            "BetBoom или Parimatch. API букмекеров и сторонние агрегаторы не используются."
        ),
    }
    return tuple(markets_by_name.values()), meta
