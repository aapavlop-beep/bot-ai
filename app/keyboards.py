from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔥 Лучшие ставки", callback_data="top")],
            [
                InlineKeyboardButton(text="🎮 CS2", callback_data="sport:cs2"),
                InlineKeyboardButton(text="🏒 КХЛ", callback_data="sport:khl"),
            ],
            [InlineKeyboardButton(text="⚽ Футбол", callback_data="sport:football")],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
                InlineKeyboardButton(text="ℹ️ О боте", callback_data="about"),
            ],
        ]
    )
