from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from .config import settings
from .keyboards import main_menu
from .storage import PredictionStore


dp = Dispatcher()
store = PredictionStore(settings.database_path)


@dp.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "🎯 Спортивная аналитика\n\n"
        "CS2 • КХЛ • футбол\n\n"
        "Выбирай раздел:",
        reply_markup=main_menu(),
    )


@dp.callback_query()
async def callbacks(callback: CallbackQuery) -> None:
    data = callback.data or ""

    if data == "stats":
        stats = store.stats()
        text = (
            "📊 Статистика модели\n\n"
            f"Прогнозов: {stats['total']}\n"
            f"Зашло: {stats['won']}\n"
            f"Не зашло: {stats['lost']}\n"
            f"Проходимость: {stats['hit_rate']:.1f}%"
        )
    elif data == "about":
        text = (
            "ℹ️ О боте\n\n"
            "Бот собирает спортивные данные, рассчитывает вероятности "
            "и сравнивает их с коэффициентами.\n\n"
            "На текущем этапе подключается слой реальных источников данных."
        )
    elif data == "top":
        text = "🔥 Лучшие ставки\n\nПока нет проверенных сигналов: сначала подключаем данные и модель."
    elif data.startswith("sport:"):
        sport = data.split(":", 1)[1].upper()
        text = f"{sport}\n\nМатчи появятся после подключения источника данных."
    else:
        text = "Раздел пока не настроен."

    await callback.message.edit_text(text, reply_markup=main_menu())
    await callback.answer()


async def run_bot() -> None:
    bot = Bot(token=settings.bot_token)
    await dp.start_polling(bot)
