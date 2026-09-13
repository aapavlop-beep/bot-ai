from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .ai_predictor_v3 import Best3AIPredictor
from .config import settings
from .khl_auto_refresh import KHLBackgroundCache
from .khl_enhanced import EnhancedKHLService
from .khl_schedule import verified_games_for_date
from .keyboards import main_menu
from .providers.api_sport_ru import ApiSportRuClient, ApiSportRuError
from .storage import PredictionStore
from .team_names import display_team_name

MSK = ZoneInfo("Europe/Moscow")

dp = Dispatcher()
store = PredictionStore(settings.database_path)
khl = EnhancedKHLService(ApiSportRuClient(settings.api_sport_ru_key or ""))
ai_predictor = Best3AIPredictor(settings.openai_api_key, settings.openai_model, settings.openai_base_url) if settings.openai_api_key else None
khl_refresh = KHLBackgroundCache(khl, interval_minutes=30, days_ahead=3)


def _team_obj(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _team_name(obj: dict) -> str:
    for key in ("name", "teamName", "shortName", "team_name", "title"):
        value = obj.get(key)
        if value:
            return str(value).strip()
    return ""


def normalize_game_names(game: dict) -> dict:
    normalized = dict(game)
    teams = dict(game.get("teams") or {})
    home = _team_obj(teams.get("home"))
    away = _team_obj(teams.get("away"))
    if not _team_name(home):
        home = _team_obj(game.get("homeTeam") or game.get("home_team") or game.get("home"))
    if not _team_name(away):
        away = _team_obj(game.get("awayTeam") or game.get("away_team") or game.get("away"))
    home_name = display_team_name(_team_name(home))
    away_name = display_team_name(_team_name(away))
    if home_name:
        home["name"] = home_name
    if away_name:
        away["name"] = away_name
    normalized["teams"] = {"home": home, "away": away}
    return normalized


def khl_games_keyboard(games: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, raw_game in enumerate(games[:15]):
        game = normalize_game_names(raw_game)
        game_id = game.get("id")
        teams = game.get("teams") or {}
        home = _team_name(_team_obj(teams.get("home"))) or "Хозяева"
        away = _team_name(_team_obj(teams.get("away"))) or "Гости"
        if game_id is not None:
            rows.append([InlineKeyboardButton(text=f"{home} — {away}", callback_data=f"khl:game:{game_id}")])
        else:
            rows.append([InlineKeyboardButton(text=f"⚠️ {home} — {away}", callback_data=f"khl:missing:{index}")])
    rows.append([
        InlineKeyboardButton(text="📅 Сегодня", callback_data="sport:khl"),
        InlineKeyboardButton(text="➡️ Завтра", callback_data="khl:date:1"),
    ])
    rows.append([InlineKeyboardButton(text="📅 Послезавтра", callback_data="khl:date:2")])
    rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_khl_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Матчи КХЛ", callback_data="sport:khl")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu")],
        ]
    )


def _date_by_offset(offset: int) -> str:
    return (datetime.now(MSK).date() + timedelta(days=offset)).isoformat()


def _date_label(offset: int) -> str:
    if offset == 0:
        return "сегодня"
    if offset == 1:
        return "завтра"
    if offset == 2:
        return "послезавтра"
    return _date_by_offset(offset)


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
                "Бот собирает спортивные данные, проверяет публичные источники, "
                "рассчитывает вероятности и использует ИИ для итогового анализа.\n\n"
                "Сейчас запускаем первый полноценный раздел — КХЛ."
            )
            markup = main_menu()
        elif data == "top":
            text = "🔥 <b>Лучшие прогнозы</b>\n\nПосле обновления аналитики здесь будут отображаться только кандидаты, прошедшие фильтр качества данных и value."
            markup = main_menu()
        elif data == "sport:khl" or data.startswith("khl:date:"):
            offset = 0 if data == "sport:khl" else int(data.rsplit(":", 1)[1])
            date_value = _date_by_offset(offset)
            games = khl_refresh.get_games(date_value)
            if not games:
                games = await verified_games_for_date(khl.client, date_value)
            games = [normalize_game_names(game) for game in games]
            if not games:
                text = (
                    f"🏒 <b>КХЛ — {_date_label(offset)}</b>\n\n"
                    "Матчи на эту дату не найдены в доступных источниках."
                )
                markup = khl_games_keyboard([])
            else:
                source = "резервный/смешанный источник"
                if all(game.get("__khl_mobile") for game in games):
                    source = "Официальный KHL mobile API"
                elif all(game.get("__api_sport_ru") for game in games):
                    source = "API-SPORT.ru"
                text = (
                    f"🏒 <b>КХЛ — {_date_label(offset)}</b>\n\n"
                    f"📅 {date_value}\n"
                    f"Матчей: <b>{len(games)}</b>\n"
                    f"📡 Расписание: <b>{source}</b>\n\n"
                    "Выбери матч:\n\n"
                    "🔄 Расписание автоматически собирается и обновляется каждый день."
                )
                markup = khl_games_keyboard(games)
        elif data.startswith("khl:missing:"):
            text = "⚠️ <b>Матч не имеет идентификатора источника.</b>\n\nНевозможно получить линию и прогноз."
            markup = back_khl_keyboard()
        elif data.startswith("khl:game:"):
            if ai_predictor is None:
                text = "⚠️ <b>ИИ не настроен.</b>\n\nДобавь OPENAI_API_KEY в переменные Railway и перезапусти бота."
                markup = main_menu()
            else:
                game_id = int(data.rsplit(":", 1)[1])
                await safe_status(callback, "🏒 <b>Глубокий анализ матча</b>\n\n1/6 Нахожу матч и актуальные данные...")
                cached = khl_refresh.get_context(game_id)
                if cached:
                    game, markets, analysis_data = cached
                else:
                    game = None
                    games: list[dict] = []
                    for date_value in (_date_by_offset(i) for i in range(3)):
                        games = khl_refresh.get_games(date_value)
                        if not games:
                            games = await verified_games_for_date(khl.client, date_value)
                        game = next((item for item in games if int(item.get("id", -1)) == game_id), None)
                        if game is not None:
                            break
                    if game is None:
                        await safe_edit(callback, "Матч не найден. Обнови список матчей КХЛ.", back_khl_keyboard())
                        return
                    game = normalize_game_names(game)
                    await safe_status(callback, "🏒 <b>Глубокий анализ матча</b>\n\n2/6 Ищу составы, травмы и дисквалификации...")
                    analysis_data = await khl.analysis_for_game(game)
                    await safe_status(callback, "🏒 <b>Глубокий анализ матча</b>\n\n3/6 Проверяю форму, H2H, таблицу, вратарей и свежие новости...")
                    markets = await khl.markets_for_game(game_id, game)
                game = normalize_game_names(game)
                match = khl.to_match(game, markets, analysis_data)
                if not match.markets:
                    source = analysis_data.get("активный_источник_статистики", "резервный источник")
                    text = (
                        khl.format_game(match)
                        + "\n\n⚠️ <b>Линия букмекеров сейчас недоступна.</b>"
                        + f"\n📡 Статистика получена из: <b>{source}</b>"
                        + "\n\nПрогноз без реального коэффициента не формирую, чтобы не выдумывать линию."
                    )
                    markup = back_khl_keyboard()
                else:
                    await safe_status(
                        callback,
                        f"🏒 <b>{match.home} — {match.away}</b>\n\n"
                        "4/6 Проверяю актуальность найденных данных...\n"
                        "5/6 ИИ оценивает все факторы и ищет только ставки с реальным перевесом...",
                    )
                    try:
                        predictions = await asyncio.wait_for(asyncio.to_thread(ai_predictor.predict, match), timeout=90.0)
                        diagnostic = analysis_data.get("диагностика_статистики", "")
                        source = analysis_data.get("активный_источник_статистики", "нет")
                        text = khl.format_game(match) + Best3AIPredictor.format(predictions)
                        text += f"\n\n📡 <b>Статистика:</b> {source}"
                        if diagnostic:
                            text += f"\n🔎 {diagnostic}"
                    except asyncio.TimeoutError:
                        print("AI prediction error: TimeoutError: AI did not answer within 90 seconds", flush=True)
                        text = khl.format_game(match) + "\n\n⚠️ <b>ИИ не успел ответить.</b>\nПопробуй запрос ещё раз."
                    except Exception as exc:
                        print(f"AI prediction error: {type(exc).__name__}: {exc}", flush=True)
                        text = khl.format_game(match) + "\n\n⚠️ <b>ИИ-прогноз временно недоступен.</b>\nОшибка: " + type(exc).__name__
                markup = back_khl_keyboard()
        elif data.startswith("sport:"):
            sport = data.split(":", 1)[1].upper()
            text = f"{sport}\n\nЭтот раздел подключим следующим этапом."
            markup = main_menu()
        else:
            text = "Раздел пока не настроен."
            markup = main_menu()
        await safe_edit(callback, text, markup)
    except (ApiSportRuError, ValueError) as exc:
        print(f"Application error: {type(exc).__name__}: {exc}", flush=True)
        await safe_edit(callback, "⚠️ <b>Не удалось получить данные КХЛ.</b>\n\nОсновной и резервный источники временно недоступны.", main_menu())
        try:
            await callback.answer("Не удалось получить данные", show_alert=False)
        except TelegramBadRequest:
            pass
    except Exception as exc:
        print(f"Unhandled application error: {type(exc).__name__}: {exc}", flush=True)
        await safe_edit(callback, "⚠️ <b>Не удалось обработать запрос КХЛ.</b>\n\nПопробуй ещё раз — источник мог временно ограничить запросы.", main_menu())


async def run_bot() -> None:
    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    khl_refresh.start()
    try:
        await dp.start_polling(bot)
    finally:
        await khl_refresh.stop()
        await bot.session.close()
