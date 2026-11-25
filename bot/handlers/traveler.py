from datetime import date, datetime, time, timedelta
from html import escape
import logging
import re
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from config import CHECKIN_INTERVAL_MIN, DB_PATH, KYIV_TZ, MONITORING_GROUP_ID, UTC_TZ
from database.queries import (
    activate_trip,
    cancel_planned_trip,
    close_trip,
    create_trip,
    get_all_users,
    get_operators_tg_ids,
    get_trip_details,
    get_user_active_trips,
    record_checkin,
    record_help_request,
    resolve_help_request,
)
from keyboards.inline import (
    build_cancel_creation_keyboard,
    build_cancel_note_keyboard,
    build_close_confirm_keyboard,
    build_companions_keyboard,
    build_help_comment_keyboard,
    build_interval_selection_keyboard,
    build_planned_trip_keyboard,
    build_traveler_trip_keyboard,
)
from keyboards.reply import BTN_MY_TRIP, BTN_NEW_TRIP


logger = logging.getLogger(__name__)
traveler_router = Router()

MAX_PLACE_LENGTH = 60
MAX_NOTE_LENGTH = 500


class NewTripFSM(StatesGroup):
    waiting_for_origin = State()
    waiting_for_destination = State()
    waiting_for_departure_date = State()
    waiting_for_departure_time = State()
    waiting_for_interval = State()
    waiting_for_companions = State()


class TravelerNoteFSM(StatesGroup):
    waiting_for_note = State()
    waiting_for_help_comment = State()


# =====================================================================
# ДОПОМІЖНІ ФУНКЦІЇ ФОРМАТУВАННЯ ТА СПОВІЩЕНЬ
# =====================================================================
def utc_str_to_kyiv_str(utc_str: str) -> str:
    """Конвертує рядок часу з БД (UTC) у зручний формат за Києвом (DD.MM at HH:MM)."""
    try:
        dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC_TZ)
        dt_kyiv = dt_utc.astimezone(KYIV_TZ)
        return dt_kyiv.strftime("%d.%m at %H:%M")
    except Exception:
        return utc_str


def parse_strict_kyiv_date(text: str) -> tuple[date | None, str | None]:
    """
    Парсить дату у форматі DD.MM.YYYY та перевіряє, що вона не в минулому (за Києвом).
    Повертає (date_obj, None) або (None, error_message).
    """
    raw = text.strip()
    # Сувора перевірка формату (відсікає 2-значний рік, зайві роздільники та пропущені нулі)
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw):
        example = datetime.now(KYIV_TZ).strftime("%d.%m.%Y")
        return None, (
            "❌ Invalid date format.\n"
            f"Please use strict format <code>DD.MM.YYYY</code> (e.g., <code>{example}</code>):"
        )
    try:
        parsed_date = datetime.strptime(raw, "%d.%m.%Y").date()
    except ValueError:
        return None, (
            "❌ This calendar date does not exist.\n"
            "Check the day and month and enter the date again (<code>DD.MM.YYYY</code>):"
        )
    today_kyiv = datetime.now(KYIV_TZ).date()
    if parsed_date < today_kyiv:
        return None, (
            "❌ Departure date cannot be in the past.\n"
            f"Enter today's date (<code>{today_kyiv.strftime('%d.%m.%Y')}</code>) or a future date:"
        )
    return parsed_date, None


def parse_strict_kyiv_time(text: str) -> tuple[time | None, str | None]:
    """
    Парсить час у 24-годинному форматі HH:MM.
    Повертає (time_obj, None) або (None, error_message).
    """
    raw = text.strip()
    # Захищає від пастки strptime, де '09:5' мовчки парситься як 09:05
    if not re.fullmatch(r"\d{2}:\d{2}", raw):
        return None, (
            "❌ Invalid time format.\n"
            "Please use strict 24-hour format <code>HH:MM</code> (e.g., <code>08:30</code> or <code>14:00</code>):"
        )
    try:
        parsed_time = datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None, (
            "❌ Invalid time value (hours must be 00–23, minutes 00–59).\n"
            "Please enter the time again in format <code>HH:MM</code>:"
        )
    return parsed_time, None


def format_crew_list(crew: list[dict]) -> str:
    """Форматує список учасників поїздки із зазначенням відділу та старшого групи (HTML-безпечно)."""
    formatted = []
    for m in crew:
        tags = []
        if m.get("team"):
            tags.append(escape(m["team"]))
        if m.get("is_leader"):
            tags.append("Team Leader")
        tag_str = f" ({', '.join(tags)})" if tags else ""
        formatted.append(f"{escape(m['full_name'])}{tag_str}")
    return "\n".join(formatted)


def format_interval(interval_min: int | None) -> str:
    """Перетворює числовий інтервал у текстове представлення для інтерфейсу."""
    if not interval_min or interval_min <= 0:
        return "Start / Finish only"
    return f"every {interval_min} min"


def _is_crew_member(trip: dict, user_id: int) -> bool:
    """Перевіряє, чи входить користувач до складу екіпажу цієї поїздки."""
    return any(m["id"] == user_id for m in trip["crew"])


def format_trip_card(trip: dict) -> str:
    """Формує картку поїздки залежно від її статусу (PLANNED або ACTIVE)."""
    crew_names = format_crew_list(trip["crew"])
    origin_safe = escape(trip["origin"])
    dest_safe = escape(trip["destination"])
    interval_label = format_interval(trip.get("checkin_interval_min"))
    if trip["status"] == "PLANNED":
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        return (
            f"📅 <b>Planned trip #{trip['id']}</b>\n"
            f"📍 Route: <b>{origin_safe} → {dest_safe}</b>\n"
            f"👥 Crew:\n{crew_names}\n"
            f"🕒 Departure: <b>{dep_str}</b>\n"
            f"⏱ Check-in interval: <b>{interval_label}</b>\n\n"
            "<i>Monitoring will begin after you confirm departure using the button below.</i>"
        )
    minutes_ago = max(0, trip["minutes_ago"] or 0)
    last_by_safe = escape(trip["last_checkin_user_name"] or "Trip start")
    location_line = (
        f"\n📌 Last location: <i>{escape(trip['last_checkin_note'])}</i>"
        if trip.get("last_checkin_note") else ""
    )
    ping_warning = (
        "\n⚠️ <b>Monitoring is awaiting your check-in with location!</b>\n"
        if trip["pending_request_sent_at"] else ""
    )
    help_warning = ""
    if trip.get("help_requested_at"):
        help_time = utc_str_to_kyiv_str(trip["help_requested_at"])
        help_by = escape(trip.get("help_requested_user_name") or "Crew member")
        help_note_part = (
            f"\n💬 Details: <i>{escape(trip['help_request_note'])}</i>"
            if trip.get("help_request_note") else ""
        )
        help_warning = (
            f"\n\n🚨 <b>ACTIVE HELP REQUEST ({help_time}, by {help_by})</b>"
            f"{help_note_part}\n"
            "<i>Tap \"Issue resolved\" once the situation is under control.</i>"
        )
    return (
        f"🚗 <b>Active trip #{trip['id']}</b>\n"
        f"📍 Route: <b>{origin_safe} → {dest_safe}</b>\n"
        f"👥 Crew:\n{crew_names}\n"
        f"⏱ Check-in interval: <b>{interval_label}</b>\n"
        f"🕒 Last check-in: <b>{minutes_ago} min ago</b> ({last_by_safe})"
        f"{location_line}"
        f"{ping_warning}"
        f"{help_warning}"
    )


async def notify_monitoring(bot: Bot, text: str, reply_markup=None):
    """Надсилає повідомлення в групу моніторингу (або операторам у приват, якщо група не задана)."""
    if MONITORING_GROUP_ID is not None:
        try:
            await bot.send_message(
                chat_id=MONITORING_GROUP_ID,
                text=text,
                reply_markup=reply_markup
            )
            return
        except Exception as e:
            logger.warning(f"Failed to send message to MONITORING_GROUP_ID={MONITORING_GROUP_ID}: {e}")
    operator_ids = await get_operators_tg_ids(DB_PATH)
    for op_tg_id in operator_ids:
        try:
            await bot.send_message(chat_id=op_tg_id, text=text, reply_markup=reply_markup)
        except Exception as e:
            logger.warning(f"Failed to notify operator tg_id={op_tg_id}: {e}")


async def notify_other_crew_members(bot: Bot, trip: dict, actor_user_id: int, text: str, reply_markup=None):
    """Сповіщає інших учасників екіпажу."""
    for member in trip["crew"]:
        if member["id"] != actor_user_id:
            try:
                await bot.send_message(
                    chat_id=member["tg_id"],
                    text=text,
                    reply_markup=reply_markup
                )
            except Exception as e:
                logger.warning(
                    f"Failed to notify crew member tg_id={member['tg_id']} "
                    f"(trip #{trip['id']}): {e}"
                )


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
        return
    await state.clear()
    await message.answer(
        "🚗 <b>New Trip Setup</b>\n\n"
        "Step 1/6. Enter the <b>point of departure</b> (e.g., <i>Kyiv</i>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_origin)


@traveler_router.message(or_f(Command("my_trip"), F.text == BTN_MY_TRIP))
async def cmd_my_trip(message: Message, db_user: dict | None, state: FSMContext):
    await state.clear()
    if not db_user:
        return
    trips = await get_user_active_trips(DB_PATH, db_user["id"])
    if not trips:
        await message.answer(
            "🟢 You have no active or planned trips.\n"
            f"Tap <b>\"{escape(BTN_NEW_TRIP)}\"</b> in the menu to create a trip."
        )
        return
    for trip in trips:
        kb = (
            build_planned_trip_keyboard(trip["id"])
            if trip["status"] == "PLANNED"
            else build_traveler_trip_keyboard(
                trip["id"],
                has_active_help=bool(trip.get("help_requested_at"))
            )
        )
        await message.answer(text=format_trip_card(trip), reply_markup=kb)


# =====================================================================
# КРОКИ FSM СТВОРЕННЯ ПОЇЗДКИ (Текстові + Інлайн-кнопки)
# =====================================================================
@traveler_router.callback_query(F.data == "trip_create_cancel")
async def cb_cancel_creation(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() is None:
        await callback.answer("The process has already been completed.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        return
    await callback.answer("Cancelled")
    await state.clear()
    await callback.message.edit_text("❌ Trip creation cancelled.")


@traveler_router.message(NewTripFSM.waiting_for_origin, F.text)
async def process_origin(message: Message, state: FSMContext):
    origin = message.text.strip()
    if not origin or len(origin) > MAX_PLACE_LENGTH:
        await message.answer(
            f"❌ Please enter a valid point of departure (up to {MAX_PLACE_LENGTH} characters):",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    await state.update_data(origin=origin)
    await message.answer(
        f"Departure: <b>{escape(origin)}</b>\n\n"
        "Step 2/6. Enter the <b>destination</b> (e.g., <i>Dnipro</i>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_destination)


@traveler_router.message(NewTripFSM.waiting_for_destination, F.text)
async def process_destination(message: Message, state: FSMContext):
    destination = message.text.strip()
    if not destination or len(destination) > MAX_PLACE_LENGTH:
        await message.answer(
            f"❌ Please enter a valid destination (up to {MAX_PLACE_LENGTH} characters):",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    data = await state.get_data()
    origin = data.get("origin", "")
    await state.update_data(destination=destination)
    today_str = datetime.now(KYIV_TZ).strftime("%d.%m.%Y")
    await message.answer(
        f"Route: <b>{escape(origin)} → {escape(destination)}</b>\n\n"
        "Step 3/6. Enter the <b>departure date</b> (Kyiv time) in format <code>DD.MM.YYYY</code> "
        f"(e.g., <code>{today_str}</code>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_departure_date)


@traveler_router.message(NewTripFSM.waiting_for_departure_date)
async def process_departure_date(message: Message, state: FSMContext):
    # Обробка нетекстових повідомлень (фото/стікер/геолокація), щоб користувач не "зависав"
    if not message.text:
        await message.answer(
            "❌ Please send the departure date as text in format <code>DD.MM.YYYY</code>:",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    parsed_date, error_msg = parse_strict_kyiv_date(message.text)
    if not parsed_date:
        logger.warning(f"Invalid departure date input: '{message.text.strip()}'")
        await message.answer(error_msg, reply_markup=build_cancel_creation_keyboard())
        return
    date_display = parsed_date.strftime("%d.%m.%Y")
    await state.update_data(
        departure_date_iso=parsed_date.isoformat(),
        departure_date_display=date_display
    )
    await message.answer(
        f"📅 Departure date: <b>{date_display}</b>\n\n"
        "Step 4/6. Enter the <b>departure time</b> (Kyiv time) in 24-hour format <code>HH:MM</code> "
        "(e.g., <code>08:30</code> or <code>14:00</code>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_departure_time)


@traveler_router.message(NewTripFSM.waiting_for_departure_time)
async def process_departure_time(message: Message, state: FSMContext):
    if not message.text:
        await message.answer(
            "❌ Please send the departure time as text in format <code>HH:MM</code>:",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    parsed_time, error_msg = parse_strict_kyiv_time(message.text)
    if not parsed_time:
        logger.warning(f"Invalid departure time input: '{message.text.strip()}'")
        await message.answer(error_msg, reply_markup=build_cancel_creation_keyboard())
        return
    data = await state.get_data()
    date_iso = data.get("departure_date_iso")
    if not date_iso:
        await state.clear()
        await message.answer("❌ Session expired. Please start trip setup again.")
        return
    parsed_date = date.fromisoformat(date_iso)
    dt_kyiv = datetime.combine(parsed_date, parsed_time, tzinfo=KYIV_TZ)
    # Захист від одруківки часу для сьогоднішньої дати (напр. 08:30 замість 18:30) із 5-хв буфером
    now_kyiv = datetime.now(KYIV_TZ)
    if dt_kyiv < now_kyiv - timedelta(minutes=5):
        await message.answer(
            f"❌ Time <b>{parsed_time.strftime('%H:%M')}</b> on <b>{data['departure_date_display']}</b> "
            "has already passed.\n"
            "Enter a future time (<code>HH:MM</code>) or tap Cancel to choose another date:",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    dt_utc = dt_kyiv.astimezone(UTC_TZ)
    kyiv_label = dt_kyiv.strftime("%d.%m.%Y at %H:%M")
    await state.update_data(
        planned_start_utc=dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
        kyiv_departure_label=kyiv_label
    )
    await message.answer(
        f"🕒 Departure: <b>{kyiv_label}</b>\n\n"
        "Step 5/6. Select the <b>check-in interval</b> according to the route risk level:",
        reply_markup=build_interval_selection_keyboard(
            callback_prefix="trip_set_interval",
            cancel_callback="trip_create_cancel",
            cancel_text="❌ Cancel creation"
        )
    )
    await state.set_state(NewTripFSM.waiting_for_interval)


@traveler_router.callback_query(NewTripFSM.waiting_for_interval, F.data.startswith("trip_set_interval:"))
async def process_interval_selection(callback: CallbackQuery, db_user: dict, state: FSMContext):
    await callback.answer()
    interval_min = int(callback.data.split(":")[1])
    all_users = await get_all_users(DB_PATH)
    other_users = [u for u in all_users if u["id"] != db_user["id"]]
    await state.update_data(
        checkin_interval_min=interval_min,
        other_users=other_users,
        selected_companion_ids=[],
        comp_page=0
    )
    await callback.message.edit_text(
        f"⏱ Check-in interval: <b>{format_interval(interval_min)}</b>\n\n"
        f"Step 6/6. You (<b>{escape(db_user['full_name'])}</b>) are automatically assigned as the trip leader.\n"
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
    try:
        await callback.message.edit_reply_markup(
            reply_markup=build_companions_keyboard(other_users, selected_ids, page=page)
        )
    except TelegramBadRequest:
        pass


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
    try:
        await callback.message.edit_reply_markup(
            reply_markup=build_companions_keyboard(other_users, selected_ids, page=current_page)
        )
    except TelegramBadRequest:
        pass


@traveler_router.callback_query(NewTripFSM.waiting_for_companions, F.data == "trip_confirm_companions")
async def finalize_planned_trip(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    data = await state.get_data()
    # Захист від подвійного кліку, коли перший запит уже очистив FSM
    if not data.get("origin") or not data.get("destination") or not data.get("planned_start_utc"):
        await callback.answer("This trip has already been submitted.", show_alert=True)
        return
    await state.clear()
    await callback.answer("Trip planned!")
    interval_min = data.get("checkin_interval_min", CHECKIN_INTERVAL_MIN)
    trip_id = await create_trip(
        db_path=DB_PATH,
        origin=data["origin"],
        destination=data["destination"],
        creator_user_id=db_user["id"],
        companion_ids=data.get("selected_companion_ids", []),
        planned_start_utc=data["planned_start_utc"],
        interval_min=interval_min
    )
    if not trip_id:
        await callback.message.edit_text("❌ Error saving the trip to the database.")
        return
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip:
        await callback.message.edit_text("❌ Trip was created, but failed to load its details.")
        return
    trip_card = format_trip_card(trip)
    planned_kb = build_planned_trip_keyboard(trip_id)
    actor_name_safe = escape(db_user["full_name"])
    # Показуємо картку запланованої поїздки ініціатору
    await callback.message.edit_text(text=trip_card, reply_markup=planned_kb)
    # Розсилаємо картку іншим учасникам групи (якщо вони є)
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"📅 <b>{actor_name_safe} added you to planned trip #{trip_id}</b>\n\n"
            + trip_card
        ),
        reply_markup=planned_kb
    )
    crew_names = format_crew_list(trip["crew"])
    dep_str = utc_str_to_kyiv_str(trip["started_at"])
    # Сповіщаємо групу моніторингу про заплановану поїздку
    await notify_monitoring(
        bot,
        text=(
            f"📅 <b>NEW TRIP PLANNED #{trip_id}</b>\n"
            f"📍 Route: <b>{escape(trip['route_code'])}</b>\n"
            f"👥 Crew:\n{crew_names}\n"
            f"🕒 Planned departure: <b>{dep_str}</b>\n"
            f"⏱ Check-in interval: <b>{format_interval(trip.get('checkin_interval_min'))}</b>\n"
            f"👤 Created by: <b>{actor_name_safe}</b>"
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
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "PLANNED":
        await callback.answer("The trip is already active or has been cancelled.", show_alert=True)
        return
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    activated = await activate_trip(DB_PATH, trip_id, actor_user_id=db_user["id"])
    if not activated:
        await callback.answer("The trip is already active or has been cancelled.", show_alert=True)
        return
    await callback.answer("🚀 Trip started! Have a safe trip!")
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip:
        return
    trip_card = format_trip_card(trip)
    active_kb = build_traveler_trip_keyboard(trip_id)
    actor_name_safe = escape(db_user["full_name"])
    await callback.message.edit_text(
        text=trip_card + "\n\n🚀 <i>Monitoring started!</i>",
        reply_markup=active_kb
    )
    crew_names = format_crew_list(trip["crew"])
    await notify_monitoring(
        bot,
        text=(
            f"🚀 <b>TRIP STARTED #{trip_id}</b> ({escape(trip['route_code'])})\n"
            f"👥 Crew:\n{crew_names}\n"
            f"▶️ Started by: <b>{actor_name_safe}</b>\n"
            f"⏱ Check-in interval: <b>{format_interval(trip.get('checkin_interval_min'))}</b>"
        )
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🚀 <b>Trip #{trip_id} started!</b> ({actor_name_safe})\n\n" + trip_card,
        reply_markup=active_kb
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
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    cancelled = await cancel_planned_trip(DB_PATH, trip_id, db_user["id"])
    if not cancelled:
        await callback.answer("Failed to cancel the trip.", show_alert=True)
        return
    route_safe = escape(trip["route_code"])
    actor_name_safe = escape(db_user["full_name"])
    await callback.answer("Trip cancelled")
    await callback.message.edit_text(
        f"❌ <b>Planned trip #{trip_id} ({route_safe}) has been cancelled.</b>"
    )
    # Сповіщаємо інших учасників екіпажу
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"❌ Planned trip #{trip_id} ({route_safe}) was cancelled by {actor_name_safe}."
    )
    # Сповіщаємо групу моніторингу (щоб закрити запис про запланований виїзд у стрічці)
    crew_names = format_crew_list(trip["crew"])
    dep_str = utc_str_to_kyiv_str(trip["started_at"])
    await notify_monitoring(
        bot,
        text=(
            f"❌ <b>PLANNED TRIP CANCELLED #{trip_id}</b>\n"
            f"📍 Route: <b>{route_safe}</b>\n"
            f"👥 Crew:\n{crew_names}\n"
            f"🕒 Planned departure was: <b>{dep_str}</b>\n"
            f"👤 Cancelled by: <b>{actor_name_safe}</b>"
        )
    )


# =====================================================================
# ЧЕКІНИ, ЗАПИТ ДОПОМОГИ ТА ЗАКРИТТЯ ПОЇЗДКИ
# =====================================================================
@traveler_router.callback_query(F.data.startswith("trv_refresh:"))
async def refresh_trip_card(callback: CallbackQuery, db_user: dict | None, state: FSMContext):
    if not db_user:
        return
    await state.clear()
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("ACTIVE", "PLANNED"):
        await callback.answer("This trip has already been completed.", show_alert=True)
        try:
            await callback.message.edit_text("🏁 Trip completed.")
        except TelegramBadRequest:
            pass
        return
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    kb = (
        build_planned_trip_keyboard(trip_id)
        if trip["status"] == "PLANNED"
        else build_traveler_trip_keyboard(
            trip_id,
            has_active_help=bool(trip.get("help_requested_at"))
        )
    )
    await callback.answer("Status updated")
    try:
        await callback.message.edit_text(text=format_trip_card(trip), reply_markup=kb)
    except TelegramBadRequest:
        pass


# --- Чекін із обов'язковим коментарем ---
@traveler_router.callback_query(F.data.startswith("trv_note:"))
async def ask_checkin_note(callback: CallbackQuery, db_user: dict | None, state: FSMContext):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is not active.", show_alert=True)
        return
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return

    await callback.answer()
    await state.update_data(note_trip_id=trip_id)
    await state.set_state(TravelerNoteFSM.waiting_for_note)
    await callback.message.edit_text(
        f"📍 <b>Check-in for trip #{trip_id} ({escape(trip['route_code'])})</b>\n\n"
        "Specify your <b>approximate location</b> or current status "
        "(e.g., <i>\"passed Pryluky\"</i> or <i>\"in Chernihiv, 15 min stop\"</i>):",
        reply_markup=build_cancel_note_keyboard(trip_id)
    )


@traveler_router.message(TravelerNoteFSM.waiting_for_note, F.text)
async def process_checkin_note(message: Message, db_user: dict | None, state: FSMContext, bot: Bot):
    if not db_user:
        return
    location_note = message.text.strip()
    data = await state.get_data()
    trip_id = data.get("note_trip_id")
    if not trip_id:
        await state.clear()
        return
    if not location_note or len(location_note) > MAX_NOTE_LENGTH:
        await message.answer(
            f"❌ Please enter a valid location or status (up to {MAX_NOTE_LENGTH} characters):",
            reply_markup=build_cancel_note_keyboard(trip_id)
        )
        return
    await state.clear()
    event_type = await record_checkin(DB_PATH, trip_id, db_user["id"], note=location_note)
    if not event_type:
        await message.answer("🏁 This trip has already been completed.")
        return
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip:
        return
    note_safe = escape(location_note)
    route_safe = escape(trip["route_code"])
    actor_name_safe = escape(db_user["full_name"])
    await message.answer(
        text=format_trip_card(trip) + f"\n\n✅ <i>Check-in recorded: \"{note_safe}\"</i>",
        reply_markup=build_traveler_trip_keyboard(
            trip_id,
            has_active_help=bool(trip.get("help_requested_at"))
        )
    )
    kind_label = "requested by operator" if event_type == "REQUESTED_CHECKIN" else "scheduled"
    interval_min = trip.get("checkin_interval_min") or 0
    next_check_line = (
        f"⏱ Next check-in in {interval_min} min."
        if interval_min > 0
        else "⏱ Mode: <b>Start / Finish only</b>"
    )
    await notify_monitoring(
        bot,
        f"🟢 <b>CHECK-IN — Trip #{trip_id}</b> ({route_safe})\n"
        f"👤 From: <b>{actor_name_safe}</b> ({kind_label})\n"
        f"📍 Location / comment: <b>{note_safe}</b>\n"
        f"{next_check_line}"
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"ℹ️ <b>Trip #{trip_id} ({route_safe}):</b>\n"
            f"{actor_name_safe} checked in: <i>\"{note_safe}\"</i>"
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
    if not trip:
        return None
    crew_names = format_crew_list(trip["crew"])
    actor_name_safe = escape(db_user["full_name"])
    route_safe = escape(trip["route_code"])
    comment_block = (
        f"💬 <b>Details:</b> <i>{escape(comment)}</i>"
        if comment
        else "💬 <b>Details:</b> <i>No comment (urgent alert)</i>"
    )
    # Термінове сповіщення в групу моніторингу
    await notify_monitoring(
        bot,
        f"🚨🆘 <b>NEED HELP — Trip #{trip_id}</b>\n"
        f"📍 Route: <b>{route_safe}</b>\n"
        f"👤 Alert from: <b>{actor_name_safe}</b>\n"
        f"👥 Full crew:\n{crew_names}\n"
        f"{comment_block}\n\n"
        "❗️ <b>Contact the crew immediately!</b>"
    )
    # Сповіщення іншим учасникам цієї поїздки
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"🚨 <b>Trip #{trip_id}:</b> {actor_name_safe} sent a "
            f"<b>\"Need help\"</b> request to monitoring!\n{comment_block}"
        )
    )
    return trip


@traveler_router.callback_query(F.data.startswith("trv_help_ask:"))
async def ask_help_comment(callback: CallbackQuery, db_user: dict | None, state: FSMContext):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is not active.", show_alert=True)
        return
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
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
    trip_id = int(callback.data.split(":")[1])
    trip_check = await get_trip_details(DB_PATH, trip_id)
    if not trip_check or trip_check["status"] != "ACTIVE":
        await state.clear()
        await callback.answer("The trip has already been completed.", show_alert=True)
        return
    if not _is_crew_member(trip_check, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    await state.clear()
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
        reply_markup=build_traveler_trip_keyboard(
            trip_id,
            has_active_help=bool(trip.get("help_requested_at"))
        )
    )


@traveler_router.message(TravelerNoteFSM.waiting_for_help_comment, F.text)
async def process_help_with_comment(message: Message, db_user: dict | None, state: FSMContext, bot: Bot):
    if not db_user:
        return
    comment_text = message.text.strip()
    data = await state.get_data()
    trip_id = data.get("help_trip_id")
    if not trip_id:
        await state.clear()
        return
    if not comment_text or len(comment_text) > MAX_NOTE_LENGTH:
        await message.answer(
            f"❌ Please enter a brief message (up to {MAX_NOTE_LENGTH} characters) or tap the emergency button below:",
            reply_markup=build_help_comment_keyboard(trip_id)
        )
        return
    await state.clear()
    trip = await execute_help_alert(bot, db_user, trip_id, comment=comment_text)
    if not trip:
        await message.answer("🏁 This trip has already been completed.")
        return
    await message.answer(
        text=(
            format_trip_card(trip)
            + f"\n\n🚨 <b>Help request (\"{escape(comment_text)}\") has been forwarded to monitoring! Standby for contact.</b>"
        ),
        reply_markup=build_traveler_trip_keyboard(
            trip_id,
            has_active_help=bool(trip.get("help_requested_at"))
        )
    )


@traveler_router.callback_query(F.data.startswith("trv_resolve_help:"))
async def process_resolve_help(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is no longer active.", show_alert=True)
        return
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    if not trip.get("help_requested_at"):
        await callback.answer("Help request has already been resolved.", show_alert=True)
        try:
            await callback.message.edit_text(
                text=format_trip_card(trip),
                reply_markup=build_traveler_trip_keyboard(trip_id, has_active_help=False)
            )
        except TelegramBadRequest:
            pass
        return
    resolved = await resolve_help_request(
        DB_PATH, trip_id, db_user["id"], note="Resolved by crew member"
    )
    if not resolved:
        await callback.answer("Help request has already been resolved.", show_alert=True)
        return
    updated_trip = await get_trip_details(DB_PATH, trip_id)
    if not updated_trip:
        return
    route_safe = escape(updated_trip["route_code"])
    actor_name_safe = escape(db_user["full_name"])
    trip_card = format_trip_card(updated_trip)
    normal_kb = build_traveler_trip_keyboard(trip_id, has_active_help=False)
    logger.info(
        f"Crew member {db_user['full_name']} (id={db_user['id']}) resolved help request for trip #{trip_id}."
    )
    await callback.answer("✅ Help status cleared!")
    await callback.message.edit_text(
        text=trip_card + "\n\n✅ <i>Help request marked as resolved. Monitoring returned to normal mode.</i>",
        reply_markup=normal_kb
    )
    await notify_monitoring(
        bot,
        text=(
            f"✅ <b>HELP REQUEST RESOLVED — Trip #{trip_id}</b>\n"
            f"📍 Route: <b>{route_safe}</b>\n"
            f"👤 Resolved by crew: <b>{actor_name_safe}</b>\n"
            "🟢 Trip status returned to normal monitoring."
        )
    )
    await notify_other_crew_members(
        bot,
        updated_trip,
        actor_user_id=db_user["id"],
        text=(
            f"✅ <b>Trip #{trip_id} ({route_safe}):</b> "
            f"{actor_name_safe} marked the help request as resolved.\n\n" + trip_card
        ),
        reply_markup=normal_kb
    )


# --- Закриття поїздки ---
@traveler_router.callback_query(F.data.startswith("trv_close_ask:"))
async def ask_close_confirmation(callback: CallbackQuery, db_user: dict | None):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip has already been completed.", show_alert=True)
        return
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🏁 <b>Arrival Confirmation</b>\n\n"
        f"Please confirm that you have arrived in <b>{escape(trip['destination'])}</b> "
        f"and want to close trip #{trip_id}:",
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
    if not _is_crew_member(trip, db_user["id"]):
        await callback.answer("⛔️ You are not a member of this trip.", show_alert=True)
        return
    if not await close_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("Error closing the trip.", show_alert=True)
        return
    route_safe = escape(trip["route_code"])
    actor_name_safe = escape(db_user["full_name"])
    await callback.answer("🏁 Arrival confirmed!")
    await callback.message.edit_text(
        f"🏁 <b>Trip #{trip_id} ({route_safe}) successfully completed!</b>\n"
        f"Closed by: {actor_name_safe}."
    )
    await notify_monitoring(
        bot,
        f"🏁 <b>Trip #{trip_id} COMPLETED</b>\n"
        f"📍 Route: <b>{route_safe}</b>\n"
        f"👤 Arrival confirmed by: <b>{actor_name_safe}</b>"
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🏁 <b>Trip #{trip_id} ({route_safe}) completed!</b> ({actor_name_safe})"
    )
