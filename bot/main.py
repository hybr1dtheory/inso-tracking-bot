import asyncio
from html import escape
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from config import BOT_TOKEN, DB_PATH
from database.init_db import create_tables
from handlers.admin import admin_router
from handlers.operator import operator_router
from handlers.traveler import traveler_router
from keyboards.reply import get_main_menu_keyboard
from middlewares.auth import AuthMiddleware
from services.watchdog import watchdog_loop


# Налаштування логування для терміналу
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Ініціалізація диспетчера
dp = Dispatcher()


# Хендлер команди /start (перебиває будь-який активний стан FSM у всіх роутерах)
@dp.message(CommandStart())
async def cmd_start(message: Message, db_user: dict, state: FSMContext):
    await state.clear()
    full_name_safe = escape(db_user["full_name"])
    role = db_user["role"]
    await message.answer(
        f"Hello, <b>{full_name_safe}</b>!\n"
        f"Your role in the system: <b>{role}</b>.\n\n"
        "Use the buttons in the bottom menu to interact with the bot:",
        reply_markup=get_main_menu_keyboard(role)
    )


async def main():
    # Ініціалізація бази даних SQLite (створення директорії та таблиць)
    logger.info("Initializing the database...")
    await create_tables(DB_PATH)
    # Ініціалізація бота з підтримкою HTML-розмітки за замовчуванням
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    # Реєстрація outer_middleware (спрацьовує до фільтрів: миттєво блокує групи та видає ID незареєстрованим)
    dp.message.outer_middleware(AuthMiddleware())
    dp.callback_query.outer_middleware(AuthMiddleware())
    # Реєстрація роутерів
    dp.include_router(admin_router)
    dp.include_router(operator_router)
    dp.include_router(traveler_router)
    # Видаляємо вебхук без скидання накопичених оновлень, щоб не втратити SOS/чекіни при рестарті
    await bot.delete_webhook(drop_pending_updates=False)
    logger.info("Bot polling started (waiting for updates)...")
    watchdog_task = asyncio.create_task(watchdog_loop(bot))
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    finally:
        # Коректна зупинка фонового Watchdog та закриття HTTP-сесії бота
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("Bot shutdown completed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually (Ctrl+C).")
