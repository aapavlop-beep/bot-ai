from __future__ import annotations

import json
import subprocess
from urllib.parse import urlparse

import httpx

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
        status: str = "no_bet",
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
        self.status = status


class AIPredictor:
    """Спортивный аналитик через OpenAI-совместимый Chat Completions API."""

    # Жёсткие правила финального фильтра. ИИ может найти кандидата,
    # но бот не покажет его как ставку, если он не проходит эти условия.
    MIN_BET_VALUE = 5.0
    MIN_BET_CONFIDENCE = 7.0
    MIN_BET_PROBABILITY = 55.0
    MIN_WATCH_VALUE = 2.0
    MIN_WATCH_CONFIDENCE = 6.0
    MIN_WATCH_PROBABILITY = 53.0

    def __init__(self, api_key: str | None, model: str, base_url: str | None = None) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY не указан в .env")

        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.http_client = httpx.Client(
            http2=False,
            trust_env=False,
            timeout=httpx.Timeout(90.0, connect=20.0),
        )

    def _curl_fallback(self, url: str, body: dict) -> dict:
        payload = json.dumps(body, ensure_ascii=False)
        command = [
            "curl.exe", "-4", "-sS", "--connect-timeout", "20", "--max-time", "90",
            "-X", "POST", url,
            "-H", f"Authorization: Bearer {self.api_key}",
            "-H", "Content-Type: application/json",
            "--data-binary", payload,
        ]
        hostname = urlparse(url).hostname
        if hostname == "dindindon.ru":
            command[1:1] = ["--resolve", "dindindon.ru:443:217.26.24.242"]

        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=100, check=False)
        except FileNotFoundError as exc:
            raise RuntimeError("Не найден curl.exe для резервного подключения к AI API") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Резервное подключение к AI API через curl превысило таймаут") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:1000]
            raise RuntimeError(f"AI API curl error: {detail}")
        try:
            response_body = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"AI API вернул невалидный JSON через curl: {result.stdout[:1000]}") from exc
        if isinstance(response_body, dict) and "error" in response_body:
            raise RuntimeError(f"AI API HTTP error через curl: {response_body}")
        return response_body

    def _request(self, payload: dict) -> dict:
        url = f"{self.base_url}/chat/completions"
        request_body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты автономный спортивный аналитик. Отвечай только на русском языке. "
                        "Твоя задача НЕ перечислять все возможные ставки. Найди ОДНУ лучшую линию "
                        "из доступных и оцени, заслуживает ли она внимания или реальной рекомендации. "
                        "Используй ВСЕ переданные спортивные данные: сезонную статистику, последние матчи, "
                        "результативность, домашний/гостевой контекст, очные встречи, таблицу и линии. "
                        "Никогда не выдумывай отсутствующие факты. Если данных мало, снижай уверенность. "
                        "Рыночная вероятность — только ориентир, не твоя вероятность. "
                        "Для каждой линии, которую рассматриваешь, самостоятельно оцени вероятность. "
                        "Справедливый КФ = 100 / вероятность. Value = (КФ * вероятность как доля - 1) * 100. "
                        "Выбери только одну наиболее сильную существующую линию. Не предлагай несколько ставок. "
                        "Если есть хороший кандидат, верни его в pick. Если ни одна линия не имеет реального преимущества, "
                        "верни pick=СТАВКИ НЕТ. "
                        "Статус: BET только если value >= 5%, вероятность >= 55% и уверенность >= 7/10; "
                        "WATCH если value >= 2%, вероятность >= 53% и уверенность >= 6/10; "
                        "во всех остальных случаях NO_BET. Эти пороги являются обязательными. "
                        "Не называй ставку гарантированной. Вероятность 0..100, уверенность 0..10. "
                        "Верни ТОЛЬКО валидный JSON без markdown. "
                        "Поля: pick, status, recommended, probability, confidence, reason, alternatives, caution. "
                        "status должен быть только BET, WATCH или NO_BET. alternatives обычно пустой массив."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "max_tokens": 1600,
        }

        try:
            response = self.http_client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=request_body,
            )
            if response.status_code >= 400:
                try:
                    error_body = response.json()
                except ValueError:
                    error_body = response.text[:1000]
                raise RuntimeError(f"AI API HTTP {response.status_code}: {error_body}")
            try:
                body = response.json()
                content = body["choices"][0]["message"]["content"] or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"Некорректный ответ AI API: {response.text[:1000]}") from exc
        except httpx.ConnectError:
            body = self._curl_fallback(url, request_body)
            try:
                content = body["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(f"Некорректный ответ AI API через curl: {str(body)[:1000]}") from exc

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ИИ вернул невалидный JSON: {content[:1000]}") from exc

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
            "спортивный_контекст": match.analysis_data,
            "доступные_линии": markets,
            "задача": (
                "Проанализируй доступные линии и выбери только ОДНУ лучшую. "
                "Мне нужны не все прогнозы, а только действительно сильный кандидат. "
                "Если он проходит строгие критерии BET — это рекомендация. "
                "Если он слабее, но заслуживает наблюдения — WATCH. "
                "Если сильного кандидата нет — NO_BET. "
                "Не используй рыночную вероятность как собственную оценку и не выдумывай статистику."
            ),
        }

        data = self._request(payload)
        probability = max(0.0, min(100.0, float(data["probability"])))
        confidence = max(0.0, min(10.0, float(data["confidence"])))
        pick = str(data.get("pick") or "СТАВКИ НЕТ")
        status = str(data.get("status") or "NO_BET").upper()
        if status not in {"BET", "WATCH", "NO_BET"}:
            status = "NO_BET"

        alternatives = tuple(str(item) for item in data.get("alternatives", [])[:1])
        selected = next((market for market in match.markets if market.name == pick), None)

        if selected is None:
            status = "NO_BET"
            pick = "СТАВКИ НЕТ"
            odds = fair_odds = value_percent = 0.0
        else:
            odds = selected.odds
            fair_odds = 100 / probability if probability > 0 else 0.0
            value_percent = (odds * probability / 100 - 1) * 100

            # Финальная серверная проверка. Ответ модели не может обойти пороги.
            if (
                value_percent >= self.MIN_BET_VALUE
                and probability >= self.MIN_BET_PROBABILITY
                and confidence >= self.MIN_BET_CONFIDENCE
            ):
                status = "BET"
            elif (
                value_percent >= self.MIN_WATCH_VALUE
                and probability >= self.MIN_WATCH_PROBABILITY
                and confidence >= self.MIN_WATCH_CONFIDENCE
            ):
                status = "WATCH"
            else:
                status = "NO_BET"

            if status == "NO_BET":
                pick = "СТАВКИ НЕТ"
                odds = fair_odds = value_percent = 0.0

        recommended = status == "BET"

        return AIPrediction(
            pick=pick,
            probability=probability,
            confidence=confidence,
            reason=str(data.get("reason") or "Недостаточно данных для надёжной рекомендации."),
            alternatives=alternatives if status != "BET" else (),
            caution=str(data.get("caution") or "Оценка не является гарантией результата."),
            odds=odds,
            fair_odds=fair_odds,
            value_percent=value_percent,
            recommended=recommended,
            status=status,
        )

    @staticmethod
    def format(prediction: AIPrediction) -> str:
        if prediction.status == "BET":
            header = "🟢 <b>СТАВКА — ЛУЧШИЙ СИГНАЛ</b>"
            pick = prediction.pick
            value_line = f"📈 <b>Value ИИ:</b> {prediction.value_percent:+.1f}%"
            odds_line = f"💰 <b>Коэффициент:</b> {prediction.odds:.2f}\n📐 <b>Справедливый КФ ИИ:</b> {prediction.fair_odds:.2f}"
        elif prediction.status == "WATCH":
            header = "🟡 <b>НАБЛЮДЕНИЕ — СИЛЬНЫЙ КАНДИДАТ</b>"
            pick = prediction.pick
            value_line = f"📈 <b>Value ИИ:</b> {prediction.value_percent:+.1f}%"
            odds_line = f"💰 <b>Коэффициент:</b> {prediction.odds:.2f}\n📐 <b>Справедливый КФ ИИ:</b> {prediction.fair_odds:.2f}"
        else:
            header = "⚪ <b>СИЛЬНОГО СИГНАЛА НЕТ</b>"
            pick = "Нет линии, которую модель считает достаточно сильной"
            value_line = "📈 <b>Value:</b> ниже установленного порога"
            odds_line = ""

        return (
            "\n\n🤖 <b>ПРОГНОЗ ИИ</b>\n\n"
            f"{header}\n🎯 <b>Выбор:</b> {pick}\n"
            f"📊 <b>Вероятность ИИ:</b> {prediction.probability:.1f}%\n"
            f"🧠 <b>Уверенность:</b> {prediction.confidence:.1f}/10\n"
            f"{odds_line}\n{value_line}\n\n"
            f"<b>Почему:</b> {prediction.reason}\n\n"
            f"⚠️ <i>{prediction.caution}</i>"
        )
