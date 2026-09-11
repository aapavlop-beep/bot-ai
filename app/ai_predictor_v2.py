from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .models import Match
from .ai_predictor import AIPredictor


@dataclass(frozen=True)
class Recommendation:
    pick: str
    probability: float
    confidence: float
    odds: float
    fair_odds: float
    value_percent: float
    edge_percent: float
    reason: str


class Best3AIPredictor(AIPredictor):
    """AI analyst that always ranks the best three available lines."""

    def _request_v2(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        request_body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты профессиональный спортивный аналитик. Отвечай только на русском языке. "
                        "Твоя задача — не отказываться от прогноза из-за того, что рынок не даёт положительного value. "
                        "Нужно выбрать ЛУЧШИЙ прогноз из доступных линий и ещё ДВА наиболее сильных альтернативных прогноза. "
                        "Используй спортивную статистику как основу: текущий сезон, последние матчи, голы, домашний/гостевой контекст, "
                        "очные встречи, положение в таблице, составные игровые показатели и любые доступные данные матча. "
                        "Коэффициенты букмекера используй только как дополнительный сигнал и для оценки value. "
                        "Никогда не выдумывай отсутствующие факты. Если конкретной статистики нет, учитывай только реально переданные данные. "
                        "Для каждого из трёх прогнозов самостоятельно оцени вероятность исхода. "
                        "Вероятность должна быть независимой от нормализованной рыночной вероятности, но не должна быть фантазией: "
                        "не отклоняйся от рынка более чем примерно на 20 процентных пунктов без очень сильного статистического основания. "
                        "Справедливый КФ = 100 / вероятность. Value = (коэффициент * вероятность / 100 - 1) * 100. "
                        "ЛУЧШИЙ прогноз выбирай по совокупности вероятности, статистического преимущества, value и надёжности данных. "
                        "Не нужно требовать положительного value любой ценой: если все линии имеют отрицательное value, всё равно ранжируй три лучших, "
                        "но честно укажи это в reason. "
                        "Уверенность 0..10 означает уверенность именно в аналитической оценке, а не гарантию выигрыша. "
                        "Если данных достаточно, уверенность может быть 6-9. Если данных мало — не выше 4. "
                        "Верни ТОЛЬКО валидный JSON без markdown в формате: "
                        "{\"recommendations\":[{\"pick\":\"точное название линии\",\"probability\":60,\"confidence\":7.5,\"reason\":\"...\"}],\"summary\":\"...\"}. "
                        "В recommendations должно быть ровно три элемента, если доступно минимум три линии. "
                        "pick обязан дословно совпадать с одной из переданных линий."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "max_tokens": 2200,
        }

        try:
            response = self.http_client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=request_body,
            )
            if response.status_code >= 400:
                try:
                    detail = response.json()
                except ValueError:
                    detail = response.text[:1000]
                raise RuntimeError(f"AI API HTTP {response.status_code}: {detail}")
            body = response.json()
            content = body["choices"][0]["message"]["content"] or ""
        except (httpx.ConnectError, httpx.ConnectTimeout):
            payload_json = json.dumps(request_body, ensure_ascii=False)
            command = [
                "curl.exe", "-4", "-sS", "--connect-timeout", "20", "--max-time", "90",
                "-X", "POST", url,
                "-H", f"Authorization: Bearer {self.api_key}",
                "-H", "Content-Type: application/json",
                "--data-binary", payload_json,
            ]
            hostname = urlparse(url).hostname
            if hostname == "dindindon.ru":
                command[1:1] = ["--resolve", "dindindon.ru:443:217.26.24.242"]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"AI API curl error: {(result.stderr or result.stdout).strip()[:1000]}")
            body = json.loads(result.stdout)
            content = body["choices"][0]["message"]["content"] or ""

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ИИ вернул невалидный JSON: {content[:1000]}") from exc

    @staticmethod
    def _data_quality(match: Match) -> float:
        official = match.analysis_data.get("официальные_данные_khl") or {}
        quality = official.get("качество_данных") or {}
        home = int(quality.get("история_хозяев") or 0)
        away = int(quality.get("история_гостей") or 0)
        h2h = int(quality.get("h2h") or 0)
        seasonal = bool(quality.get("есть_сезонная_статистика"))
        score = 2.0
        if official.get("источник_статистики"):
            score += 2.0
        if home >= 5:
            score += 1.5
        elif home > 0:
            score += 0.7
        if away >= 5:
            score += 1.5
        elif away > 0:
            score += 0.7
        if seasonal:
            score += 1.0
        if h2h >= 3:
            score += 0.5
        return min(score, 10.0)

    def predict(self, match: Match) -> tuple[Recommendation, ...]:
        markets = [
            {
                "name": market.name,
                "odds": round(market.odds, 4),
                "market_probability": round(market.probability * 100, 2),
                "market_fair_odds": round(market.fair_odds, 4),
                "market_value_percent": round(market.value_percent, 2),
            }
            for market in match.markets
        ]
        quality = self._data_quality(match)
        payload = {
            "спорт": match.sport.value,
            "лига": match.league,
            "хозяева": match.home,
            "гости": match.away,
            "начало": match.start_time,
            "качество_данных_0_10": quality,
            "спортивный_контекст": match.analysis_data,
            "доступные_линии": markets,
            "задача": "Выбери три лучших прогноза: №1 лучший, №2 и №3 сильные альтернативы.",
        }
        data = self._request_v2(payload)
        raw_recs = data.get("recommendations") or []
        by_name = {m.name: m for m in match.markets}
        result: list[Recommendation] = []

        for raw in raw_recs:
            if not isinstance(raw, dict):
                continue
            pick = str(raw.get("pick") or "").strip()
            market = by_name.get(pick)
            if market is None or any(r.pick == pick for r in result):
                continue
            try:
                probability = float(raw.get("probability"))
                confidence = float(raw.get("confidence"))
            except (TypeError, ValueError):
                continue
            market_probability = market.probability * 100
            # Keep the model grounded in the actual market instead of allowing
            # a malformed AI response to create an absurd probability.
            probability = max(5.0, min(95.0, probability))
            probability = max(market_probability - 20.0, min(market_probability + 20.0, probability))
            confidence = max(0.0, min(10.0, confidence))
            confidence = min(confidence, quality + 1.0)
            fair_odds = 100 / probability if probability > 0 else 0.0
            value = (market.odds * probability / 100 - 1) * 100
            edge = probability - market_probability
            result.append(
                Recommendation(
                    pick=pick,
                    probability=round(probability, 1),
                    confidence=round(confidence, 1),
                    odds=market.odds,
                    fair_odds=round(fair_odds, 2),
                    value_percent=round(value, 1),
                    edge_percent=round(edge, 1),
                    reason=str(raw.get("reason") or "Оценка основана на доступной статистике и линии."),
                )
            )

        # If the model omitted some slots, fill them with the strongest market
        # candidates instead of returning an empty prediction.
        remaining = [m for m in match.markets if m.name not in {r.pick for r in result}]
        remaining.sort(key=lambda m: (m.value_percent, m.probability), reverse=True)
        for market in remaining:
            if len(result) >= min(3, len(match.markets)):
                break
            p = market.probability * 100
            result.append(
                Recommendation(
                    pick=market.name,
                    probability=round(p, 1),
                    confidence=round(min(quality, 6.0), 1),
                    odds=market.odds,
                    fair_odds=round(market.fair_odds, 2),
                    value_percent=round(market.value_percent, 1),
                    edge_percent=0.0,
                    reason="Резервный кандидат: модель не вернула отдельную оценку, поэтому использована нормализованная вероятность рынка.",
                )
            )

        result.sort(key=lambda r: (r.value_percent, r.edge_percent, r.confidence, r.probability), reverse=True)
        return tuple(result[:3])

    @staticmethod
    def format(predictions: tuple[Recommendation, ...]) -> str:
        if not predictions:
            return "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\nНет доступных линий для анализа."
        medals = ["🥇", "🥈", "🥉"]
        lines = ["\n\n🤖 <b>ЛУЧШИЕ ПРОГНОЗЫ ИИ</b>"]
        for i, rec in enumerate(predictions):
            strength = "🔥 Лучший" if i == 0 else "🟢 Сильная альтернатива"
            value_sign = "+" if rec.value_percent >= 0 else ""
            edge_sign = "+" if rec.edge_percent >= 0 else ""
            lines.append(
                f"\n{medals[i]} <b>{strength}</b>\n"
                f"🎯 <b>{rec.pick}</b>\n"
                f"💰 КФ: <b>{rec.odds:.2f}</b>\n"
                f"📊 Вероятность ИИ: <b>{rec.probability:.1f}%</b>\n"
                f"📐 Справедливый КФ ИИ: <b>{rec.fair_odds:.2f}</b>\n"
                f"📈 Value ИИ: <b>{value_sign}{rec.value_percent:.1f}%</b>\n"
                f"🔎 Отклонение от рынка: <b>{edge_sign}{rec.edge_percent:.1f} п.п.</b>\n"
                f"🧠 Уверенность: <b>{rec.confidence:.1f}/10</b>\n"
                f"Почему: {rec.reason}"
            )
        lines.append("\n⚠️ Это ранжирование аналитических кандидатов, а не гарантия результата.")
        return "\n".join(lines)
