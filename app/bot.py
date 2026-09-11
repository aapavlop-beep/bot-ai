from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .ai_predictor import AIPredictor
from .config import settings
from .khl import KHLService
from .keyboards import main_menu
from .providers.api_sports import ApiSportsClient, ApiSportsError
from .storage import PredictionStore


dp = Dispatcher()
store = PredictionStore(settings.database_path)
khl = KHLService(ApiSportsClient(settings.api_sports_key)) if settings.api_sports_key else None
ai_predictor = AIPredictor(settings.ollama_base_url, settings.ollama_model)


def khl_games_keyboard(games: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for game in games[:15]:
        game_id = game.get("id")
        teams = game.get("teams") or {}
        home = (teams.get("home") or {}).get("name") or "Хозяева"
        away = (teams.get("away") or {}).get("name") or "Гости"
        if game_id is not None:
            rows.append([InlineKeyboardButton(text=f"{home} — {away}", callback_data=f"khl:game:{game_id}")])
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
    """Изменяет сообщение и игнорирует Telegram 'message is not modified'."""
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


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
                "и использует локальный ИИ для итогового прогноза.\n\n"
                "Сейчас запускаем первый полноценный раздел — КХЛ."
            )
            markup = main_menu()

        elif data == "top":
            text = (
                "🔥 <b>Лучшие прогнозы</b>\n\n"
                "Пока нет проверенных сигналов. Сначала собираем реальные линии и историю модели."
            )
            markup = main_menu()

        elif data == "sport:khl":
            if khl is None:
                text = "🏒 <b>КХЛ</b>\n\nНе указан API_SPORTS_KEY в локальном .env."
                markup = main_menu()
            else:
                games = await khl.today_games()
                if not games:
                    text = "🏒 <b>КХЛ</b>\n\nНа текущую дату матчи КХЛ не найдены или источник временно недоступен."
                    markup = main_menu()
                else:
                    text = f"🏒 <b>КХЛ</b>\n\nМатчи на сегодня: {len(games)}\n\nВыбери матч:"
                    markup = khl_games_keyboard(games)

        elif data.startswith("khl:game:"):
            if khl is None:
                text = "Не указан API_SPORTS_KEY в локальном .env."
                markup = main_menu()
            else:
                game_id = int(data.rsplit(":", 1)[1])
                games = await khl.today_games()
                game = next((item for item in games if int(item.get("id", -1)) == game_id), None)
                if game is None:
                    text = "Матч не найден. Обнови список матчей КХЛ."
                    markup = back_khl_keyboard()
                else:
                    markets = await khl.markets_for_game(game_id)
                    match = khl.to_match(game, markets)
                    text = khl.format_game(match) + khl.format_markets(match)

                    try:
                        prediction = await asyncio.to_thread(ai_predictor.predict, match)
                        text += AIPredictor.format(prediction)
                    except Exception as exc:
                        print(f"AI prediction error: {type(exc).__name__}: {exc}")
                        text += (
                            "\n\n⚠️ <b>ИИ-прогноз временно недоступен.</b>\n"
                            "Проверьте, что Ollama запущена и модель установлена."
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
        await callback.answer()

    except (ApiSportsError, ValueError) as exc:
        print(f"Application error: {type(exc).__name__}: {exc}")
        error_text = (
            "⚠️ <b>Не удалось получить данные КХЛ.</b>\n\n"
            "Проверьте настройки API или попробуйте позже."
        )
        await safe_edit(callback, error_text, main_menu())
        await callback.answer()


async def run_bot() -> None:
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    await dp.start_polling(bot)
