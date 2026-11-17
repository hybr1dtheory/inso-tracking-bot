import logging
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from config import DB_PATH, MONITORING_GROUP_ID
from database.queries import (
    get_user_active_trips,
    get_trip_details,
    record_checkin,
    close_trip,
    get_operators_tg_ids
)
from keyboards.inline import (
    build_traveler_trip_keyboard,
    build_close_confirm_keyboard,
    build_cancel_note_keyboard
)


logger = logging.getLogger(__name__)
traveler_router = Router()


class TravelerNoteFSM(StatesGroup):
    waiting_for_note = State()


def format_trip_card(trip: dict) -> str:
    '''Формує текстову картку активної поїздки для мандрівника.'''
    crew_names = [
        f"{m['full_name']} (Старший)" if m["is_leader"] else m["full_name"]
        for m in trip["crew"]
    ]
    minutes_ago = max(0, trip["minutes_ago"] or 0)
    last_by = trip["last_checkin_user_name"] or "Система"
    ping_warning = ""
    if trip["pending_request_sent_at"]:
        ping_warning = "\n⚠️ <b>Моніторинг очікує вашого підтвердження!</b>\n"
        
    return (
        f"🚗 <b>Активна поїздка #{trip['id']}</b>\n"
        f"📍 Маршрут: <b>{trip['origin']} → {trip['destination']}</b>\n"
        f"👥 Екіпаж: {', '.join(crew_names)}\n"
        f"⏱ Інтервал контролю: кожні <b>{trip['checkin_interval_min']} хв</b>\n"
        f"🕒 Останній чекін: <b>{minutes_ago} хв тому</b> ({last_by})"
        f"{ping_warning}"
    )


async def notify_monitoring(bot: Bot, text: str):
    '''Надсилає сповіщення в групу моніторингу або в приват операторам (якщо група не налаштована).'''
    if MONITORING_GROUP_ID:
        try:
            await bot.send_message(chat_id=int(MONITORING_GROUP_ID), text=text)
            return
        except Exception as e:
            logger.warning(f"Не вдалося відправити повідомлення в MONITORING_GROUP_ID: {e}")
    # Fallback для локального тестування: шлемо всім OPERATOR та ADMIN
    operator_ids = await get_operators_tg_ids(DB_PATH)
    for op_tg_id in operator_ids:
        try:
            await bot.send_message(chat_id=op_tg_id, text=text)
        except Exception as e:
            logger.warning(f"Не вдалося сповістити оператора {op_tg_id}: {e}")


async def notify_other_crew_members(bot: Bot, trip: dict, actor_user_id: int, text: str):
    '''Сповіщає інших учасників екіпажу, щоб уникнути дублювання чекінів.'''
    for member in trip["crew"]:
        if member["id"] != actor_user_id:
            try:
                await bot.send_message(chat_id=member["tg_id"], text=text)
            except Exception as e:
                logger.warning(f"Не вдалося сповістити члена екіпажу {member['tg_id']}: {e}")


# --- Перегляд поточних поїздок (/my_trip) ---
@traveler_router.message(Command("my_trip"))
async def cmd_my_trip(message: Message, db_user: dict | None, state: FSMContext):
    await state.clear()
    if not db_user:
        await message.answer("Спочатку зареєструйтесь командою /start.")
        return
    active_trips = await get_user_active_trips(DB_PATH, db_user["id"])
    if not active_trips:
        await message.answer("🟢 У вас зараз немає активних відряджень.")
        return
    for trip in active_trips:
        await message.answer(
            text=format_trip_card(trip),
            reply_markup=build_traveler_trip_keyboard(trip["id"])
        )


# --- Оновлення картки поїздки ---
@traveler_router.callback_query(F.data.startswith("trv_refresh:"))
async def refresh_trip_card(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Ця поїздка вже завершена.", show_alert=True)
        await callback.message.edit_text("🏁 Поїздку завершено.")
        return
    await callback.answer("Статус оновлено")
    try:
        await callback.message.edit_text(
            text=format_trip_card(trip),
            reply_markup=build_traveler_trip_keyboard(trip_id)
        )
    except Exception:
        # Ігноруємо помилку Telegram "message is not modified", якщо текст не змінився
        pass


# --- Миттєвий чекін в 1 клік ---
@traveler_router.callback_query(F.data.startswith("trv_checkin:"))
async def process_quick_checkin(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        await callback.answer("Помилка авторизації. Натисніть /start", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    event_type = await record_checkin(DB_PATH, trip_id, db_user["id"])
    if not event_type:
        await callback.answer("Поїздка вже закрита або не знайдена.", show_alert=True)
        await callback.message.edit_text("🏁 Ця поїздка вже завершена.")
        return
    await callback.answer("✅ Чекін прийнято!")
    trip = await get_trip_details(DB_PATH, trip_id)
    await callback.message.edit_text(
        text=format_trip_card(trip) + "\n\n✅ <i>Ваш чекін щойно зафіксовано!</i>",
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    # Сповіщаємо моніторинг
    kind_label = "за запитом оператора" if event_type == "REQUESTED_CHECKIN" else "самостійний"
    await notify_monitoring(
        bot,
        f"🟢 <b>Чекін по поїздці #{trip_id}</b> ({trip['route_code']})\n"
        f"👤 Від: <b>{db_user['full_name']}</b> ({kind_label})\n"
        f"⏱ Наступний контроль через {trip['checkin_interval_min']} хв."
    )
    # Сповіщаємо інших учасників поїздки
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"ℹ️ <b>Поїздка #{trip_id} ({trip['route_code']}):</b>\n{db_user['full_name']} щойно підтвердив(ла) чекін для вашої групи."
    )


# --- Чекін із текстовою приміткою (FSM) ---
@traveler_router.callback_query(F.data.startswith("trv_note:"))
async def ask_checkin_note(callback: CallbackQuery, state: FSMContext):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)

    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка вже закрита.", show_alert=True)
        return

    await callback.answer()
    await state.update_data(note_trip_id=trip_id)
    await state.set_state(TravelerNoteFSM.waiting_for_note)

    await callback.message.edit_text(
        f"💬 <b>Чекін з приміткою для поїздки #{trip_id}</b>\n\n"
        "Напишіть коротке повідомлення для оператора (наприклад: <i>Проїхали Полтаву, зупинка на АЗС 15 хв</i>):",
        reply_markup=build_cancel_note_keyboard(trip_id)
    )


@traveler_router.message(TravelerNoteFSM.waiting_for_note, F.text)
async def process_checkin_note(message: Message, db_user: dict | None, state: FSMContext, bot: Bot):
    if not db_user:
        return
    data = await state.get_data()
    trip_id = data.get("note_trip_id")
    note_text = message.text.strip()
    await state.clear()
    event_type = await record_checkin(DB_PATH, trip_id, db_user["id"], note=note_text)
    if not event_type:
        await message.answer("🏁 Ця поїздка вже завершена.")
        return
    trip = await get_trip_details(DB_PATH, trip_id)
    await message.answer(
        text=format_trip_card(trip) + f"\n\n✅ <i>Чекін з приміткою («{note_text}») зафіксовано!</i>",
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    await notify_monitoring(
        bot,
        f"🟢 <b>Чекін з приміткою — Поїздка #{trip_id}</b> ({trip['route_code']})\n"
        f"👤 Від: <b>{db_user['full_name']}</b>\n"
        f"💬 Примітка: <i>{note_text}</i>"
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"ℹ️ <b>Поїздка #{trip_id} ({trip['route_code']}):</b>\n"
            f"{db_user['full_name']} зробив(ла) чекін із приміткою: <i>«{note_text}»</i>"
        )
    )


# --- Закриття поїздки (2 кроки з підтвердженням) ---
@traveler_router.callback_query(F.data.startswith("trv_close_ask:"))
async def ask_close_confirmation(callback: CallbackQuery):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка вже завершена.", show_alert=True)
        await callback.message.edit_text("🏁 Поїздку завершено.")
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🏁 <b>Підтвердження прибуття</b>\n\n"
        f"Ви дійсно прибули в пункт призначення (<b>{trip['destination']}</b>) "
        f"і хочете закрити поїздку #{trip_id}?",
        reply_markup=build_close_confirm_keyboard(trip_id)
    )


@traveler_router.callback_query(F.data.startswith("trv_close_confirm:"))
async def confirm_close_trip(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка вже була закрита раніше.", show_alert=True)
        await callback.message.edit_text("🏁 Поїздку вже завершено.")
        return
    success = await close_trip(DB_PATH, trip_id, db_user["id"])
    if not success:
        await callback.answer("Не вдалося закрити поїздку.", show_alert=True)
        return
    await callback.answer("🏁 З прибуттям! Поїздку закрито.")
    await callback.message.edit_text(
        f"🏁 <b>Поїздку #{trip_id} ({trip['route_code']}) успішно завершено!</b>\n"
        f"Закрив(ла): {db_user['full_name']}. Дякуємо за роботу!"
    )
    # Сповіщаємо моніторинг про прибуття
    await notify_monitoring(
        bot,
        f"🏁 <b>Поїздку #{trip_id} ЗАВЕРШЕНО</b>\n"
        f"📍 Маршрут: <b>{trip['route_code']}</b>\n"
        f"👤 Підтвердив прибуття: <b>{db_user['full_name']}</b>"
    )
    # Сповіщаємо решту екіпажу
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🏁 <b>Поїздку #{trip_id} ({trip['route_code']}) завершено!</b>\n{db_user['full_name']} відмітив(ла) прибуття в пункт призначення."
    )
