import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config import BOT_TOKEN, DB_PATH
from database.init_db import create_tables
from database.queries import get_user_by_tg_id, add_new_user
from middlewares.auth import AuthMiddleware
from handlers.admin import admin_router
from handlers.operator import operator_router


# Налаштування логування для терміналу
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ініціалізація диспетчера (обробника подій)
dp = Dispatcher()

# Тестовий хендлер: реагує на повідомлення /start
@dp.message(CommandStart())
async def cmd_start(message: Message):
    tg_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    # Перевіряємо, чи є користувач у базі
    user = await get_user_by_tg_id(DB_PATH, tg_id)
    if not user:  # Якщо немає — створюємо
        logger.info(f"Новий користувач: {full_name} ({tg_id})")
        user = await add_new_user(DB_PATH, tg_id, full_name, username=username)
        if user:
            await message.answer(
                f"👋 Вітаю, <b>{full_name}</b>!\n\n"
                "Вас успішно зареєстровано в системі трекінгу поїздок.\n"
                "Ваша роль: <code>TRAVELER</code>."
            )
        else:
            await message.answer("❌ Сталася помилка при реєстрації. Зверніться до адміністратора.")
    else:
        # Якщо вже є в базі — вітаємо в залежності від ролі
        role = user['role']
        if role == 'OPERATOR':
            await message.answer(
                f"👋 З поверненням, <b>{full_name}</b>!\n\n"
                "Ваша роль: <code>OPERATOR</code> (Відділ моніторингу).\n"
                "Ви можете керувати активними поїздками та створювати нові."
            )
        elif role == 'ADMIN':
             await message.answer(
                f"👋 Вітаю, <b>{full_name}</b>!\n\n"
                "Ваша роль: <code>ADMIN</code> (Адміністратор системи)."
            )
        else:
            await message.answer(
                f"👋 З поверненням, <b>{full_name}</b>!\n\n"
                "Ваша поточна роль: <code>TRAVELER</code>.\n"
                "Очікуйте, поки оператор додасть вас до активного відрядження."
            )


async def main():
    # Перевірка наявності токену
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не знайдено! Перевірте файл .env")
        return
    # Створення папки data, якщо вона не існує
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Ініціалізація бази даних SQLite
    logger.info("Ініціалізація бази даних...")
    await create_tables(DB_PATH)
    # Ініціалізація бота з підтримкою HTML-розмітки за замовчуванням
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    logger.info("🔄 Бот починає поллінг (очікування повідомлень)...")
    
    # Запускаємо бота
    # Видаляємо вебхуки, щоб не було конфліктів при локальному поллінгу
    await bot.delete_webhook(drop_pending_updates=True)
    # РЕЄСТРАЦІЯ MIDDLEWARE
    dp.message.middleware(AuthMiddleware())
    dp.callback_query.middleware(AuthMiddleware())
    # РЕЄСТРАЦІЯ РОУТЕРІВ
    dp.include_router(admin_router)
    dp.include_router(operator_router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Роботу бота завершено вручну (Ctrl+C).")
