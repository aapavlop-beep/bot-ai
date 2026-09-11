from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Sport(str, Enum):
    CS2 = "cs2"
    KHL = "khl"
    FOOTBALL = "football"


@dataclass(frozen=True)
class Market:
    name: str
    odds: float
    probability: float

    @property
    def fair_odds(self) -> float:
        return 1 / self.probability if self.probability > 0 else 0

    @property
    def value_percent(self) -> float:
        return (self.odds * self.probability - 1) * 100


@dataclass(frozen=True)
class Match:
    sport: Sport
    league: str
    home: str
    away: str
    start_time: str
    markets: tuple[Market, ...]
    analysis_data: dict[str, Any] = field(default_factory=dict)
