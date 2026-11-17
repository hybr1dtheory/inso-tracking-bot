import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
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
        tg_id = event.from_user.id
        user = await get_user_by_tg_id(DB_PATH, tg_id)
        # Прокидаємо об'єкт user у хендлери
        data['db_user'] = user
        # Якщо це команда /admin, але користувач не адмін (або його взагалі немає в базі)
        if isinstance(event, Message) and event.text:
            if event.text.startswith('/admin') or event.text == BTN_ADMIN_PANEL:
                if not user or user['role'] != 'ADMIN':
                    await event.answer("⛔️ У вас немає доступу до панелі адміністратора.")
                    return
        # Передаємо управління далі
        return await handler(event, data)
