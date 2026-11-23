import logging
import re
from datetime import datetime, timedelta
from aiogram import Bot, Router, F
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from config import DB_PATH, MONITORING_GROUP_ID, CHECKIN_INTERVAL_MIN, KYIV_TZ, UTC_TZ
from keyboards.reply import BTN_NEW_TRIP, BTN_MY_TRIP
from database.queries import (
    get_all_users,
    create_trip,
    activate_trip,
    cancel_planned_trip,
    get_user_active_trips,
    get_trip_details,
    record_checkin,
    close_trip,
    get_operators_tg_ids,
    record_help_request
)
from keyboards.inline import (
    build_cancel_creation_keyboard,
    build_companions_keyboard,
    build_planned_trip_keyboard,
    build_traveler_trip_keyboard,
    build_close_confirm_keyboard,
    build_cancel_note_keyboard,
    build_help_comment_keyboard
)


logger = logging.getLogger(__name__)
traveler_router = Router()


class NewTripFSM(StatesGroup):
    waiting_for_origin = State()
    waiting_for_destination = State()
    waiting_for_departure_time = State()
    waiting_for_companions = State()


class TravelerNoteFSM(StatesGroup):
    waiting_for_note = State()
    waiting_for_help_comment = State()


# --- Допоміжні функції форматування часу та карток ---
def utc_str_to_kyiv_str(utc_str: str) -> str:
    """Конвертує рядок часу з БД (UTC) у зручний формат за Києвом (ДД.ММ о ГГ:ХХ)."""
    try:
        dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC_TZ)
        dt_kyiv = dt_utc.astimezone(KYIV_TZ)
        # Кирилична 'о' винесена за межі strftime для сумісності з Windows
        return f"{dt_kyiv.strftime('%d.%m')} at {dt_kyiv.strftime('%H:%M')}"
    except Exception:
        return utc_str


def parse_kyiv_departure_time(text: str) -> tuple[datetime | None, str | None]:
    '''
    Парсить введений користувачем час (за Києвом) через регулярні вирази.
    Підтримує всі зручні формати:
    - Тільки час: '08:30', '8:30', '08.30', '08-30'
    - Дата і час: '18.11 08:30', '18.11. 08:30', '18.11.26 08:30', '18.11.2026 08:30'
    Повертає (datetime_utc, kyiv_formatted_str) або (None, None).
    '''
    raw = text.strip()
    now_kyiv = datetime.now(KYIV_TZ)
    try:
        # Варіант 1: Тільки час (наприклад: 08:30, 8.30, 14-15)
        time_only_match = re.match(r"^(\d{1,2})[:.\-](\d{2})$", raw)
        if time_only_match:
            hour = int(time_only_match.group(1))
            minute = int(time_only_match.group(2))

            dt_kyiv = now_kyiv.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Якщо вказаний час сьогодні вже минув більш ніж на 5 хв — плануємо на завтра
            if dt_kyiv < now_kyiv - timedelta(minutes=5):
                dt_kyiv += timedelta(days=1)
        else:
            # Варіант 2: Дата і час (наприклад: 18.11 08:30, 18.11.26 08.30, 18.11.2026 08:30)
            date_time_match = re.match(
                r"^(\d{1,2})[./\-](\d{1,2})(?:[./\-](\d{2,4}))?\.?\s+(\d{1,2})[:.\-](\d{2})$",
                raw
            )
            if not date_time_match:
                return None, None
            day = int(date_time_match.group(1))
            month = int(date_time_match.group(2))
            year_str = date_time_match.group(3)
            hour = int(date_time_match.group(4))
            minute = int(date_time_match.group(5))
            if year_str:
                year = int(year_str)
                if year < 100:
                    year += 2000
            else:
                year = now_kyiv.year
            # Прямий конструктор (перевіряє існування дати та діапазон 0..23 і 0..59)
            dt_kyiv = datetime(year, month, day, hour, minute, tzinfo=KYIV_TZ)
            # Якщо рік не вказували, а дата вже в минулому (напр. у грудні ввели січень)
            if not year_str and dt_kyiv < now_kyiv - timedelta(days=30):
                dt_kyiv = datetime(year + 1, month, day, hour, minute, tzinfo=KYIV_TZ)
        dt_utc = dt_kyiv.astimezone(UTC_TZ)
        kyiv_label = f"{dt_kyiv.strftime('%d.%m.%Y')} at {dt_kyiv.strftime('%H:%M')}"
        return dt_utc, kyiv_label
    except ValueError as e:
        # Логуємо точну причину, якщо ввели неіснуючу дату (напр. 32.11 або 25:00)
        logger.warning(f"Не вдалося розпарсити час '{raw}': {e}")
        return None, None


def format_crew_list(crew: list[dict]) -> str:
    """Форматує список учасників поїздки із зазначенням відділу та старшого групи."""
    formatted = []
    for m in crew:
        tags = []
        if m.get("team"):
            tags.append(m["team"])
        if m.get("is_leader"):
            tags.append("Team Leader")
        tag_str = f" ({', '.join(tags)})" if tags else ""
        formatted.append(f"{m['full_name']}{tag_str}")
    return "\n".join(formatted)


def format_trip_card(trip: dict) -> str:
    """Формує картку поїздки залежно від її статусу (PLANNED або ACTIVE)."""
    crew_names = format_crew_list(trip["crew"])
    if trip["status"] == "PLANNED":
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        return (
            f"📅 <b>Planned trip #{trip['id']}</b>\n"
            f"📍 Route: <b>{trip['origin']} → {trip['destination']}</b>\n"
            f"👥 Crew: \n{crew_names}\n"
            f"🕒 Departure: <b>{dep_str}</b>\n"
            f"⏱ Check-in interval: <b>{trip['checkin_interval_min']} min</b>\n\n"
            "<i>Monitoring will begin after you confirm departure using the button below.</i>"
        )
    minutes_ago = max(0, trip["minutes_ago"] or 0)
    last_by = trip["last_checkin_user_name"] or "Trip start"
    location_line = (
        f"\n📌 Last location: <i>{trip['last_checkin_note']}</i>"
        if trip.get("last_checkin_note") else ""
    )
    ping_warning = (
        "\n⚠️ <b>Monitoring is awaiting your check-in with location!</b>\n"
        if trip["pending_request_sent_at"] else ""
    )
    return (
        f"🚗 <b>Active trip #{trip['id']}</b>\n"
        f"📍 Route: <b>{trip['origin']} → {trip['destination']}</b>\n"
        f"👥 Crew: \n{crew_names}\n"
        f"⏱ Check-in interval: every <b>{trip['checkin_interval_min']} min</b>\n"
        f"🕒 Last check-in: <b>{minutes_ago} min ago</b> ({last_by})"
        f"{location_line}"
        f"{ping_warning}"
    )


async def notify_monitoring(bot: Bot, text: str, reply_markup=None):
    '''Надсилає повідомлення в групу моніторингу (або операторам у приват, якщо група не задана).'''
    if MONITORING_GROUP_ID:
        try:
            await bot.send_message(chat_id=int(MONITORING_GROUP_ID), text=text)
            return
        except Exception as e:
            logger.warning(f"Не вдалося надіслати в MONITORING_GROUP_ID: {e}")
    operator_ids = await get_operators_tg_ids(DB_PATH)
    for op_tg_id in operator_ids:
        try:
            await bot.send_message(chat_id=op_tg_id, text=text)
        except Exception as e:
            logger.warning(f"Не вдалося сповістити оператора {op_tg_id}: {e}")


async def notify_other_crew_members(bot: Bot, trip: dict, actor_user_id: int, text: str, reply_markup=None):
    '''Сповіщає інших учасників екіпажу.'''
    for member in trip["crew"]:
        if member["id"] != actor_user_id:
            try:
                await bot.send_message(
                    chat_id=member["tg_id"],
                    text=text,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.warning(f"Не вдалося сповістити члена екіпажу {member['tg_id']}: {e}")


# =====================================================================
# ГЛОБАЛЬНІ КОМАНДИ ТА КНОПКИ НИЖНЬОГО МЕНЮ (МАЮТЬ БУТИ НАЙПЕРШИМИ!)
# Перебивають будь-який активний стан FSM через await state.clear()
# =====================================================================
@traveler_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("There is no active process to cancel.")
        return
    await state.clear()
    await message.answer("❌ Action cancelled.")


@traveler_router.message(or_f(Command("new_trip"), F.text == BTN_NEW_TRIP))
async def cmd_new_trip(message: Message, db_user: dict | None, state: FSMContext):
    if not db_user:
        await message.answer("Please register first using the /start command.")
        return
    await state.clear()
    await message.answer(
        "🚗 <b>New Trip Setup</b>\n\n"
        "Step 1/4. Enter the <b>point of departure</b> (e.g., <i>Kyiv</i>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_origin)


@traveler_router.message(or_f(Command("my_trip"), F.text == BTN_MY_TRIP))
async def cmd_my_trip(message: Message, db_user: dict | None, state: FSMContext):
    await state.clear()
    if not db_user:
        await message.answer("Please register first using the /start command.")
        return
    trips = await get_user_active_trips(DB_PATH, db_user["id"])
    if not trips:
        await message.answer(
            "🟢 You have no active or planned trips.\n"
            "Tap <b>«🚗 New Trip»</b> in the menu to create a trip."
        )
        return
    for trip in trips:
        kb = (
            build_planned_trip_keyboard(trip["id"])
            if trip["status"] == "PLANNED"
            else build_traveler_trip_keyboard(trip["id"])
        )
        await message.answer(text=format_trip_card(trip), reply_markup=kb)


# =====================================================================
# КРОКИ FSM СТВОРЕННЯ ПОЇЗДКИ (Текстові + Інлайн-кнопки)
# =====================================================================
@traveler_router.callback_query(F.data == "trip_create_cancel")
async def cb_cancel_creation(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() is None:
        await callback.answer("The process has already been completed.", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.answer("Cancelled")
    await state.clear()
    await callback.message.edit_text("❌ Trip creation cancelled.")


@traveler_router.message(NewTripFSM.waiting_for_origin, F.text)
async def process_origin(message: Message, state: FSMContext):
    origin = message.text.strip()
    await state.update_data(origin=origin)
    await message.answer(
        f"Departure: <b>{origin}</b>\n\n"
        "Step 2/4. Enter the <b>destination</b> (e.g., <i>Dnipro</i>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_destination)


@traveler_router.message(NewTripFSM.waiting_for_destination, F.text)
async def process_destination(message: Message, state: FSMContext):
    destination = message.text.strip()
    await state.update_data(destination=destination)
    now_str = datetime.now(KYIV_TZ).strftime("%d.%m %H:%M")
    await message.answer(
        f"Route: <b>{(await state.get_data())['origin']} → {destination}</b>\n\n"
        "Step 3/4. Enter the <b>departure time</b> (Kyiv time):\n"
        "• Format <code>HH:MM</code> (e.g., <code>09:30</code>) — for today/tomorrow\n"
        f"• Format <code>DD.MM HH:MM</code> (e.g., <code>{now_str}</code>) — for a specific date",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_departure_time)


@traveler_router.message(NewTripFSM.waiting_for_departure_time, F.text)
async def process_departure_time(message: Message, db_user: dict, state: FSMContext):
    dt_utc, kyiv_label = parse_kyiv_departure_time(message.text)
    if not dt_utc:
        await message.answer(
            "❌ Invalid time format. Enter, for example, <code>08:30</code> or <code>18.11 08:30</code>:",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    all_users = await get_all_users(DB_PATH)
    other_users = [u for u in all_users if u["id"] != db_user["id"]]
    await state.update_data(
        planned_start_utc=dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
        kyiv_departure_label=kyiv_label,
        other_users=other_users,
        selected_companion_ids=[],
        comp_page=0
    )
    await message.answer(
        f"🕒 Departure time: <b>{kyiv_label}</b>\n\n"
        f"Step 4/4. You (<b>{db_user['full_name']}</b>) are automatically assigned as the trip leader.\n"
        "If traveling solo — tap the top button. If traveling in a group — select your companions:",
        reply_markup=build_companions_keyboard(other_users, [], page=0)
    )
    await state.set_state(NewTripFSM.waiting_for_companions)


@traveler_router.callback_query(NewTripFSM.waiting_for_companions, F.data.startswith("trip_comp_page:"))
async def paginate_companions(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected_ids: list[int] = data.get("selected_companion_ids", [])
    other_users: list[dict] = data.get("other_users", [])
    await state.update_data(comp_page=page)
    await callback.message.edit_reply_markup(
        reply_markup=build_companions_keyboard(other_users, selected_ids, page=page)
    )


@traveler_router.callback_query(NewTripFSM.waiting_for_companions, F.data.startswith("trip_toggle_comp:"))
async def toggle_companion(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    comp_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected_ids: list[int] = data.get("selected_companion_ids", [])
    other_users: list[dict] = data.get("other_users", [])
    current_page: int = data.get("comp_page", 0)
    if comp_id in selected_ids:
        selected_ids.remove(comp_id)
    else:
        selected_ids.append(comp_id)
    await state.update_data(selected_companion_ids=selected_ids)
    await callback.message.edit_reply_markup(
        reply_markup=build_companions_keyboard(other_users, selected_ids, page=current_page)
    )


@traveler_router.callback_query(NewTripFSM.waiting_for_companions, F.data == "trip_confirm_companions")
async def finalize_planned_trip(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    await callback.answer("Trip planned!")
    data = await state.get_data()
    await state.clear()
    trip_id = await create_trip(
        db_path=DB_PATH,
        origin=data["origin"],
        destination=data["destination"],
        creator_user_id=db_user["id"],
        companion_ids=data["selected_companion_ids"],
        planned_start_utc=data["planned_start_utc"],
        interval_min=CHECKIN_INTERVAL_MIN
    )
    if not trip_id:
        await callback.message.edit_text("❌ Error saving the trip to the database.")
        return
    trip = await get_trip_details(DB_PATH, trip_id)
    # Показуємо картку запланованої поїздки ініціатору
    await callback.message.edit_text(
        text=format_trip_card(trip),
        reply_markup=build_planned_trip_keyboard(trip_id)
    )
    # Розсилаємо картку іншим учасникам групи (якщо вони є)
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"📅 <b>{db_user['full_name']} added you to planned trip #{trip_id}</b>\n\n"
            + format_trip_card(trip)
        ),
        reply_markup=build_planned_trip_keyboard(trip_id)
    )
    crew_names = format_crew_list(trip["crew"])
    dep_str = utc_str_to_kyiv_str(trip["started_at"])
    # Сповіщаємо групу моніторингу про заплановану поїздку
    await notify_monitoring(
        bot,
        text=(
            f"📅 <b>NEW TRIP PLANNED #{trip_id}</b>\n"
            f"📍 Route: <b>{trip['route_code']}</b>\n"
            f"👥 Crew: \n{crew_names}\n"
            f"🕒 Planned departure: <b>{dep_str}</b>\n"
            f"👤 Created by: <b>{db_user['full_name']}</b>"
        )
    )


# =====================================================================
# ДІЇ ІЗ ЗАПЛАНОВАНОЮ ПОЇЗДКОЮ (Ручний старт або Скасування)
# =====================================================================
@traveler_router.callback_query(F.data.startswith("trv_start_now:"))
async def process_manual_start(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    activated = await activate_trip(DB_PATH, trip_id, actor_user_id=db_user["id"])
    if not activated:
        await callback.answer("The trip is already active or has been cancelled.", show_alert=True)
        return
    await callback.answer("🚀 Trip started! Have a safe trip!")
    trip = await get_trip_details(DB_PATH, trip_id)
    await callback.message.edit_text(
        text=format_trip_card(trip) + "\n\n🚀 <i>Monitoring started!</i>",
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    crew_names = format_crew_list(trip["crew"])
    await notify_monitoring(
        bot,
        text=(
            f"🚀 <b>TRIP STARTED #{trip_id}</b> ({trip['route_code']})\n"
            f"👥 Crew: \n{crew_names}\n"
            f"▶️ Started by: <b>{db_user['full_name']}</b>\n"
            f"⏱ Check-in interval (SOP): <b>{trip['checkin_interval_min']} min</b>"
        )
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🚀 <b>Trip #{trip_id} started!</b> ({db_user['full_name']})\n\n" + format_trip_card(trip),
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )


@traveler_router.callback_query(F.data.startswith("trv_cancel_planned:"))
async def process_cancel_planned_trip(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "PLANNED":
        await callback.answer("The trip has already started or was cancelled earlier.", show_alert=True)
        return
    cancelled = await cancel_planned_trip(DB_PATH, trip_id, db_user["id"])
    if not cancelled:
        await callback.answer("Failed to cancel the trip.", show_alert=True)
        return
    await callback.answer("Trip cancelled")
    await callback.message.edit_text(
        f"❌ <b>Planned trip #{trip_id} ({trip['route_code']}) has been cancelled.</b>"
    )
    # Сповіщаємо інших учасників екіпажу
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"❌ Planned trip #{trip_id} ({trip['route_code']}) was cancelled by {db_user['full_name']}."
    )
    # Сповіщаємо групу моніторингу (щоб закрити запис про запланований виїзд у стрічці)
    crew_names = format_crew_list(trip["crew"])
    dep_str = utc_str_to_kyiv_str(trip["started_at"])
    await notify_monitoring(
        bot,
        text=(
            f"❌ <b>PLANNED TRIP CANCELLED #{trip_id}</b>\n"
            f"📍 Route: <b>{trip['route_code']}</b>\n"
            f"👥 Crew: \n{crew_names}\n"
            f"🕒 Planned departure was: <b>{dep_str}</b>\n"
            f"👤 Cancelled by: <b>{db_user['full_name']}</b>"
        )
    )


# =====================================================================
# 4. ЧЕКІНИ, ЗАПИТ ДОПОМОГИ ТА ЗАКРИТТЯ ПОЇЗДКИ
# =====================================================================
@traveler_router.callback_query(F.data.startswith("trv_refresh:"))
async def refresh_trip_card(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("ACTIVE", "PLANNED"):
        await callback.answer("This trip has already been completed.", show_alert=True)
        await callback.message.edit_text("🏁 Trip completed.")
        return
    kb = (
        build_planned_trip_keyboard(trip_id)
        if trip["status"] == "PLANNED"
        else build_traveler_trip_keyboard(trip_id)
    )
    await callback.answer("Status updated")
    try:
        await callback.message.edit_text(text=format_trip_card(trip), reply_markup=kb)
    except Exception:
        pass


# --- Чекін із обов'язковим коментарем ---
@traveler_router.callback_query(F.data.startswith("trv_note:"))
async def ask_checkin_note(callback: CallbackQuery, state: FSMContext):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is not active.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(note_trip_id=trip_id)
    await state.set_state(TravelerNoteFSM.waiting_for_note)
    await callback.message.edit_text(
        f"📍 <b>Check-in for trip #{trip_id} ({trip['route_code']})</b>\n\n"
        "Specify your <b>approximate location</b> or current status "
        "(e.g., <i>\"passed Pryluky\"</i> or <i>\"in Chernihiv, 15 min stop\"</i>):",
        reply_markup=build_cancel_note_keyboard(trip_id)
    )


@traveler_router.message(TravelerNoteFSM.waiting_for_note, F.text)
async def process_checkin_note(message: Message, db_user: dict | None, state: FSMContext, bot: Bot):
    if not db_user:
        return
    data = await state.get_data()
    trip_id = data.get("note_trip_id")
    location_note = message.text.strip()
    await state.clear()
    event_type = await record_checkin(DB_PATH, trip_id, db_user["id"], note=location_note)
    if not event_type:
        await message.answer("🏁 This trip has already been completed.")
        return
    trip = await get_trip_details(DB_PATH, trip_id)
    await message.answer(
        text=format_trip_card(trip) + f"\n\n✅ <i>Check-in recorded: \"{location_note}\"</i>",
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    kind_label = "requested by operator" if event_type == "REQUESTED_CHECKIN" else "scheduled"
    await notify_monitoring(
        bot,
        f"🟢 <b>CHECK-IN — Trip #{trip_id}</b> ({trip['route_code']})\n"
        f"👤 From: <b>{db_user['full_name']}</b> ({kind_label})\n"
        f"📍 Location / comment: <b>{location_note}</b>\n"
        f"⏱ Next check-in in {trip['checkin_interval_min']} min."
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"ℹ️ <b>Trip #{trip_id} ({trip['route_code']}):</b>\n"
            f"{db_user['full_name']} checked in: <i>\"{location_note}\"</i>"
        )
    )


# --- Кнопка «Потрібна допомога» (з опціональним коментарем) ---
async def execute_help_alert(
    bot: Bot, db_user: dict, trip_id: int, comment: str | None
) -> dict | None:
    """Записує сигнал тривоги в БД та розсилає сповіщення в моніторинг і екіпажу."""
    recorded = await record_help_request(DB_PATH, trip_id, db_user["id"], note=comment)
    if not recorded:
        return None
    trip = await get_trip_details(DB_PATH, trip_id)
    crew_names = format_crew_list(trip["crew"])
    comment_block = f"💬 <b>Details:</b> <i>{comment}</i>" if comment else "💬 <b>Details:</b> <i>No comment (urgent alert)</i>"
    # Термінове сповіщення в групу моніторингу
    await notify_monitoring(
        bot,
        f"🚨🆘 <b>NEED HELP — Trip #{trip_id}</b>\n"
        f"📍 Route: <b>{trip['route_code']}</b>\n"
        f"👤 Alert from: <b>{db_user['full_name']}</b>\n"
        f"👥 Full crew: \n{crew_names}\n"
        f"{comment_block}\n\n"
        "❗️ <b>Contact the crew immediately!</b>"
    )
    # Сповіщення іншим учасникам цієї поїздки
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"🚨 <b>Trip #{trip_id}:</b> {db_user['full_name']} sent a "
            f"<b>\"Need help\"</b> request to monitoring!\n{comment_block}"
        )
    )
    return trip


@traveler_router.callback_query(F.data.startswith("trv_help_ask:"))
async def ask_help_comment(callback: CallbackQuery, state: FSMContext):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is not active.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(help_trip_id=trip_id)
    await state.set_state(TravelerNoteFSM.waiting_for_help_comment)
    await callback.message.edit_text(
        f"🆘 <b>Help Request — Trip #{trip_id}</b>\n\n"
        "Write in a message <b>what happened, where you are located, and what kind of help is needed</b>.\n\n"
        "<i>If this is an emergency and you don't have time to type — tap the button below:</i>",
        reply_markup=build_help_comment_keyboard(trip_id)
    )


@traveler_router.callback_query(F.data.startswith("trv_help_send_empty:"))
async def process_help_without_comment(callback: CallbackQuery, db_user: dict | None, state: FSMContext, bot: Bot):
    if not db_user:
        return
    await state.clear()
    trip_id = int(callback.data.split(":")[1])
    trip = await execute_help_alert(bot, db_user, trip_id, comment=None)
    if not trip:
        await callback.answer("The trip has already been completed.", show_alert=True)
        return
    await callback.answer("🚨 Help signal sent!", show_alert=True)
    await callback.message.edit_text(
        text=(
            format_trip_card(trip)
            + "\n\n🚨 <b>\"Need help\" request has been forwarded to monitoring! Standby for contact.</b>"
        ),
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )


@traveler_router.message(TravelerNoteFSM.waiting_for_help_comment, F.text)
async def process_help_with_comment(message: Message, db_user: dict | None, state: FSMContext, bot: Bot):
    if not db_user:
        return
    data = await state.get_data()
    trip_id = data.get("help_trip_id")
    comment_text = message.text.strip()
    await state.clear()
    trip = await execute_help_alert(bot, db_user, trip_id, comment=comment_text)
    if not trip:
        await message.answer("🏁 This trip has already been completed.")
        return
    await message.answer(
        text=(
            format_trip_card(trip)
            + f"\n\n🚨 <b>Help request (\"{comment_text}\") has been forwarded to monitoring! Standby for contact.</b>"
        ),
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )


# --- Закриття поїздки ---
@traveler_router.callback_query(F.data.startswith("trv_close_ask:"))
async def ask_close_confirmation(callback: CallbackQuery):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip has already been completed.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🏁 <b>Arrival Confirmation</b>\n\n"
        f"Have you really arrived in <b>{trip['destination']}</b> and want to close trip #{trip_id}?",
        reply_markup=build_close_confirm_keyboard(trip_id)
    )


@traveler_router.callback_query(F.data.startswith("trv_close_confirm:"))
async def confirm_close_trip(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip has already been closed.", show_alert=True)
        return
    if not await close_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("Error closing the trip.", show_alert=True)
        return
    await callback.answer("🏁 Welcome upon arrival!")
    await callback.message.edit_text(
        f"🏁 <b>Trip #{trip_id} ({trip['route_code']}) successfully completed!</b>\n"
        f"Closed by: {db_user['full_name']}."
    )
    await notify_monitoring(
        bot,
        f"🏁 <b>Trip #{trip_id} COMPLETED</b>\n"
        f"📍 Route: <b>{trip['route_code']}</b>\n"
        f"👤 Arrival confirmed by: <b>{db_user['full_name']}</b>"
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🏁 <b>Trip #{trip_id} ({trip['route_code']}) completed!</b> ({db_user['full_name']})"
    )
