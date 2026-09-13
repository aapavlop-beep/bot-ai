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
from .keyboards import main_menu
from .storage import PredictionStore
from .team_names import display_team_name

MSK = ZoneInfo("Europe/Moscow")

dp = Dispatcher()
store = PredictionStore(settings.database_path)
khl = EnhancedKHLService()
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
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise
