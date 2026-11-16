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
    await message.answer(
        "✅ <b>Бот успішно запущено!</b>\n\n"
        "З'єднання з Telegram API та базою даних працює.",
        parse_mode=ParseMode.HTML
    )

async def main():
    # Перевірка наявності токену
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN не знайдено! Перевірте файл .env")
        return

    # Створення папки data, якщо вона не існує (aiosqlite створює файл, але не папки)
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Роботу бота завершено вручну (Ctrl+C).")
