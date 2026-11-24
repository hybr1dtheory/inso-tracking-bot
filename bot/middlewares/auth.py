from collections.abc import Awaitable, Callable
import logging
from typing import Any
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from config import DB_PATH
from database.queries import get_user_by_tg_id
from keyboards.reply import BTN_ADMIN_PANEL


logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any]
    ) -> Any:
        # Ігноруємо будь-які події поза приватними чатами
        chat = (
            event.chat if isinstance(event, Message)
            else (event.message.chat if event.message else None)
        )
        if not chat or chat.type != "private" or not event.from_user:
            return
        tg_id = event.from_user.id
        user = await get_user_by_tg_id(DB_PATH, tg_id)
        # ФІЛЬТР: Якщо користувача немає в БД — блокуємо доступ
        if not user:
            logger.warning(f"Unauthorized access attempt by unregistered user: tg_id={tg_id}")
            if isinstance(event, Message):
                await event.answer(
                    "⛔️ <b>Access denied.</b>\n"
                    "Your account is not registered in the monitoring system.\n\n"
                    f"Your Telegram ID: <code>{tg_id}</code>\n"
                    "<i>Copy this ID and send it to the administrator to grant access.</i>",
                    reply_markup=ReplyKeyboardRemove()
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⛔️ Access denied. You are not registered in the system.",
                    show_alert=True
                )
            return
        # Користувач верифікований — передаємо його профіль у хендлери
        data["db_user"] = user
        # Перевірка прав для адмін-панелі (як для команд, так і для інлайн-кнопок)
        if user["role"] != "ADMIN":
            is_admin_msg = (
                isinstance(event, Message)
                and event.text
                and (event.text.startswith("/admin") or event.text == BTN_ADMIN_PANEL)
            )
            if is_admin_msg:
                await event.answer("⛔️ You do not have access to the admin panel.")
                return
            is_admin_cb = (
                isinstance(event, CallbackQuery)
                and event.data
                and event.data.startswith("admin_")
            )
            if is_admin_cb:
                await event.answer("⛔️ You do not have access to the admin panel.", show_alert=True)
                return
        return await handler(event, data)
