import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from database.queries import get_user_by_tg_id
from config import DB_PATH
from keyboards.reply import BTN_ADMIN_PANEL


logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        # Ігноруємо будь-які події у групових чатах
        chat = (
            event.chat if isinstance(event, Message)
            else (event.message.chat if event.message else None)
        )
        if chat and chat.type != "private":
            return
        tg_id = event.from_user.id
        user = await get_user_by_tg_id(DB_PATH, tg_id)
        # ФІЛЬТР: Якщо користувача немає в БД — блокуємо доступ
        if not user:
            logger.warning(f"Attempt by an unregistered user to access : tg_id={tg_id}")
            if isinstance(event, Message):
                await event.answer(
                    "⛔️ <b>Access is prohibited.</b>\n"
                    "Your account is not registered in the monitoring system.\n\n"
                    f"Your Telegram ID: <code>{tg_id}</code>\n"
                    "<i>Copy this ID and send it to the administrator to grant access.</i>",
                    reply_markup=ReplyKeyboardRemove()
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⛔️ Access is denied. You are not registered in the system.",
                    show_alert=True
                )
            return
        # Користувач верифікований — передаємо його профіль у хендлери
        data["db_user"] = user
        # Перевірка прав для адмін-панелі
        if isinstance(event, Message) and event.text:
            if event.text.startswith("/admin") or event.text == BTN_ADMIN_PANEL:
                if user["role"] != "ADMIN":
                    await event.answer("⛔️ You do not have access to the admin panel.")
                    return
        return await handler(event, data)
