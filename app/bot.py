from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .ai_predictor_v2 import Best3AIPredictor
from .config import settings
from .khl_enhanced import EnhancedKHLService
from .khl_schedule import verified_today_games
from .keyboards import main_menu
from .providers.api_sports import ApiSportsClient, ApiSportsError
from .storage import PredictionStore


dp = Dispatcher()
store = PredictionStore(settings.database_path)
khl = EnhancedKHLService(ApiSportsClient(settings.api_sports_key)) if settings.api_sports_key else None
ai_predictor = Best3AIPredictor(settings.openai_api_key, settings.openai_model, settings.openai_base_url) if settings.openai_api_key else None


def khl_games_keyboard(games: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, game in enumerate(games[:15]):
        game_id = game.get("id")
        teams = game.get("teams") or {}
        home = (teams.get("home") or {}).get("name") or "Хозяева"
        away = (teams.get("away") or {}).get("name") or "Гости"
        if game_id is not None:
            rows.append([InlineKeyboardButton(text=f"{home} — {away}", callback_data=f"khl:game:{game_id}")])
        else:
            rows.append([InlineKeyboardButton(text=f"⚠️ {home} — {away}", callback_data=f"khl:missing:{index}")])
    rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_khl_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Матчи КХЛ", callback_data="sport:khl")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
        ]
    )


async def safe_edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        if callback.message:
            await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def safe_status(callback: CallbackQuery, text: str) -> None:
    try:
        if callback.message:
            await callback.message.edit_text(text)
    except TelegramBadRequest:
        pass


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "🎯 <b>Спортивная аналитика</b>\n\n"
        "🏒 КХЛ • ⚽ Футбол • 🎮 CS2\n\n"
        "Выбери раздел:",
        reply_markup=main_menu(),
    )


@dp.callback_query()
async def callbacks(callback: CallbackQuery) -> None:
    data = callback.data or ""

    try:
        await callback.answer()

        if data == "menu":
            text = "🎯 <b>Спортивная аналитика</b>\n\n🏒 КХЛ • ⚽ Футбол • 🎮 CS2\n\nВыбери раздел:"
            markup = main_menu()

        elif data == "stats":
            stats = store.stats()
            text = (
                "📊 <b>Статистика модели</b>\n\n"
                f"Прогнозов: {stats['total']}\n"
                f"Зашло: {stats['won']}\n"
                f"Не зашло: {stats['lost']}\n"
                f"Проходимость: {stats['hit_rate']:.1f}%"
            )
            markup = main_menu()

        elif data == "about":
            text = (
                "ℹ️ <b>О боте</b>\n\n"
                "Бот собирает спортивные данные, рассчитывает вероятности "
                "и использует ИИ для итогового анализа.\n\n"
                "Сейчас запускаем первый полноценный раздел — КХЛ."
            )
            markup = main_menu()

        elif data == "top":
            text = (
                "🔥 <b>Лучшие прогнозы</b>\n\n"
                "После обновления аналитики здесь будут отображаться лучшие кандидаты по всем матчам."
            )
            markup = main_menu()

        elif data == "sport:khl":
            if khl is None:
                text = "🏒 <b>КХЛ</b>\n\nНе указан API_SPORTS_KEY в локальном .env."
                markup = main_menu()
            else:
                games = await verified_today_games(khl.client)
                if not games:
                    text = "🏒 <b>КХЛ</b>\n\nНа текущую дату матчи КХЛ не найдены или источники временно недоступны."
                    markup = main_menu()
                else:
                    schedule_only = sum(1 for game in games if game.get("__schedule_only"))
                    suffix = f"\n⚠️ Без линии API-Sports: {schedule_only}" if schedule_only else ""
                    text = f"🏒 <b>КХЛ</b>\n\nМатчи на сегодня: {len(games)}{suffix}\n\nВыбери матч:"
                    markup = khl_games_keyboard(games)

        elif data.startswith("khl:missing:"):
            text = (
                "⚠️ <b>Матч найден в официальном расписании КХЛ</b>\n\n"
                "Но API-Sports сейчас не вернул для него событие с ID, поэтому линию и прогноз ИИ получить нельзя.\n\n"
                "Матч не удаляем из расписания — ждём, пока источник синхронизирует событие."
            )
            markup = back_khl_keyboard()

        elif data.startswith("khl:game:"):
            if khl is None:
                text = "Не указан API_SPORTS_KEY в локальном .env."
                markup = main_menu()
            elif ai_predictor is None:
                text = "⚠️ <b>ИИ не настроен.</b>\n\nДобавь OPENAI_API_KEY в локальный .env и перезапусти бота."
                markup = main_menu()
            else:
                game_id = int(data.rsplit(":", 1)[1])
                await safe_status(callback, "🏒 <b>Подготовка прогноза</b>\n\n1/5 Получаю данные матча и линию...")

                games = await verified_today_games(khl.client)
                game = next((item for item in games if int(item.get("id", -1)) == game_id), None)
                if game is None:
                    text = "Матч не найден. Обнови список матчей КХЛ."
                    markup = back_khl_keyboard()
                else:
                    await safe_status(callback, "🏒 <b>Подготовка прогноза</b>\n\n2/5 Получаю все доступные линии...")
                    markets = await khl.markets_for_game(game_id)

                    await safe_status(callback, "🏒 <b>Подготовка прогноза</b>\n\n3/5 Получаю официальную статистику КХЛ, форму и очные встречи...")
                    analysis_data = await khl.analysis_for_game(game)
                    match = khl.to_match(game, markets, analysis_data)

                    await safe_status(
                        callback,
                        f"🏒 <b>{match.home} — {match.away}</b>\n\n"
                        f"4/5 Линий получено: <b>{len(match.markets)}</b>\n"
                        "🤖 ИИ выбирает лучший прогноз и две сильные альтернативы...",
                    )

                    try:
                        predictions = await asyncio.wait_for(
                            asyncio.to_thread(ai_predictor.predict, match),
                            timeout=90.0,
                        )
                        text = khl.format_game(match) + Best3AIPredictor.format(predictions)
                    except asyncio.TimeoutError:
                        print("AI prediction error: TimeoutError: AI did not answer within 90 seconds")
                        text = (
                            khl.format_game(match)
                            + "\n\n⚠️ <b>ИИ не успел ответить.</b>\nПопробуй запрос ещё раз."
                        )
                    except Exception as exc:
                        print(f"AI prediction error: {type(exc).__name__}: {exc}")
                        text = (
                            khl.format_game(match)
                            + "\n\n⚠️ <b>ИИ-прогноз временно недоступен.</b>\n"
                            f"Ошибка: {type(exc).__name__}"
                        )
                    markup = back_khl_keyboard()

        elif data.startswith("sport:"):
            sport = data.split(":", 1)[1].upper()
            text = f"{sport}\n\nЭтот раздел подключим следующим этапом."
            markup = main_menu()

        else:
            text = "Раздел пока не настроен."
            markup = main_menu()

        await safe_edit(callback, text, markup)

    except (ApiSportsError, ValueError) as exc:
        print(f"Application error: {type(exc).__name__}: {exc}")
        error_text = "⚠️ <b>Не удалось получить данные КХЛ.</b>\n\nПроверьте настройки API или попробуйте позже."
        await safe_edit(callback, error_text, main_menu())
        try:
            await callback.answer("Не удалось получить данные", show_alert=False)
        except TelegramBadRequest:
            pass

    except Exception as exc:
        print(f"Unhandled application error: {type(exc).__name__}: {exc}")
        await safe_edit(callback, "⚠️ <b>Произошла ошибка.</b>\n\nПроверь CMD — там будет точная причина.", main_menu())


async def run_bot() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await dp.start_polling(bot)
