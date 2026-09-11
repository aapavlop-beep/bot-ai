from __future__ import annotations

import json
from dataclasses import dataclass

from openai import OpenAI

from .models import Match


@dataclass(frozen=True)
class AIPrediction:
    pick: str
    probability: float
    confidence: float
    reason: str
    alternatives: tuple[str, ...]
    caution: str


class AIPredictor:
    """AI analyst that turns supplied match data into a structured forecast.

    The model is explicitly forbidden from inventing statistics. It may only
    use the facts supplied in the prompt. The numeric probability is therefore
    an AI estimate based on the available evidence, not a guaranteed result.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def predict(self, match: Match) -> AIPrediction:
        markets = [
            {
                "name": market.name,
                "odds": round(market.odds, 4),
                "market_probability": round(market.probability * 100, 2),
                "fair_odds": round(market.fair_odds, 4),
                "value_percent": round(market.value_percent, 2),
            }
            for market in match.markets
        ]

        payload = {
            "sport": match.sport.value,
            "league": match.league,
            "home": match.home,
            "away": match.away,
            "start_time": match.start_time,
            "markets": markets,
        }

        response = self.client.responses.create(
            model=self.model,
            instructions=(
                "Ты спортивный аналитик. Составь осторожный прогноз только по "
                "данным, которые переданы во входе. Не выдумывай травмы, форму, "
                "составы, статистику, новости или факты, которых нет во входе. "
                "Если данных мало, снижай confidence. Вероятность — твоя оценка "
                "вероятности выбранной ставки в процентах, а не вероятность "
                "гарантированного выигрыша. Выбери только одну основную ставку. "
                "Не называй ставку гарантированной и не используй 90%+ без очень "
                "сильных оснований. Ответ строго по JSON-схеме."
            ),
            input=json.dumps(payload, ensure_ascii=False),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "sports_prediction",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "pick": {"type": "string"},
                            "probability": {"type": "number", "minimum": 0, "maximum": 100},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 10},
                            "reason": {"type": "string"},
                            "alternatives": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 3,
                            },
                            "caution": {"type": "string"},
                        },
                        "required": [
                            "pick",
                            "probability",
                            "confidence",
                            "reason",
                            "alternatives",
                            "caution",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
        )

        data = json.loads(response.output_text)
        return AIPrediction(
            pick=str(data["pick"]),
            probability=float(data["probability"]),
            confidence=float(data["confidence"]),
            reason=str(data["reason"]),
            alternatives=tuple(str(item) for item in data["alternatives"]),
            caution=str(data["caution"]),
        )

    @staticmethod
    def format(prediction: AIPrediction) -> str:
        alternatives = "\n".join(f"• {item}" for item in prediction.alternatives) or "• Нет"
        return (
            "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\n"
            f"🎯 <b>Основная ставка:</b> {prediction.pick}\n"
            f"📊 <b>Вероятность:</b> {prediction.probability:.1f}%\n"
            f"🧠 <b>Уверенность модели:</b> {prediction.confidence:.1f}/10\n\n"
            f"<b>Почему:</b> {prediction.reason}\n\n"
            f"<b>Альтернативы:</b>\n{alternatives}\n\n"
            f"⚠️ <i>{prediction.caution}</i>"
        )
