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
    """AI analyst that ranks three best available lines using real context."""

    def _request_v2(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "Ты профессиональный спортивный аналитик. Отвечай только на русском. "
                    "Выбери лучший прогноз и еще два сильных альтернативных прогноза. "
                    "Основывай вероятность прежде всего на переданной статистике: последние матчи, голы, домашний/гостевой контекст, H2H, таблица и составы. "
                    "Если поле активный_источник_статистики не равно 'нет', считай переданный спортивный контекст реальными полученными данными и используй конкретные цифры из него. "
                    "Не пиши, что статистика отсутствует, если в контексте есть последние_10, форма_10, H2H или таблица. "
                    "Коэффициент используй как дополнительный сигнал. Не выдумывай отсутствующие факты. "
                    "Вероятность должна быть независимой от рыночной, но без необоснованного отклонения более чем на 20 п.п. "
                    "Справедливый КФ = 100 / вероятность. Value = (коэффициент * вероятность / 100 - 1) * 100. "
                    "Даже если Value отрицательный, все равно ранжируй три лучших линии и честно укажи отсутствие value. "
                    "Уверенность 0..10 означает надежность именно аналитической оценки. При хороших данных 6-9, при плохих не выше 4. "
                    "Верни только JSON: {\"recommendations\":[{\"pick\":\"точное название линии\",\"probability\":60,\"confidence\":7.5,\"reason\":\"...\"}],\"summary\":\"...\"}. "
                    "pick обязан дословно совпадать с переданной линией."
                )},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "max_tokens": 2200,
        }
        try:
            response = self.http_client.post(url, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=request_body)
            if response.status_code >= 400:
                try: detail = response.json()
                except ValueError: detail = response.text[:1000]
                raise RuntimeError(f"AI API HTTP {response.status_code}: {detail}")
            content = response.json()["choices"][0]["message"]["content"] or ""
        except (httpx.ConnectError, httpx.ConnectTimeout):
            payload_json = json.dumps(request_body, ensure_ascii=False)
            command = ["curl.exe", "-4", "-sS", "--connect-timeout", "20", "--max-time", "90", "-X", "POST", url, "-H", f"Authorization: Bearer {self.api_key}", "-H", "Content-Type: application/json", "--data-binary", payload_json]
            hostname = urlparse(url).hostname
            if hostname == "dindindon.ru": command[1:1] = ["--resolve", "dindindon.ru:443:217.26.24.242"]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100, check=False)
            if result.returncode != 0: raise RuntimeError(f"AI API curl error: {(result.stderr or result.stdout).strip()[:1000]}")
            content = json.loads(result.stdout)["choices"][0]["message"]["content"] or ""
        try: return json.loads(content)
        except json.JSONDecodeError as exc: raise RuntimeError(f"ИИ вернул невалидный JSON: {content[:1000]}") from exc

    @staticmethod
    def _data_quality(match: Match) -> float:
        official = match.analysis_data.get("официальные_данные_khl") or {}
        active = match.analysis_data.get("резервные_данные_khl") or {}
        explicit = match.analysis_data.get("качество_активных_данных") or {}
        data = official if Best3AIPredictor._quality_count(official) >= Best3AIPredictor._quality_count(active) else active
        quality = explicit or data.get("качество_данных") or {}
        home = int(quality.get("история_хозяев") or 0)
        away = int(quality.get("история_гостей") or 0)
        h2h = int(quality.get("h2h") or 0)
        seasonal = bool(quality.get("есть_сезонная_статистика") or quality.get("есть_таблица"))
        score = 2.0
        if match.analysis_data.get("активный_источник_статистики") not in (None, "нет"): score += 2.0
        if home >= 5: score += 1.5
        elif home > 0: score += 0.7
        if away >= 5: score += 1.5
        elif away > 0: score += 0.7
        if seasonal: score += 1.0
        if h2h >= 3: score += 0.5
        return min(score, 10.0)

    @staticmethod
    def _quality_count(data: dict) -> int:
        q = data.get("качество_данных") or {}
        return int(q.get("история_хозяев") or 0) + int(q.get("история_гостей") or 0) + int(q.get("h2h") or 0)

    def predict(self, match: Match) -> tuple[Recommendation, ...]:
        markets = [{"name": m.name, "odds": round(m.odds,4), "market_probability": round(m.probability*100,2), "market_fair_odds": round(m.fair_odds,4), "market_value_percent": round(m.value_percent,2)} for m in match.markets]
        quality = self._data_quality(match)
        payload = {"спорт": match.sport.value, "лига": match.league, "хозяева": match.home, "гости": match.away, "начало": match.start_time, "качество_данных_0_10": quality, "активный_источник_статистики": match.analysis_data.get("активный_источник_статистики", "нет"), "диагностика_статистики": match.analysis_data.get("диагностика_статистики", ""), "спортивный_контекст": match.analysis_data, "доступные_линии": markets, "задача": "Выбери три лучших прогноза: №1 лучший, №2 и №3 альтернативы."}
        data = self._request_v2(payload)
        raw_recs = data.get("recommendations") or []
        by_name = {m.name: m for m in match.markets}
        result: list[Recommendation] = []
        for raw in raw_recs:
            if not isinstance(raw, dict): continue
            pick = str(raw.get("pick") or "").strip(); market = by_name.get(pick)
            if market is None or any(r.pick == pick for r in result): continue
            try: probability, confidence = float(raw.get("probability")), float(raw.get("confidence"))
            except (TypeError, ValueError): continue
            market_probability = market.probability * 100
            probability = max(5.0, min(95.0, probability)); probability = max(market_probability-20.0, min(market_probability+20.0, probability))
            confidence = min(max(0.0, min(10.0, confidence)), quality + 1.0)
            fair_odds = 100 / probability; value = (market.odds * probability / 100 - 1) * 100; edge = probability - market_probability
            result.append(Recommendation(pick, round(probability,1), round(confidence,1), market.odds, round(fair_odds,2), round(value,1), round(edge,1), str(raw.get("reason") or "Оценка основана на переданных статистических данных.")))
        remaining = [m for m in match.markets if m.name not in {r.pick for r in result}]
        remaining.sort(key=lambda m: (m.value_percent, m.probability), reverse=True)
        for market in remaining:
            if len(result) >= min(3, len(match.markets)): break
            p = market.probability * 100
            result.append(Recommendation(market.name, round(p,1), round(min(quality,6.0),1), market.odds, round(market.fair_odds,2), round(market.value_percent,1), 0.0, "Резервный кандидат: модель не вернула отдельную оценку."))
        result.sort(key=lambda r: (r.value_percent, r.edge_percent, r.confidence, r.probability), reverse=True)
        return tuple(result[:3])

    @staticmethod
    def format(predictions: tuple[Recommendation, ...]) -> str:
        if not predictions: return "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\nНет доступных линий для анализа."
        medals = ["🥇","🥈","🥉"]; lines = ["\n\n🤖 <b>ЛУЧШИЕ ПРОГНОЗЫ ИИ</b>"]
        for i, rec in enumerate(predictions):
            strength = "🔥 Лучший" if i == 0 else "🟢 Сильная альтернатива"; vs = "+" if rec.value_percent >= 0 else ""; es = "+" if rec.edge_percent >= 0 else ""
            lines.append(f"\n{medals[i]} <b>{strength}</b>\n🎯 <b>{rec.pick}</b>\n💰 КФ: <b>{rec.odds:.2f}</b>\n📊 Вероятность ИИ: <b>{rec.probability:.1f}%</b>\n📐 Справедливый КФ ИИ: <b>{rec.fair_odds:.2f}</b>\n📈 Value ИИ: <b>{vs}{rec.value_percent:.1f}%</b>\n🔎 Отклонение от рынка: <b>{es}{rec.edge_percent:.1f} п.п.</b>\n🧠 Уверенность: <b>{rec.confidence:.1f}/10</b>\nПочему: {rec.reason}")
        lines.append("\n⚠️ Это ранжирование аналитических кандидатов, а не гарантия результата.")
        return "\n".join(lines)
