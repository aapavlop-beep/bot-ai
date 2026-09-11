from __future__ import annotations

from dataclasses import dataclass

from .models import Market


@dataclass(frozen=True)
class Signal:
    market: Market
    confidence: str
    score: float


def confidence_label(probability: float) -> str:
    percent = probability * 100
    if percent >= 70:
        return "★★★★★"
    if percent >= 65:
        return "★★★★☆"
    if percent >= 60:
        return "★★★☆☆"
    if percent >= 55:
        return "★★☆☆☆"
    return "★☆☆☆☆"


def rank_signals(markets: list[Market], min_probability: float = 0.55) -> list[Signal]:
    signals: list[Signal] = []
    for market in markets:
        if market.probability < min_probability:
            continue
        # Value is the primary ranking signal; probability acts as a safety floor.
        score = market.value_percent + market.probability * 10
        signals.append(
            Signal(
                market=market,
                confidence=confidence_label(market.probability),
                score=score,
            )
        )
    return sorted(signals, key=lambda x: x.score, reverse=True)
