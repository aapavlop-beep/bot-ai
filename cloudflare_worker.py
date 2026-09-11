from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
from workers import Response, WorkerEntrypoint

from app.khl_enhanced import EnhancedKHLService
from app.khl_schedule import verified_today_games
from app.providers.api_sports import ApiSportsClient


class Default(WorkerEntrypoint):
    """Cloudflare entrypoint for the Telegram sports analytics bot."""

    async def fetch(self, request):
        url = str(request.url)
        path = url.split("?", 1)[0].rstrip("/")

        if request.method == "GET" and path.endswith("/health"):
            return Response.json({"ok": True, "service": "bot-ai", "runtime": "cloudflare-python"})

        if request.method == "GET" and path.endswith("/setup"):
            return await self._setup_webhook(request)

        if request.method == "POST" and path.endswith("/telegram"):
            return await self._telegram_webhook(request)

        return Response.json({"ok": True, "service": "bot-ai"})

    async def _setup_webhook(self, request):
        setup_secret = getattr(self.env, "SETUP_SECRET", "")
        query = str(request.url).split("?", 1)[1] if "?" in str(request.url) else ""
        supplied = ""
        for item in query.split("&"):
            if item.startswith("key="):
                supplied = item[4:]
                break
        if not setup_secret or supplied != setup_secret:
            return Response.json({"ok": False, "error": "forbidden"}, status=403)

        token = getattr(self.env, "BOT_TOKEN", "")
        secret = getattr(self.env, "WEBHOOK_SECRET", "")
        if not token or not secret:
            return Response.json({"ok": False, "error": "BOT_TOKEN or WEBHOOK_SECRET is missing"}, status=500)

        base = str(request.url).split("?", 1)[0].rsplit("/", 1)[0]
        webhook_url = base + "/telegram"
        result = await self._telegram("setWebhook", {
            "url": webhook_url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        })
        return Response.json({"webhook_url": webhook_url, "telegram": result})

    async def _telegram_webhook(self, request):
        secret = getattr(self.env, "WEBHOOK_SECRET", "")
        if secret and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
            return Response.json({"ok": False, "error": "forbidden"}, status=403)

        try:
            update = await request.json()
            await self._handle_update(update)
            return Response.json({"ok": True})
        except Exception as exc:
            print(f"webhook error: {type(exc).__name__}: {exc}")
            return Response.json({"ok": False, "error": type(exc).__name__}, status=500)

    async def _telegram(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = getattr(self.env, "BOT_TOKEN", "")
        if not token:
            raise RuntimeError("BOT_TOKEN is missing")
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API: {body}")
        return body

    async def _send(self, chat_id: int, text: str, markup: dict[str, Any] | None = None):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        return await self._telegram("sendMessage", payload)

    async def _edit(self, chat_id: int, message_id: int, text: str, markup: dict[str, Any] | None = None):
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
        if markup:
            payload["reply_markup"] = markup
        return await self._telegram("editMessageText", payload)

    async def _answer_callback(self, callback_id: str):
        try:
            await self._telegram("answerCallbackQuery", {"callback_query_id": callback_id})
        except Exception as exc:
            print(f"callback answer error: {type(exc).__name__}: {exc}")

    @staticmethod
    def _menu() -> dict[str, Any]:
        return {"inline_keyboard": [
            [{"text": "🏒 КХЛ", "callback_data": "sport:khl"}],
            [{"text": "🔥 Лучшие прогнозы", "callback_data": "top"}],
            [{"text": "📊 Статистика", "callback_data": "stats"}],
        ]}

    @staticmethod
    def _khl_games_keyboard(games: list[dict[str, Any]]) -> dict[str, Any]:
        rows = []
        for index, game in enumerate(games[:15]):
            teams = game.get("teams") or {}
            home = str((teams.get("home") or {}).get("name") or "Хозяева")
            away = str((teams.get("away") or {}).get("name") or "Гости")
            game_id = game.get("id")
            if game_id is None:
                rows.append([{"text": f"⚠️ {home} — {away}", "callback_data": f"khl:missing:{index}"}])
            else:
                rows.append([{"text": f"{home} — {away}", "callback_data": f"khl:game:{game_id}"}])
        rows.append([{"text": "◀️ Главное меню", "callback_data": "menu"}])
        return {"inline_keyboard": rows}

    @staticmethod
    def _back_khl() -> dict[str, Any]:
        return {"inline_keyboard": [
            [{"text": "◀️ Матчи КХЛ", "callback_data": "sport:khl"}],
            [{"text": "🏠 Главное меню", "callback_data": "menu"}],
        ]}

    async def _handle_update(self, update: dict[str, Any]):
        message = update.get("message")
        callback = update.get("callback_query")

        if message:
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            text = str(message.get("text") or "")
            if chat_id is None:
                return
            if text.startswith("/start"):
                await self._send(chat_id, "🎯 <b>Спортивная аналитика</b>\n\n🏒 КХЛ\n\nВыбери раздел:", self._menu())
            return

        if not callback:
            return

        await self._answer_callback(str(callback.get("id")))
        data = str(callback.get("data") or "")
        cb_message = callback.get("message") or {}
        chat = cb_message.get("chat") or {}
        chat_id = chat.get("id")
        message_id = cb_message.get("message_id")
        if chat_id is None or message_id is None:
            return

        if data == "menu":
            await self._edit(chat_id, message_id, "🎯 <b>Спортивная аналитика</b>\n\n🏒 КХЛ\n\nВыбери раздел:", self._menu())
        elif data == "sport:khl":
            await self._show_khl_games(chat_id, message_id)
        elif data.startswith("khl:game:"):
            await self._show_khl_prediction(chat_id, message_id, int(data.rsplit(":", 1)[1]))
        elif data.startswith("khl:missing:"):
            await self._edit(chat_id, message_id, "⚠️ <b>Матч найден в расписании КХЛ</b>\n\nИсточник линии пока не вернул ID матча. Прогноз строить нельзя, поэтому данные не выдумываем.", self._back_khl())
        elif data == "stats":
            await self._edit(chat_id, message_id, "📊 <b>Статистика</b>\n\nХранилище D1 подключим следующим этапом. Сейчас основная задача — стабильный сбор спортивных данных и прогнозов.", self._menu())
        elif data == "top":
            await self._edit(chat_id, message_id, "🔥 <b>Лучшие прогнозы</b>\n\nАвтоматический рейтинг по всем матчам будет включён после подключения общего сканера линий.", self._menu())
        else:
            await self._edit(chat_id, message_id, "Раздел пока не настроен.", self._menu())

    async def _show_khl_games(self, chat_id: int, message_id: int):
        api_key = getattr(self.env, "API_SPORTS_KEY", "")
        if not api_key:
            await self._edit(chat_id, message_id, "⚠️ <b>API-Sports не настроен.</b>\nДобавь API_SPORTS_KEY в Cloudflare Secrets.", self._menu())
            return
        client = ApiSportsClient(api_key)
        games = await verified_today_games(client)
        if not games:
            await self._edit(chat_id, message_id, "🏒 <b>КХЛ</b>\n\nМатчи на текущую дату не найдены или источник временно недоступен.", self._menu())
            return
        schedule_only = sum(1 for game in games if game.get("__schedule_only"))
        suffix = f"\n⚠️ Без линии: {schedule_only}" if schedule_only else ""
        await self._edit(chat_id, message_id, f"🏒 <b>КХЛ</b>\n\nМатчи на сегодня: <b>{len(games)}</b>{suffix}\n\nВыбери матч:", self._khl_games_keyboard(games))

    async def _show_khl_prediction(self, chat_id: int, message_id: int, game_id: int):
        api_key = getattr(self.env, "API_SPORTS_KEY", "")
        ai_key = getattr(self.env, "OPENAI_API_KEY", "")
        if not api_key or not ai_key:
            await self._edit(chat_id, message_id, "⚠️ <b>Не настроены ключи.</b>\nНужны API_SPORTS_KEY и OPENAI_API_KEY в Cloudflare.", self._menu())
            return

        await self._edit(chat_id, message_id, "🏒 <b>Подготовка прогноза</b>\n\nПолучаю расписание, линию, форму, сезонную статистику и очные встречи...", self._back_khl())

        client = ApiSportsClient(api_key)
        service = EnhancedKHLService(client)
        games = await verified_today_games(client)
        game = next((item for item in games if int(item.get("id", -1)) == game_id), None)
        if game is None:
            await self._edit(chat_id, message_id, "Матч не найден. Обнови список матчей.", self._back_khl())
            return

        markets = await service.markets_for_game(game_id)
        analysis = await service.analysis_for_game(game)
        match = service.to_match(game, markets, analysis)
        predictions = await self._ai_rank(match)

        text = self._format_match(match, predictions)
        await self._edit(chat_id, message_id, text, self._back_khl())

    async def _ai_rank(self, match) -> list[dict[str, Any]]:
        base_url = getattr(self.env, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1"
        model = getattr(self.env, "OPENAI_MODEL", "") or "gpt-5.6"
        payload = {
            "sport": match.sport.value,
            "league": match.league,
            "home": match.home,
            "away": match.away,
            "start_time": match.start_time,
            "data_quality": self._data_quality(match),
            "sports_context": match.analysis_data,
            "markets": [
                {"name": m.name, "odds": round(m.odds, 3), "market_probability": round(m.probability * 100, 2)}
                for m in match.markets
            ],
        }
        system = (
            "Ты профессиональный спортивный аналитик. Всегда ранжируй доступные линии. "
            "Нужно вернуть ровно 3 лучших прогноза, если доступно минимум 3 линии. "
            "Не отвечай 'ставки нет' только потому, что у рынка отрицательное value. "
            "Выбирай лучший прогноз по качеству спортивных данных, вероятности, устойчивости и value. "
            "Не выдумывай факты. Вероятность должна быть независимой оценкой, но без необоснованного отклонения от рынка более чем на 20 п.п. "
            "Уверенность 0-10 отражает качество аналитической основы. "
            "Верни только JSON: {\"recommendations\":[{\"pick\":\"точное название линии\",\"probability\":60,\"confidence\":7,\"reason\":\"...\"}]}"
        )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "max_tokens": 2200,
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.env.OPENAI_API_KEY}", "Content-Type": "application/json"},
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"] or "{}"
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            parsed = json.loads(content[start:end + 1]) if start >= 0 and end > start else {}

        by_name = {m.name: m for m in match.markets}
        result = []
        for raw in parsed.get("recommendations", []):
            if not isinstance(raw, dict):
                continue
            pick = str(raw.get("pick") or "").strip()
            market = by_name.get(pick)
            if market is None or any(item["pick"] == pick for item in result):
                continue
            try:
                probability = float(raw.get("probability"))
                confidence = float(raw.get("confidence"))
            except (TypeError, ValueError):
                continue
            market_probability = market.probability * 100
            probability = max(5.0, min(95.0, probability))
            probability = max(market_probability - 20.0, min(market_probability + 20.0, probability))
            confidence = max(0.0, min(10.0, confidence))
            fair = 100 / probability
            value = (market.odds * probability / 100 - 1) * 100
            result.append({"pick": pick, "probability": probability, "confidence": confidence, "odds": market.odds, "fair": fair, "value": value, "edge": probability - market_probability, "reason": str(raw.get("reason") or "Оценка основана на переданных статистических данных.")})

        remaining = [m for m in match.markets if m.name not in {x["pick"] for x in result}]
        remaining.sort(key=lambda m: (m.value_percent, m.probability), reverse=True)
        for market in remaining:
            if len(result) >= min(3, len(match.markets)):
                break
            p = market.probability * 100
            result.append({"pick": market.name, "probability": p, "confidence": min(6.0, self._data_quality(match)), "odds": market.odds, "fair": market.fair_odds, "value": market.value_percent, "edge": 0.0, "reason": "Резервный кандидат: использована нормализованная вероятность рынка, потому что ИИ не вернул отдельную оценку для этой линии."})

        result.sort(key=lambda x: (x["value"], x["edge"], x["confidence"], x["probability"]), reverse=True)
        return result[:3]

    @staticmethod
    def _data_quality(match) -> float:
        official = match.analysis_data.get("официальные_данные_khl") or {}
        quality = official.get("качество_данных") or {}
        score = 2.0
        if official.get("источник_статистики"):
            score += 2.0
        if int(quality.get("история_хозяев") or 0) >= 5:
            score += 1.5
        if int(quality.get("история_гостей") or 0) >= 5:
            score += 1.5
        if quality.get("есть_сезонная_статистика"):
            score += 1.0
        if int(quality.get("h2h") or 0) >= 3:
            score += 0.5
        return min(score, 10.0)

    @staticmethod
    def _format_match(match, predictions: list[dict[str, Any]]) -> str:
        quality = Default._data_quality(match)
        lines = [
            f"🏒 <b>{match.home} — {match.away}</b>",
            f"🏆 {match.league}",
            f"🕒 {match.start_time}",
            f"📊 Качество спортивных данных: <b>{quality:.1f}/10</b>",
            "",
            "🤖 <b>ЛУЧШИЕ ПРОГНОЗЫ ИИ</b>",
        ]
        medals = ["🥇", "🥈", "🥉"]
        for index, item in enumerate(predictions):
            sign = "+" if item["value"] >= 0 else ""
            edge_sign = "+" if item["edge"] >= 0 else ""
            lines.extend([
                "",
                f"{medals[index]} <b>{'Лучший прогноз' if index == 0 else 'Сильная альтернатива'}</b>",
                f"🎯 <b>{item['pick']}</b>",
                f"💰 КФ: <b>{item['odds']:.2f}</b>",
                f"📊 Вероятность ИИ: <b>{item['probability']:.1f}%</b>",
                f"📐 Справедливый КФ: <b>{item['fair']:.2f}</b>",
                f"📈 Value ИИ: <b>{sign}{item['value']:.1f}%</b>",
                f"🔎 Отклонение от рынка: <b>{edge_sign}{item['edge']:.1f} п.п.</b>",
                f"🧠 Уверенность: <b>{item['confidence']:.1f}/10</b>",
                f"Почему: {item['reason']}",
            ])
        lines.append("\n⚠️ Это аналитический рейтинг, а не гарантия результата.")
        return "\n".join(lines)
