from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Sport(str, Enum):
    CS2 = "cs2"
    KHL = "khl"
    FOOTBALL = "football"


def _json_safe(value: Any, seen: set[int] | None = None, depth: int = 0) -> Any:
    """Make provider data safe for JSON serialization and break cycles."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth > 8:
        return None
    if seen is None:
        seen = set()
    object_id = id(value)
    if object_id in seen:
        return None
    if isinstance(value, dict):
        seen.add(object_id)
        result = {str(key): _json_safe(item, seen, depth + 1) for key, item in value.items()}
        seen.remove(object_id)
        return result
    if isinstance(value, (list, tuple)):
        seen.add(object_id)
        result = [_json_safe(item, seen, depth + 1) for item in value]
        seen.remove(object_id)
        return result
    return str(value)


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

    def __post_init__(self) -> None:
        # Provider responses can contain shared/cyclic references. Normalize
        # them once at the boundary so every AI implementation receives plain
        # JSON-safe data. This prevents ValueError: Circular reference detected.
        object.__setattr__(self, "analysis_data", _json_safe(self.analysis_data))
