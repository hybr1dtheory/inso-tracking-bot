import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from config import BOT_TOKEN, DB_PATH
from database.init_db import create_tables
from middlewares.auth import AuthMiddleware
from handlers.admin import admin_router
from handlers.operator import operator_router
from handlers.traveler import traveler_router
from services.watchdog import watchdog_loop
from keyboards.reply import get_main_menu_keyboard


# Налаштування логування для терміналу
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ініціалізація диспетчера
dp = Dispatcher()

# хендлер реагує на повідомлення /start
@dp.message(CommandStart())
async def cmd_start(message: Message, db_user: dict, state: FSMContext):
    await state.clear()
    full_name = db_user["full_name"]
    role = db_user["role"]
    await message.answer(
        f"Hello, <b>{full_name}</b>!\n"
        f"Your role in the system: <b>{role}</b>.\n\n"
        "Use the buttons in the bottom menu to interact with the bot:",
        reply_markup=get_main_menu_keyboard(role)
    )


async def main():
    # Перевірка наявності токену
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not found! Check the .env file")
        return
    # Створення папки data, якщо вона не існує
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Ініціалізація бази даних SQLite
    logger.info("Initializing the database...")
    await create_tables(DB_PATH)
    # Ініціалізація бота з підтримкою HTML-розмітки за замовчуванням
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    logger.info("🔄 The bot is starting polling (waiting for messages)...")
    
    # Запускаємо бота
    # Видаляємо вебхуки, щоб не було конфліктів при локальному поллінгу
    await bot.delete_webhook(drop_pending_updates=True)
    # РЕЄСТРАЦІЯ MIDDLEWARE
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    # РЕЄСТРАЦІЯ РОУТЕРІВ
    dp.include_router(admin_router)
    dp.include_router(operator_router)
    dp.include_router(traveler_router)
    asyncio.create_task(watchdog_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 The bot's task was manually finished (Ctrl+C)")
