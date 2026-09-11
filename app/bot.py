from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from .config import settings


dp = Dispatcher()


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Аналитический бот CS2 / КХЛ / футбол.\n\n"
        "Здесь будут вероятности, value-ставки, линии и история точности модели.\n"
        "Бот предоставляет аналитическую информацию и не размещает ставки автоматически."
    )


async def run_bot() -> None:
    bot = Bot(token=settings.bot_token)
    await dp.start_polling(bot)
