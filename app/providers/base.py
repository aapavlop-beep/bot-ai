from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Event:
    id: str
    sport: str
    league: str
    home: str
    away: str
    start_time: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Market:
    event_id: str
    name: str
    selection: str
    line: float | None
    odds: float
    bookmaker: str | None = None


class SportsProvider(ABC):
    @abstractmethod
    async def events(self, sport: str) -> list[Event]:
        raise NotImplementedError

    @abstractmethod
    async def markets(self, event_id: str) -> list[Market]:
        raise NotImplementedError
