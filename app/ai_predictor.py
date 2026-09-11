from __future__ import annotations

import json

from openai import OpenAI

from .models import Match


class AIPrediction:
    def __init__(self, pick: str, probability: float, confidence: float, reason: str, alternatives: tuple[str, ...], caution: str) -> None:
        self.pick = pick
        self.probability = probability
        self.confidence = confidence
        self.reason = reason
        self.alternatives = alternatives
        self.caution = caution


class AIPredictor:
    """Спортивный аналитик через официальный OpenAI Responses API."""

    def __init__(self, api_key: str | None, model: str) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY не указан в .env")
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def _request(self, payload: dict) -> dict:
        response = self.client.responses.create(
            model=self.model,
            reasoning={"effort": "low"},
            instructions=(
                "Ты автономный спортивный аналитик. Отвечай только на русском языке. "
                "Анализируй только переданные данные и не выдумывай факты. "
                "Выбери одну основную ставку только из доступных линий. "
                "Вероятность — оценка, а не гарантия. Не используй 90%+ без исключительных оснований. "
                "Если данных недостаточно, снижай уверенность. Никогда не называй ставку гарантированной."
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
                            "probability": {"type": "number"},
                            "confidence": {"type": "number"},
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
            max_output_tokens=1200,
            store=False,
        )
        return json.loads(response.output_text)

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
            "спорт": match.sport.value,
            "лига": match.league,
            "хозяева": match.home,
            "гости": match.away,
            "начало": match.start_time,
            "доступные_линии": markets,
            "задача": (
                "Выбери лучшую доступную линию для прогноза. "
                "Укажи вероятность в процентах, уверенность от 0 до 10, "
                "краткое обоснование, до 3 альтернатив и предупреждение о риске."
            ),
        }

        data = self._request(payload)
        probability = max(0.0, min(100.0, float(data["probability"])))
        confidence = max(0.0, min(10.0, float(data["confidence"])))
        alternatives = tuple(str(item) for item in data.get("alternatives", [])[:3])

        return AIPrediction(
            pick=str(data["pick"]),
            probability=probability,
            confidence=confidence,
            reason=str(data["reason"]),
            alternatives=alternatives,
            caution=str(data["caution"]),
        )

    @staticmethod
    def format(prediction: AIPrediction) -> str:
        alternatives = "\n".join(f"• {item}" for item in prediction.alternatives) or "• Нет подходящих альтернатив"
        return (
            "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\n"
            f"🎯 <b>Основная ставка:</b> {prediction.pick}\n"
            f"📊 <b>Вероятность:</b> {prediction.probability:.1f}%\n"
            f"🧠 <b>Уверенность:</b> {prediction.confidence:.1f}/10\n\n"
            f"<b>Обоснование:</b> {prediction.reason}\n\n"
            f"<b>Альтернативы:</b>\n{alternatives}\n\n"
            f"⚠️ <i>{prediction.caution}</i>"
        )
