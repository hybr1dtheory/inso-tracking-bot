from datetime import datetime
from html import escape
import logging
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from config import DB_PATH, KYIV_TZ
from database.queries import (
    activate_trip,
    cancel_planned_trip,
    close_trip,
    get_all_monitored_trips,
    get_trip_details,
    mark_operator_ping_sent,
    update_trip_interval,
)
from handlers.traveler import (
    format_crew_list,
    format_interval,
    format_trip_card,
    notify_monitoring,
    notify_other_crew_members,
    utc_str_to_kyiv_str,
)
from keyboards.inline import (
    build_checkin_response_keyboard,
    build_interval_selection_keyboard,
    build_operator_close_confirm_keyboard,
    build_operator_dashboard_keyboard,
    build_operator_trip_manage_keyboard,
    build_traveler_trip_keyboard,
    get_trip_status_icon,
)
from keyboards.reply import BTN_TRIPS_DASHBOARD


logger = logging.getLogger(__name__)
operator_router = Router()


def is_operator_or_admin(db_user: dict | None) -> bool:
    """Перевіряє, чи має користувач права оператора або адміністратора."""
    return bool(db_user and db_user["role"] in ("OPERATOR", "ADMIN"))


def _format_leader_label(crew: list[dict]) -> str:
    """Повертає HTML-безпечне ім'я старшого групи з відділом для дашборду."""
    for m in crew:
        if m["is_leader"]:
            name_safe = escape(m["full_name"])
            if m.get("team"):
                return f"{name_safe} ({escape(m['team'])})"
            return name_safe
    return "Unknown"


def format_dashboard_text(trips: list[dict]) -> str:
    """Формує зведений текст дашборду всіх активних та запланованих поїздок."""
    now_str = datetime.now(KYIV_TZ).strftime("%H:%M:%S")
    if not trips:
        return (
            f"📋 <b>Trip monitoring</b> (updated at {now_str})\n\n"
            "🟢 There are currently no active or planned trips."
        )
    active_trips = [t for t in trips if t["status"] == "ACTIVE"]
    planned_trips = [t for t in trips if t["status"] == "PLANNED"]
    lines = [
        f"📋 <b>Trip monitoring dashboard</b> (updated at {now_str})",
        f"Active: <b>{len(active_trips)}</b> | Planned: <b>{len(planned_trips)}</b>\n"
    ]
    if active_trips:
        lines.append("🚗 <b>ACTIVE TRIPS:</b>")
        for t in active_trips:
            icon = get_trip_status_icon(t)
            mins = max(0, t["minutes_ago"] or 0)
            interval = t.get("checkin_interval_min") or 0
            crew_count = len(t["crew"])
            leader = _format_leader_label(t["crew"])
            status_note = ""
            if interval > 0 and mins >= interval:
                status_note = " — ⚠️ <b>CHECK-IN OVERDUE!</b>"
            elif t["pending_request_sent_at"]:
                status_note = " — ⏳ <i>Waiting for response</i>"
            checkin_line = (
                f"Check-in: <b>{mins} min ago</b>"
                if interval > 0
                else "Mode: <b>Start / Finish only</b>"
            )
            lines.append(
                f"{icon} <b>#{t['id']} {escape(t['route_code'])}</b> ({crew_count} pax, leader: {leader})\n"
                f"   └ {checkin_line}{status_note}"
            )
        lines.append("")
    if planned_trips:
        lines.append("📅 <b>PLANNED TRIPS:</b>")
        for t in planned_trips:
            dep_str = utc_str_to_kyiv_str(t["started_at"])
            leader = _format_leader_label(t["crew"])
            # Якщо minutes_ago > 0 для PLANNED поїздки — час виїзду вже настав, але старт ще не підтвердили
            delay_mins = t["minutes_ago"] or 0
            delay_note = f" — ⏳ <i>Delayed ({delay_mins} min)</i>" if delay_mins > 0 else ""
            lines.append(
                f"📅 <b>#{t['id']} {escape(t['route_code'])}</b> (leader: {leader})\n"
                f"   └ Departure: <b>{dep_str}</b>{delay_note}"
            )
    lines.append("\n<i>Select a trip below to view details or send a check-in request:</i>")
    return "\n".join(lines)


def format_operator_trip_details(trip: dict) -> str:
    """Формує розширену картку конкретної поїздки для оператора."""
    icon = get_trip_status_icon(trip)
    origin_safe = escape(trip["origin"])
    dest_safe = escape(trip["destination"])
    interval = trip.get("checkin_interval_min") or 0
    interval_label = format_interval(interval)
    crew_lines = []
    for m in trip["crew"]:
        team_tag = f" | 🏢 {escape(m['team'])}" if m.get("team") else ""
        role_tag = " 👑 <i>(Team Leader)</i>" if m["is_leader"] else ""
        crew_lines.append(f"• {escape(m['full_name'])}{team_tag}{role_tag} [ID: <code>{m['tg_id']}</code>]")
    crew_block = "\n".join(crew_lines)
    if trip["status"] == "PLANNED":
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        return (
            f"{icon} <b>Trip #{trip['id']} (PLANNED)</b>\n\n"
            f"📍 Route: <b>{origin_safe} → {dest_safe}</b>\n"
            f"🕒 Scheduled departure: <b>{dep_str}</b>\n"
            f"⏱ Check-in interval: <b>{interval_label}</b>\n\n"
            f"👥 <b>Group members ({len(trip['crew'])}):</b>\n{crew_block}"
        )
    mins = max(0, trip["minutes_ago"] or 0)
    last_by_safe = escape(trip["last_checkin_user_name"] or "Trip start")
    last_time_kyiv = utc_str_to_kyiv_str(trip["last_checkin_at"])
    start_time_kyiv = utc_str_to_kyiv_str(trip["started_at"])
    alert_banner = ""
    if interval > 0 and mins >= interval:
        alert_banner = "\n🔴 <b>ATTENTION: The check-in interval has been exceeded! Send a request to the group.</b>\n"
    elif trip["pending_request_sent_at"]:
        ping_time_kyiv = utc_str_to_kyiv_str(trip["pending_request_sent_at"])
        alert_banner = f"\n🟡 <b>Request sent ({ping_time_kyiv}). Awaiting confirmation from the group.</b>\n"

    location_str = (
        f"📌 Last comment: <b>{escape(trip['last_checkin_note'])}</b>\n"
        if trip.get("last_checkin_note") else ""
    )
    return (
        f"{icon} <b>Trip #{trip['id']} (ACTIVE)</b>\n"
        f"{alert_banner}\n"
        f"📍 Route: <b>{origin_safe} → {dest_safe}</b>\n"
        f"🚀 Actual start: <b>{start_time_kyiv}</b>\n"
        f"🕒 Last check-in: <b>{mins} min ago</b> ({last_time_kyiv}, from: {last_by_safe})\n"
        f"{location_str}"
        f"⏱ Check-in interval: <b>{interval_label}</b>\n\n"
        f"👥 <b>Group members ({len(trip['crew'])}):</b>\n{crew_block}"
    )


async def send_ping_to_crew(bot: Bot, trip: dict, operator_name: str) -> int:
    """Розсилає запит на чекін усім учасникам поїздки. Повертає кількість доставлених повідомлень."""
    sent_count = 0
    trip_id = trip["id"]
    route_safe = escape(trip["route_code"])
    operator_safe = escape(operator_name)
    keyboard = build_checkin_response_keyboard(trip_id)
    for member in trip["crew"]:
        try:
            await bot.send_message(
                chat_id=member["tg_id"],
                text=(
                    f"📡 <b>Check-in request from the monitoring team!</b>\n\n"
                    f"🚗 Trip #{trip_id}: <b>{route_safe}</b>\n"
                    f"Duty operator (<b>{operator_safe}</b>) asks you to confirm "
                    "that everything is okay and your trip is going as planned:"
                ),
                reply_markup=keyboard
            )
            sent_count += 1
        except Exception as e:
            logger.warning(
                f"Failed to send check-in ping to user tg_id={member['tg_id']} "
                f"(trip #{trip_id}): {e}"
            )
    return sent_count


# =================================================
# ГОЛОВНИЙ ДАШБОРД (/trips та кнопка оновлення)
# =================================================
@operator_router.message(or_f(Command("trips"), F.text == BTN_TRIPS_DASHBOARD))
async def cmd_trips_dashboard(message: Message, db_user: dict | None, state: FSMContext):
    await state.clear()
    if not is_operator_or_admin(db_user):
        await message.answer("⛔️ This panel is available only to monitoring operators and administrators.")
        return
    trips = await get_all_monitored_trips(DB_PATH)
    await message.answer(
        text=format_dashboard_text(trips),
        reply_markup=build_operator_dashboard_keyboard(trips)
    )


@operator_router.callback_query(F.data == "op_dashboard_refresh")
async def cb_refresh_dashboard(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Access denied.", show_alert=True)
        return
    trips = await get_all_monitored_trips(DB_PATH)
    await callback.answer("Dashboard updated")
    try:
        await callback.message.edit_text(
            text=format_dashboard_text(trips),
            reply_markup=build_operator_dashboard_keyboard(trips)
        )
    except TelegramBadRequest:
        pass


# =======================================
# ПЕРЕГЛЯД КАРТКИ КОНКРЕТНОЇ ПОЇЗДКИ
# =======================================
@operator_router.callback_query(F.data.startswith("op_view_trip:"))
async def cb_view_trip_details(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Access denied.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("ACTIVE", "PLANNED"):
        await callback.answer("This trip has already ended or been cancelled.", show_alert=True)
        trips = await get_all_monitored_trips(DB_PATH)
        try:
            await callback.message.edit_text(
                text=format_dashboard_text(trips),
                reply_markup=build_operator_dashboard_keyboard(trips)
            )
        except TelegramBadRequest:
            pass
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            text=format_operator_trip_details(trip),
            reply_markup=build_operator_trip_manage_keyboard(trip)
        )
    except TelegramBadRequest:
        pass


# ===============================
# НАДСИЛАННЯ ЗАПИТУ НА ЧЕКІН
# ===============================
@operator_router.callback_query(F.data.startswith("op_card_ping:"))
async def cb_ping_from_card(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    """Надсилання запиту на чекін зсередини детальної картки дашборду."""
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ For monitoring operators only.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is no longer active.", show_alert=True)
        return
    if not await mark_operator_ping_sent(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("The trip is no longer active.", show_alert=True)
        return
    sent_count = await send_ping_to_crew(bot, trip, db_user["full_name"])
    await callback.answer(f"📡 Check-in request sent to {sent_count} crew member(s).", show_alert=True)
    # Оновлюємо картку поїздки, щоб одразу з'явився жовтий статус очікування
    updated_trip = await get_trip_details(DB_PATH, trip_id)
    if not updated_trip:
        return
    try:
        await callback.message.edit_text(
            text=format_operator_trip_details(updated_trip),
            reply_markup=build_operator_trip_manage_keyboard(updated_trip)
        )
    except TelegramBadRequest:
        pass


# =====================================================================
# РУЧНЕ КЕРУВАННЯ ПОЇЗДКОЮ ОПЕРАТОРОМ (Старт / Скасування / Закриття / Інтервал)
# =====================================================================
@operator_router.callback_query(F.data.startswith("op_force_start:"))
async def cb_operator_force_start(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Access denied.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    if not await activate_trip(DB_PATH, trip_id, actor_user_id=db_user["id"]):
        await callback.answer("The trip is already active or has been cancelled.", show_alert=True)
        return
    await callback.answer("🚀 Trip tracking has begun!")
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip:
        return
    operator_safe = escape(db_user["full_name"])
    crew_names = format_crew_list(trip["crew"])
    # Розсилаємо активну картку всім учасникам
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=-1,  # -1 щоб сповіщення отримали всі члени екіпажу, включно зі старшим
        text=(
            f"🚀 <b>Operator {operator_safe} activated tracking for trip #{trip_id}!</b>\n\n"
            + format_trip_card(trip)
        ),
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    # Фіксуємо старт у групі моніторингу
    await notify_monitoring(
        bot,
        text=(
            f"🚀 <b>TRIP STARTED #{trip_id}</b> ({escape(trip['route_code'])})\n"
            f"👥 Crew:\n{crew_names}\n"
            f"▶️ Started by operator: <b>{operator_safe}</b>\n"
            f"⏱ Check-in interval: <b>{format_interval(trip.get('checkin_interval_min'))}</b>"
        )
    )
    await callback.message.edit_text(
        text=format_operator_trip_details(trip),
        reply_markup=build_operator_trip_manage_keyboard(trip)
    )


@operator_router.callback_query(F.data.startswith("op_force_cancel:"))
async def cb_operator_force_cancel(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Access denied.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or not await cancel_planned_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("Unable to cancel the trip.", show_alert=True)
        return
    route_safe = escape(trip["route_code"])
    operator_safe = escape(db_user["full_name"])
    await callback.answer("The planned trip has been cancelled.")
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=-1,
        text=(
            f"❌ Planned trip #{trip_id} ({route_safe}) "
            f"has been cancelled by operator {operator_safe}."
        )
    )
    await notify_monitoring(
        bot,
        text=(
            f"❌ <b>PLANNED TRIP CANCELLED #{trip_id}</b>\n"
            f"📍 Route: <b>{route_safe}</b>\n"
            f"👤 Cancelled by operator: <b>{operator_safe}</b>"
        )
    )
    trips = await get_all_monitored_trips(DB_PATH)
    await callback.message.edit_text(
        text=format_dashboard_text(trips),
        reply_markup=build_operator_dashboard_keyboard(trips)
    )


@operator_router.callback_query(F.data.startswith("op_force_close_ask:"))
async def cb_operator_force_close_ask(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Access denied.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("The trip is already over.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🏁 <b>Force close trip #{trip_id}</b>\n\n"
        f"Route: <b>{escape(trip['route_code'])}</b>\n"
        "Are you sure you want to close this trip on behalf of the monitoring team?",
        reply_markup=build_operator_close_confirm_keyboard(trip_id)
    )


@operator_router.callback_query(F.data.startswith("op_force_close_confirm:"))
async def cb_operator_force_close_confirm(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Access denied.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or not await close_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("The trip has already been closed.", show_alert=True)
        return
    route_safe = escape(trip["route_code"])
    operator_safe = escape(db_user["full_name"])
    await callback.answer("🏁 Trip closed.")
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=-1,
        text=f"🏁 <b>Trip #{trip_id} ({route_safe}) has been closed by operator {operator_safe}.</b>"
    )
    # Фіксуємо примусове закриття у групі моніторингу
    await notify_monitoring(
        bot,
        text=(
            f"🏁 <b>Trip #{trip_id} COMPLETED (by operator)</b>\n"
            f"📍 Route: <b>{route_safe}</b>\n"
            f"👤 Closed by operator: <b>{operator_safe}</b>"
        )
    )
    # Повертаємо оператора на оновлений дашборд
    trips = await get_all_monitored_trips(DB_PATH)
    await callback.message.edit_text(
        text=format_dashboard_text(trips),
        reply_markup=build_operator_dashboard_keyboard(trips)
    )


@operator_router.callback_query(F.data.startswith("op_edit_interval:"))
async def cb_operator_edit_interval(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Insufficient permissions.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("PLANNED", "ACTIVE"):
        await callback.answer("This trip is no longer active or planned.", show_alert=True)
        return
    await callback.answer()
    current_label = format_interval(trip.get("checkin_interval_min"))
    await callback.message.edit_text(
        f"⏱ <b>Change Check-in Interval — Trip #{trip_id}</b>\n"
        f"📍 Route: <b>{escape(trip['route_code'])}</b>\n"
        f"Current interval: <b>{current_label}</b>\n\n"
        "Select a new check-in interval:",
        reply_markup=build_interval_selection_keyboard(
            callback_prefix=f"op_set_interval:{trip_id}",
            cancel_callback=f"op_view_trip:{trip_id}",
            current_interval=trip.get("checkin_interval_min"),
            cancel_text="⬅️ Back to trip card"
        )
    )


@operator_router.callback_query(F.data.startswith("op_set_interval:"))
async def cb_operator_save_interval(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Insufficient permissions.", show_alert=True)
        return
    _, trip_id_str, new_interval_str = callback.data.split(":")
    trip_id = int(trip_id_str)
    new_interval = int(new_interval_str)
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("PLANNED", "ACTIVE"):
        await callback.answer("This trip is already completed or cancelled.", show_alert=True)
        return
    old_interval = trip.get("checkin_interval_min") or 0
    if old_interval == new_interval:
        await callback.answer("Interval is already set to this value.")
        try:
            await callback.message.edit_text(
                text=format_operator_trip_details(trip),
                reply_markup=build_operator_trip_manage_keyboard(trip)
            )
        except TelegramBadRequest:
            pass
        return
    updated = await update_trip_interval(DB_PATH, trip_id, new_interval, db_user["id"])
    if not updated:
        await callback.answer("❌ Failed to update check-in interval.", show_alert=True)
        return
    old_label = format_interval(old_interval)
    new_label = format_interval(new_interval)
    logger.info(
        f"Operator {db_user['full_name']} (id={db_user['id']}) changed check-in interval "
        f"for trip #{trip_id} from '{old_label}' to '{new_label}'."
    )
    updated_trip = await get_trip_details(DB_PATH, trip_id)
    if not updated_trip:
        return
    route_safe = escape(updated_trip["route_code"])
    operator_safe = escape(db_user["full_name"])
    await callback.answer(f"✅ Interval updated to: {new_label}")
    # Оновлюємо картку поїздки у самого оператора
    await callback.message.edit_text(
        text=format_operator_trip_details(updated_trip),
        reply_markup=build_operator_trip_manage_keyboard(updated_trip)
    )
    # Сповіщаємо всіх учасників екіпажу про зміну нормативу чекіну
    await notify_other_crew_members(
        bot,
        updated_trip,
        actor_user_id=-1,
        text=(
            f"🔔 <b>Check-in interval updated — Trip #{trip_id} ({route_safe})</b>\n\n"
            f"Operator <b>{operator_safe}</b> changed your check-in interval:\n"
            f"• Previous: <i>{old_label}</i>\n"
            f"• New: <b>{new_label}</b>"
        )
    )
    # Логуємо зміну в групі моніторингу
    await notify_monitoring(
        bot,
        text=(
            f"⏱ <b>CHECK-IN INTERVAL UPDATED — Trip #{trip_id}</b>\n"
            f"📍 Route: <b>{route_safe}</b>\n"
            f"🔄 Changed: <i>{old_label}</i> → <b>{new_label}</b>\n"
            f"👤 Operator: <b>{operator_safe}</b>"
        )
    )
