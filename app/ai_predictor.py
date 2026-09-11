from __future__ import annotations

import json

import httpx
from openai import OpenAI

from .models import Match


class AIPrediction:
    def __init__(
        self,
        pick: str,
        probability: float,
        confidence: float,
        reason: str,
        alternatives: tuple[str, ...],
        caution: str,
        odds: float = 0.0,
        fair_odds: float = 0.0,
        value_percent: float = 0.0,
        recommended: bool = False,
    ) -> None:
        self.pick = pick
        self.probability = probability
        self.confidence = confidence
        self.reason = reason
        self.alternatives = alternatives
        self.caution = caution
        self.odds = odds
        self.fair_odds = fair_odds
        self.value_percent = value_percent
        self.recommended = recommended


class AIPredictor:
    """Спортивный аналитик через OpenAI-совместимый Chat Completions API."""

    def __init__(self, api_key: str | None, model: str, base_url: str | None = None) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY не указан в .env")

        # Для dindindon используем обычный HTTP/1.1-клиент без системного proxy.
        # Curl к тому же endpoint работает, поэтому отключаем возможное влияние
        # HTTP(S)_PROXY из окружения Windows и не используем HTTP/2.
        http_client = httpx.Client(
            http2=False,
            trust_env=False,
            timeout=httpx.Timeout(90.0, connect=20.0),
        )
        client_kwargs = {
            "api_key": api_key,
            "http_client": http_client,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)
        self.model = model

    def _request(self, payload: dict) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты автономный спортивный аналитик и модель оценки вероятностей. "
                        "Отвечай только на русском языке. Анализируй только переданные данные. "
                        "Никогда не выдумывай форму команд, травмы, составы, очные встречи, новости "
                        "или другую статистику, которой нет во входных данных. "
                        "Для каждой выбранной линии сначала оцени истинную вероятность исхода САМОСТОЯТЕЛЬНО, "
                        "а не копируй market_probability. Затем сравни свою вероятность с коэффициентом. "
                        "Справедливый коэффициент = 100 / твоя вероятность в процентах. "
                        "Value = (коэффициент * твоя вероятность как доля) - 1, в процентах. "
                        "Выбирай только существующую линию из доступных_линий и возвращай ее точное имя. "
                        "Если ни одна линия не имеет положительного и достаточно надежного value, "
                        "не заставляй себя выбирать ставку: recommended=false и pick=СТАВКИ НЕТ. "
                        "Отрицательное value не является выгодной ставкой. "
                        "Не называй ставку гарантированной. Вероятность всегда должна быть от 0 до 100, "
                        "уверенность от 0 до 10. Не используй 90%+ без исключительно сильных оснований. "
                        "При отсутствии спортивной статистики снижай уверенность и явно указывай ограничение данных. "
                        "Верни ТОЛЬКО валидный JSON без markdown и без пояснений вне JSON. "
                        "Поля JSON: pick (string), recommended (boolean), probability (number), "
                        "confidence (number), reason (string), alternatives (array из строк, максимум 3), "
                        "caution (string)."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            temperature=0.2,
            max_tokens=1400,
        )

        content = response.choices[0].message.content or ""
        return json.loads(content)

    def predict(self, match: Match) -> AIPrediction:
        markets = [
            {
                "name": market.name,
                "odds": round(market.odds, 4),
                "market_probability": round(market.probability * 100, 2),
                "fair_odds_market": round(market.fair_odds, 4),
                "market_value_percent": round(market.value_percent, 2),
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
                "Самостоятельно оцени вероятность исходов доступных линий. "
                "Не копируй рыночную вероятность. Сравни свою оценку с коэффициентами, "
                "выбери одну лучшую линию только если она действительно имеет положительное value. "
                "Для основной линии укажи точное имя из доступных_линий. "
                "Если преимущества нет, верни recommended=false и pick=СТАВКИ НЕТ. "
                "Альтернативы также должны быть только из доступных линий."
            ),
        }

        data = self._request(payload)
        probability = max(0.0, min(100.0, float(data["probability"])))
        confidence = max(0.0, min(10.0, float(data["confidence"])))
        recommended = bool(data["recommended"])
        pick = str(data["pick"])
        alternatives = tuple(str(item) for item in data.get("alternatives", [])[:3])

        selected = next((market for market in match.markets if market.name == pick), None)

        if selected is None:
            recommended = False
            pick = "СТАВКИ НЕТ"
            odds = 0.0
            fair_odds = 0.0
            value_percent = 0.0
        else:
            odds = selected.odds
            fair_odds = 100 / probability if probability > 0 else 0.0
            value_percent = (odds * probability / 100 - 1) * 100

            if value_percent <= 0:
                recommended = False

        return AIPrediction(
            pick=pick,
            probability=probability,
            confidence=confidence,
            reason=str(data["reason"]),
            alternatives=alternatives,
            caution=str(data["caution"]),
            odds=odds,
            fair_odds=fair_odds,
            value_percent=value_percent,
            recommended=recommended,
        )

    @staticmethod
    def format(prediction: AIPrediction) -> str:
        alternatives = "\n".join(f"• {item}" for item in prediction.alternatives) or "• Нет подходящих альтернатив"

        if prediction.recommended:
            header = "🎯 <b>ОСНОВНОЙ ПРОГНОЗ</b>"
            pick = prediction.pick
            value_line = f"📈 <b>Value ИИ:</b> {prediction.value_percent:+.1f}%"
            odds_line = (
                f"💰 <b>Коэффициент:</b> {prediction.odds:.2f}\n"
                f"📐 <b>Справедливый КФ ИИ:</b> {prediction.fair_odds:.2f}"
            )
        else:
            header = "🚫 <b>СТАВКИ НЕТ</b>"
            pick = "Нет линии с подтвержденным положительным преимуществом"
            value_line = "📈 <b>Value:</b> недостаточно для рекомендации"
            odds_line = ""

        return (
            "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\n"
            f"{header}\n"
            f"🎯 <b>Выбор:</b> {pick}\n"
            f"📊 <b>Вероятность ИИ:</b> {prediction.probability:.1f}%\n"
            f"🧠 <b>Уверенность:</b> {prediction.confidence:.1f}/10\n"
            f"{odds_line}\n"
            f"{value_line}\n\n"
            f"<b>Обоснование:</b> {prediction.reason}\n\n"
            f"<b>Альтернативы:</b>\n{alternatives}\n\n"
            f"⚠️ <i>{prediction.caution}</i>"
        )
