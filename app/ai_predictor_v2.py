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


def _json_safe(value, seen=None, depth: int = 0):
    """Return a JSON-safe copy and replace recursive references with None."""
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
    """AI analyst that ranks the three best available lines using real context."""

    def _request_v2(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        safe_payload = _json_safe(payload)
        # Serialize before creating the HTTP request. This makes the exact
        # failure point explicit and prevents httpx from doing hidden JSON
        # serialization of an unexpected object graph.
        try:
            user_content = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False)
            request_body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": (
                        "Ты профессиональный спортивный аналитик. Отвечай только на русском. "
                        "Твоя задача — дать три лучших прогноза по силе спортивного сценария, а не просто три самых вероятных исхода. "
                        "ОБЯЗАТЕЛЬНО сначала изучи блок спортивный_контекст. "
                        "Если активный_источник_статистики не равен 'нет', статистика реально получена и должна использоваться. "
                        "Если активный_источник_статистики равен 'API-SPORT.ru', API-SPORT.ru является ПРИОРИТЕТНЫМ источником: используй его данные как основу прогноза и не заменяй их данными HockeyTech, SofaScore или другого источника. "
                        "Опирайся на конкретные цифры: последние матчи, победы/поражения, забитые и пропущенные, средние голы, H2H, таблицу, домашний/гостевой фактор. "
                        "В первую очередь изучи поле статистика_для_ии — оно содержит выбранный основной источник. "
                        "Не пиши 'статистика отсутствует', если в контексте есть эти поля. "
                        "Рыночные вероятности и коэффициенты — только дополнительная информация; не копируй рыночную вероятность без спортивного обоснования. "
                        "Для каждой выбранной линии самостоятельно оцени вероятность. "
                        "Не выдумывай составы, травмы, форму или цифры, которых нет в контексте. "
                        "Справедливый КФ = 100 / вероятность. Value = (коэффициент * вероятность / 100 - 1) * 100. "
                        "Положительное value желательно, но не является обязательным: если его нет, всё равно выбери три наиболее сильных сценария и честно укажи отрицательное value. "
                        "Уверенность 0..10 означает качество доказательств. При реальных данных о 10 последних матчах и таблице обычно можно дать 6-9, при неполных данных ниже. "
                        "Вероятность не должна отклоняться от рынка более чем на 20 п.п., если нет сильного статистического основания. "
                        "Верни ТОЛЬКО JSON: {\"recommendations\":[{\"pick\":\"точное название линии\",\"probability\":60,\"confidence\":7.5,\"reason\":\"...\"}],\"summary\":\"...\"}. "
                        "Нужно ровно до 3 разных существующих линий, в порядке силы: №1 лучший, №2 второй, №3 третий. "
                        "pick обязан дословно совпадать с переданной линией."
                    )},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": 2400,
            }
            request_json = json.dumps(request_body, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            print(f"AI prediction serialization error: {type(exc).__name__}: {exc}", flush=True)
            raise RuntimeError(f"Не удалось подготовить данные для ИИ: {type(exc).__name__}: {exc}") from exc

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            # Send already serialized JSON so httpx cannot perform a second
            # object-graph serialization pass.
            response = self.http_client.post(url, headers=headers, content=request_json)
            if response.status_code >= 400:
                try:
                    detail = response.json()
                except ValueError:
                    detail = response.text[:1000]
                raise RuntimeError(f"AI API HTTP {response.status_code}: {detail}")
            content = response.json()["choices"][0]["message"]["content"] or ""
        except (httpx.ConnectError, httpx.ConnectTimeout):
            command = [
                "curl.exe", "-4", "-sS", "--connect-timeout", "20", "--max-time", "90",
                "-X", "POST", url,
                "-H", f"Authorization: Bearer {self.api_key}",
                "-H", "Content-Type: application/json",
                "--data-binary", request_json,
            ]
            hostname = urlparse(url).hostname
            if hostname == "dindindon.ru":
                command[1:1] = ["--resolve", "dindindon.ru:443:217.26.24.242"]
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100, check=False)
            if result.returncode != 0:
                raise RuntimeError(f"AI API curl error: {(result.stderr or result.stdout).strip()[:1000]}")
            try:
                content = json.loads(result.stdout)["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"Некорректный ответ AI API через curl: {result.stdout[:1000]}") from exc
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ИИ вернул невалидный JSON: {content[:1000]}") from exc

    @staticmethod
    def _data_quality(match: Match) -> float:
        q = match.analysis_data.get("качество_активных_данных") or {}
        home = int(q.get("история_хозяев") or 0)
        away = int(q.get("история_гостей") or 0)
        h2h = int(q.get("h2h") or 0)
        seasonal = bool(q.get("есть_сезонная_статистика") or q.get("есть_таблица"))
        source = match.analysis_data.get("активный_источник_статистики")
        score = 1.0
        if source not in (None, "нет"):
            score += 2.0
        if home >= 8:
            score += 2.0
        elif home >= 5:
            score += 1.5
        elif home > 0:
            score += 0.7
        if away >= 8:
            score += 2.0
        elif away >= 5:
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
                "name": m.name,
                "odds": round(m.odds, 4),
                "market_probability": round(m.probability * 100, 2),
                "market_fair_odds": round(m.fair_odds, 4),
                "market_value_percent": round(m.value_percent, 2),
            }
            for m in match.markets
        ]
        quality = self._data_quality(match)
        analysis_data = _json_safe(match.analysis_data)
        statistics_for_ai = _json_safe(match.analysis_data.get("статистика_для_ии", {}))
        payload = {
            "спорт": match.sport.value,
            "лига": match.league,
            "хозяева": match.home,
            "гости": match.away,
            "начало": match.start_time,
            "качество_данных_0_10": quality,
            "главный_источник_статистики": match.analysis_data.get("главный_источник_статистики", "API-SPORT.ru"),
            "активный_источник_статистики": match.analysis_data.get("активный_источник_статистики", "нет"),
            "режим_источника": match.analysis_data.get("режим_источника", ""),
            "диагностика_статистики": match.analysis_data.get("диагностика_статистики", ""),
            "статистика_для_ии": statistics_for_ai,
            "спортивный_контекст": analysis_data,
            "доступные_линии": markets,
            "задача": "Используй реальные спортивные данные, прежде всего статистику_для_ии из API-SPORT.ru, и выбери ровно три лучших сценария в порядке силы. №1 — лучший, №2 и №3 — следующие по качеству.",
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
            probability = max(5.0, min(95.0, probability))
            probability = max(market_probability - 20.0, min(market_probability + 20.0, probability))
            confidence = min(max(0.0, min(10.0, confidence)), quality)
            fair_odds = 100 / probability
            value = (market.odds * probability / 100 - 1) * 100
            edge = probability - market_probability
            result.append(
                Recommendation(
                    pick,
                    round(probability, 1),
                    round(confidence, 1),
                    market.odds,
                    round(fair_odds, 2),
                    round(value, 1),
                    round(edge, 1),
                    str(raw.get("reason") or "Оценка основана на переданных статистических данных."),
                )
            )
            if len(result) >= 3:
                break

        used = {r.pick for r in result}
        remaining = [m for m in match.markets if m.name not in used]
        remaining.sort(key=lambda m: (m.probability, m.value_percent), reverse=True)
        for market in remaining:
            if len(result) >= min(3, len(match.markets)):
                break
            p = market.probability * 100
            result.append(
                Recommendation(
                    market.name,
                    round(p, 1),
                    round(min(quality, 5.0), 1),
                    market.odds,
                    round(market.fair_odds, 2),
                    round(market.value_percent, 1),
                    0.0,
                    "Резервный кандидат: модель не вернула отдельную оценку по этой линии.",
                )
            )
        return tuple(result[:3])

    @staticmethod
    def format(predictions: tuple[Recommendation, ...]) -> str:
        if not predictions:
            return "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\nНет доступных линий для анализа."
        medals = ["🥇", "🥈", "🥉"]
        lines = ["\n\n🤖 <b>ЛУЧШИЕ ПРОГНОЗЫ ИИ</b>"]
        for i, rec in enumerate(predictions):
            strength = "🔥 Лучший" if i == 0 else "🟢 Сильная альтернатива"
            vs = "+" if rec.value_percent >= 0 else ""
            es = "+" if rec.edge_percent >= 0 else ""
            lines.append(
                f"\n{medals[i]} <b>{strength}</b>\n🎯 <b>{rec.pick}</b>\n💰 КФ: <b>{rec.odds:.2f}</b>\n"
                f"📊 Вероятность ИИ: <b>{rec.probability:.1f}%</b>\n📐 Справедливый КФ ИИ: <b>{rec.fair_odds:.2f}</b>\n"
                f"📈 Value ИИ: <b>{vs}{rec.value_percent:.1f}%</b>\n🔎 Отклонение от рынка: <b>{es}{rec.edge_percent:.1f} п.п.</b>\n"
                f"🧠 Уверенность: <b>{rec.confidence:.1f}/10</b>\nПочему: {rec.reason}"
            )
        lines.append("\n⚠️ Это ранжирование аналитических кандидатов, а не гарантия результата.")
        return "\n".join(lines)
