import asyncio
from datetime import datetime
from html import escape
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from config import BOT_TOKEN, DB_PATH, KYIV_TZ
from database.init_db import create_tables
from handlers.admin import admin_router
from handlers.operator import operator_router
from handlers.traveler import notify_monitoring, traveler_router
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

MAX_ERROR_REPORT_LENGTH = 400
SHUTDOWN_NOTIFY_TIMEOUT_SEC = 5.0


def _kyiv_now_str() -> str:
    """Повертає поточний київський час для системних сповіщень."""
    return datetime.now(KYIV_TZ).strftime("%d.%m.%Y at %H:%M:%S")


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
    # Ініціалізуємо бота на самому початку, щоб мати змогу повідомити про збій навіть на етапі старту БД
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    watchdog_task: asyncio.Task | None = None
    crash_error: Exception | None = None
    stop_reason = "Manual / system stop (signal)"
    try:
        # Ініціалізація бази даних SQLite (створення директорії та таблиць)
        logger.info("Initializing the database...")
        await create_tables(DB_PATH)
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
        # Повідомляємо групу моніторингу про успішний запуск системи
        await notify_monitoring(
            bot,
            text=(
                "🟢 <b>TRAVEL BOT STARTED</b>\n"
                f"🕒 Time (Kyiv): <b>{_kyiv_now_str()}</b>\n"
                "📡 Watchdog service and trip tracking are online."
            )
        )
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types()
        )
    except asyncio.CancelledError:
        # На Windows Ctrl+C скасовує корутину main() через CancelledError
        stop_reason = "Manual stop (Ctrl+C / signal)"
        raise
    except Exception as e:
        crash_error = e
        logger.exception("Critical error caused the bot to stop:")
        raise
    finally:
        # Коректна зупинка фонового Watchdog
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass
        # Сповіщення групи моніторингу про причину зупинки (з коротким таймаутом, щоб не зависнути без мережі)
        if crash_error is not None:
            err_type = type(crash_error).__name__
            err_msg = str(crash_error).strip()
            if len(err_msg) > MAX_ERROR_REPORT_LENGTH:
                err_msg = err_msg[:MAX_ERROR_REPORT_LENGTH] + "..."
            details = f"{err_type}: {err_msg}" if err_msg else err_type
            shutdown_text = (
                "🔴 <b>MONITORING BOT CRASHED</b>\n"
                f"🕒 Time (Kyiv): <b>{_kyiv_now_str()}</b>\n"
                f"⚠️ Error: <code>{escape(details)}</code>\n\n"
                "<i>Please check server logs and restart the service if needed.</i>"
            )
        else:
            shutdown_text = (
                "🟠 <b>MONITORING BOT STOPPED</b>\n"
                f"🕒 Time (Kyiv): <b>{_kyiv_now_str()}</b>\n"
                f"ℹ️ Reason: <i>{stop_reason}</i>"
            )
        try:
            await asyncio.wait_for(
                notify_monitoring(bot, text=shutdown_text),
                timeout=SHUTDOWN_NOTIFY_TIMEOUT_SEC
            )
        except Exception as notify_err:
            logger.warning(f"Failed to send shutdown notification: {notify_err}")
        # Закриття HTTP-сесії бота
        await bot.session.close()
        logger.info("Bot shutdown completed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually (Ctrl+C).")
