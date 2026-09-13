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
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if depth > 8:
        return None
    if seen is None:
        seen = set()
    oid = id(value)
    if oid in seen:
        return None
    if isinstance(value, dict):
        seen.add(oid)
        result = {str(k): _json_safe(v, seen, depth + 1) for k, v in value.items()}
        seen.remove(oid)
        return result
    if isinstance(value, (list, tuple)):
        seen.add(oid)
        result = [_json_safe(v, seen, depth + 1) for v in value]
        seen.remove(oid)
        return result
    return str(value)


class Best3AIPredictor(AIPredictor):
    """Evidence-first analyst: return only lines worth considering.

    The model may return zero, one, two or three recommendations. There is no
    fallback that fabricates a recommendation merely to fill a top-3 list.
    """

    SYSTEM_PROMPT = (
        "Ты профессиональный спортивный аналитик. Отвечай только на русском. "
        "Твоя задача — найти только ставки, на которые действительно стоит обратить внимание. "
        "НЕ нужно выдавать прогноз на каждый матч и НЕ нужно заполнять список до трёх позиций. "
        "Если доказательств недостаточно или value отсутствует, верни пустой список. "
        "Сначала изучи спортивный_контекст и статистика_для_ии, затем веб-исследование. "
        "В веб-исследовании используй только подтверждённые факты: состав, травма, дисквалификация, вратарь, форма, H2H, таблица, домашний/гостевой фактор, нагрузка, перелёты и свежие новости. "
        "Не делай вывод 'травмирован', если источник только показывает отсутствие игрока. "
        "Для свежих кадровых новостей приоритет: официальный KHL/клуб, затем крупное спортивное СМИ. "
        "Учитывай дату публикации и не используй старую новость как подтверждение текущего состава без оговорки. "
        "Коэффициент допустим только если он есть среди переданных доступных_линий; веб-упоминание коэффициента само по себе не создаёт линию. "
        "Не выдумывай коэффициенты, игроков, травмы, составы, статистику или результаты. "
        "Оцени вероятность самостоятельно, но не отклоняйся от рыночной вероятности более чем на 20 процентных пунктов без очень сильных подтверждённых оснований. "
        "Справедливый КФ = 100 / вероятность. Value = (коэффициент * вероятность / 100 - 1) * 100. "
        "Сильный кандидат обычно должен иметь положительное value и уверенность не ниже 6/10. "
        "Если есть только слабое преимущество, не рекомендуй ставку. "
        "Верни ТОЛЬКО JSON формата: {\"recommendations\":[{\"pick\":\"точное название существующей линии\",\"probability\":60,\"confidence\":7.0,\"reason\":\"...\"}],\"summary\":\"...\"}. "
        "Отсортируй рекомендации от лучшей к худшей. Допускается 0-3 рекомендации. pick обязан дословно совпадать с переданной линией."
    )

    def _request(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        safe_payload = _json_safe(payload)
        user_content = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False)
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 2800,
        }
        request_json = json.dumps(request_body, ensure_ascii=False, allow_nan=False)
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
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
                raise RuntimeError("Некорректный ответ AI API через curl") from exc
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
        web = match.analysis_data.get("веб_исследование") or {}
        sources = int(web.get("источников_всего") or len(web.get("источники") or []))
        official = sum(1 for s in web.get("источники") or [] if s.get("тип_источника") == "official")
        score = 1.0
        if match.analysis_data.get("активный_источник_статистики") not in (None, "нет"):
            score += 2.0
        if home >= 8:
            score += 1.5
        elif home >= 5:
            score += 1.0
        elif home > 0:
            score += 0.5
        if away >= 8:
            score += 1.5
        elif away >= 5:
            score += 1.0
        elif away > 0:
            score += 0.5
        if seasonal:
            score += 1.0
        if h2h >= 3:
            score += 0.5
        if sources >= 8:
            score += 1.0
        if official:
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
        payload = {
            "спорт": match.sport.value,
            "лига": match.league,
            "хозяева": match.home,
            "гости": match.away,
            "начало": match.start_time,
            "качество_данных_0_10": quality,
            "активный_источник_статистики": match.analysis_data.get("активный_источник_статистики", "нет"),
            "режим_источника": match.analysis_data.get("режим_источника", ""),
            "статистика_для_ии": _json_safe(match.analysis_data.get("статистика_для_ии", {})),
            "веб_исследование": _json_safe(match.analysis_data.get("веб_исследование", {})),
            "спортивный_контекст": analysis_data,
            "доступные_линии": markets,
            "задача": "Найди только действительно сильные ставки. Если нет достаточного преимущества — recommendations=[]; не заполняй список искусственно.",
        }
        data = self._request(payload)
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
            # Hard post-filter: no weak candidate gets shown just because the
            # model returned it. This is the final gate for "worth attention".
            if confidence < 6.0 or value < 2.0 or edge < 1.0:
                continue
            result.append(Recommendation(
                pick,
                round(probability, 1),
                round(confidence, 1),
                market.odds,
                round(fair_odds, 2),
                round(value, 1),
                round(edge, 1),
                str(raw.get("reason") or "Подтверждённый статистический перевес."),
            ))
            if len(result) >= 3:
                break
        return tuple(result)

    @staticmethod
    def format(predictions: tuple[Recommendation, ...]) -> str:
        if not predictions:
            return (
                "\n\n🤖 <b>ЛУЧШИЕ ПРОГНОЗЫ ИИ</b>\n\n"
                "Сегодня по этому матчу нет ставки, которая одновременно проходит фильтр качества данных и имеет достаточный перевес над линией."
            )
        medals = ["🥇", "🥈", "🥉"]
        lines = ["\n\n🤖 <b>ЛУЧШИЕ ПРОГНОЗЫ ИИ</b>"]
        for i, rec in enumerate(predictions):
            strength = "🔥 Лучшая ставка" if i == 0 else "🟢 Сильная альтернатива"
            lines.append(
                f"\n{medals[i]} <b>{strength}</b>\n"
                f"🎯 <b>{rec.pick}</b>\n"
                f"💰 КФ: <b>{rec.odds:.2f}</b>\n"
                f"📊 Вероятность ИИ: <b>{rec.probability:.1f}%</b>\n"
                f"📐 Справедливый КФ: <b>{rec.fair_odds:.2f}</b>\n"
                f"📈 Value: <b>+{rec.value_percent:.1f}%</b>\n"
                f"🔎 Перевес над рынком: <b>+{rec.edge_percent:.1f} п.п.</b>\n"
                f"🧠 Уверенность: <b>{rec.confidence:.1f}/10</b>\n"
                f"Почему: {rec.reason}"
            )
        lines.append("\n⚠️ Ранжирование основано на доступных подтверждённых данных; результат матча не гарантирован.")
        return "\n".join(lines)
