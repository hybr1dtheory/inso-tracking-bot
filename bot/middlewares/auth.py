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
            logger.warning(f"Спроба доступу незареєстрованого користувача: tg_id={tg_id}")
            if isinstance(event, Message):
                await event.answer(
                    "⛔️ <b>Доступ заборонено.</b>\n"
                    "Ваш акаунт не зареєстровано в системі моніторингу.\n\n"
                    f"Ваш Telegram ID: <code>{tg_id}</code>\n"
                    "<i>Скопіюйте цей ID та надішліть адміністратору для надання доступу.</i>",
                    reply_markup=ReplyKeyboardRemove()
                )
            elif isinstance(event, CallbackQuery):
                await event.answer(
                    "⛔️ Доступ заборонено. Ви не зареєстровані в системі.",
                    show_alert=True
                )
            return
        # Користувач верифікований — передаємо його профіль у хендлери
        data["db_user"] = user
        # Перевірка прав для адмін-панелі
        if isinstance(event, Message) and event.text:
            if event.text.startswith("/admin") or event.text == BTN_ADMIN_PANEL:
                if user["role"] != "ADMIN":
                    await event.answer("⛔️ У вас немає доступу до панелі адміністратора.")
                    return
        return await handler(event, data)
