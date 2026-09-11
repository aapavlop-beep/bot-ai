from .models import Match, Market


def rank_markets(match: Match) -> list[Market]:
    """Rank markets by positive expected value.

    Probability is supplied by the analytics layer; this function does not
    invent statistics or guarantee outcomes.
    """
    return sorted(match.markets, key=lambda m: m.value_percent, reverse=True)


def format_market(market: Market) -> str:
    value = market.value_percent
    value_label = f"+{value:.1f}%" if value >= 0 else f"{value:.1f}%"
    return (
        f"{market.name}\n"
        f"Вероятность: {market.probability * 100:.1f}%\n"
        f"КФ: {market.odds:.2f}\n"
        f"Справедливый КФ: {market.fair_odds:.2f}\n"
        f"Value: {value_label}"
    )
