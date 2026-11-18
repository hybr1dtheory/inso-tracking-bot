import logging
from datetime import datetime
from aiogram import Bot, Router, F
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from config import DB_PATH, KYIV_TZ
from keyboards.reply import BTN_TRIPS_DASHBOARD
from database.queries import (
    get_all_monitored_trips,
    get_trip_details,
    mark_operator_ping_sent,
    activate_trip,
    close_trip,
    cancel_planned_trip
)
from handlers.traveler import (
    utc_str_to_kyiv_str,
    format_trip_card,
    notify_other_crew_members
)
from keyboards.inline import (
    get_trip_status_icon,
    build_checkin_response_keyboard,
    build_operator_dashboard_keyboard,
    build_operator_trip_manage_keyboard,
    build_operator_close_confirm_keyboard,
    build_traveler_trip_keyboard
)


logger = logging.getLogger(__name__)
operator_router = Router()


def is_operator_or_admin(db_user: dict | None) -> bool:
    return bool(db_user and db_user["role"] in ("OPERATOR", "ADMIN"))


def format_dashboard_text(trips: list[dict]) -> str:
    '''Формує зведений текст дашборду всіх активних та запланованих поїздок.'''
    now_str = datetime.now(KYIV_TZ).strftime("%H:%M:%S")
    if not trips:
        return (
            f"📋 <b>Моніторинг поїздок</b> (оновлено о {now_str})\n\n"
            "🟢 Наразі немає активних або запланованих відряджень."
        )
    active_trips = [t for t in trips if t["status"] == "ACTIVE"]
    planned_trips = [t for t in trips if t["status"] == "PLANNED"]
    lines = [
        f"📋 <b>Панель моніторингу поїздок</b> (станом на {now_str})",
        f"Активних: <b>{len(active_trips)}</b> | Запланованих: <b>{len(planned_trips)}</b>\n"
    ]
    if active_trips:
        lines.append("🚗 <b>АКТИВНІ ВІДРЯДЖЕННЯ:</b>")
        for t in active_trips:
            icon = get_trip_status_icon(t)
            mins = max(0, t["minutes_ago"] or 0)
            crew_count = len(t["crew"])
            leader = next((m["full_name"] for m in t["crew"] if m["is_leader"]), "Невідомо")
            status_note = ""
            if mins >= t["checkin_interval_min"]:
                status_note = " — ⚠️ <b>ЧАС ЧЕКІНУ ВИЙШОВ!</b>"
            elif t["pending_request_sent_at"]:
                status_note = " — ⏳ <i>Очікується відповідь</i>"
            lines.append(
                f"{icon} <b>#{t['id']} {t['route_code']}</b> ({crew_count} ос., ст.: {leader})\n"
                f"   └ Чекін: <b>{mins} хв тому</b>{status_note}"
            )
        lines.append("")
    if planned_trips:
        lines.append("📅 <b>ЗАПЛАНОВАНІ ВИЇЗДИ:</b>")
        for t in planned_trips:
            dep_str = utc_str_to_kyiv_str(t["started_at"])
            leader = next((m["full_name"] for m in t["crew"] if m["is_leader"]), "Невідомо")
            # Якщо minutes_ago > 0 для PLANNED поїздки — час виїзду вже настав, але старт ще не підтвердили
            delay_mins = t["minutes_ago"] or 0
            delay_note = f" — ⏳ <i>Затримка старту ({delay_mins} хв)</i>" if delay_mins > 0 else ""
            lines.append(
                f"📅 <b>#{t['id']} {t['route_code']}</b> (ст.: {leader})\n"
                f"   └ Виїзд: <b>{dep_str}</b>{delay_note}"
            )
    lines.append("\n<i>Оберіть поїздку нижче для перегляду деталей або відправки запиту:</i>")
    return "\n".join(lines)


def format_operator_trip_details(trip: dict) -> str:
    '''Формує розширену картку конкретної поїздки для оператора.'''
    icon = get_trip_status_icon(trip)
    crew_lines = []
    for m in trip["crew"]:
        role_tag = " 👑 <i>(Старший групи)</i>" if m["is_leader"] else ""
        crew_lines.append(f"• {m['full_name']}{role_tag} [ID: <code>{m['tg_id']}</code>]")
    crew_block = "\n".join(crew_lines)
    if trip["status"] == "PLANNED":
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        return (
            f"{icon} <b>Поїздка #{trip['id']} (ЗАПЛАНОВАНА)</b>\n\n"
            f"📍 Маршрут: <b>{trip['origin']} → {trip['destination']}</b>\n"
            f"🕒 Запланований виїзд: <b>{dep_str}</b>\n"
            f"⏱ Інтервал чекіну: <b>{trip['checkin_interval_min']} хв</b>\n\n"
            f"👥 <b>Склад групи ({len(trip['crew'])}):</b>\n{crew_block}"
        )
    mins = max(0, trip["minutes_ago"] or 0)
    last_by = trip["last_checkin_user_name"] or "Старт поїздки"
    last_time_kyiv = utc_str_to_kyiv_str(trip["last_checkin_at"])
    start_time_kyiv = utc_str_to_kyiv_str(trip["started_at"])
    alert_banner = ""
    if mins >= trip["checkin_interval_min"]:
        alert_banner = "\n🔴 <b>УВАГА: Перевищено інтервал чекіну (120 хв)! Надішліть запит екіпажу.</b>\n"
    elif trip["pending_request_sent_at"]:
        ping_time_kyiv = utc_str_to_kyiv_str(trip["pending_request_sent_at"])
        alert_banner = f"\n🟡 <b>Запит надіслано ({ping_time_kyiv}). Очікується підтвердження від групи.</b>\n"
    location_str = (
        f"📌 Останній коментар: <b>{trip['last_checkin_note']}</b>\n"
        if trip.get("last_checkin_note") else ""
    )
    return (
        f"{icon} <b>Поїздка #{trip['id']} (АКТИВНА)</b>\n"
        f"{alert_banner}\n"
        f"📍 Маршрут: <b>{trip['origin']} → {trip['destination']}</b>\n"
        f"🚀 Фактичний старт: <b>{start_time_kyiv}</b>\n"
        f"🕒 Останній чекін: <b>{mins} хв тому</b> ({last_time_kyiv}, від: {last_by})\n"
        f"{location_str}"
        f"⏱ Інтервал чекіну: кожні <b>{trip['checkin_interval_min']} хв</b>\n\n"
        f"👥 <b>Склад групи ({len(trip['crew'])}):</b>\n{crew_block}"
    )


async def send_ping_to_crew(bot: Bot, trip: dict, operator_name: str) -> int:
    '''Розсилає запит на чекін усім учасникам поїздки. Повертає кількість доставлених повідомлень.'''
    sent_count = 0
    trip_id = trip["id"]
    for member in trip["crew"]:
        try:
            await bot.send_message(
                chat_id=member["tg_id"],
                text=(
                    f"📡 <b>Запит на чекін від відділу моніторингу!</b>\n\n"
                    f"🚗 Поїздка #{trip_id}: <b>{trip['route_code']}</b>\n"
                    f"Черговий оператор (<b>{operator_name}</b>) просить підтвердити, "
                    "що з вами все гаразд і поїздка проходить за планом:"
                ),
                reply_markup=build_checkin_response_keyboard(trip_id)
            )
            sent_count += 1
        except Exception as e:
            logger.warning(f"Не вдалося доставити пінг учаснику {member['tg_id']}: {e}")
    return sent_count


# =================================================
# 1. ГОЛОВНИЙ ДАШБОРД (/trips та кнопка оновлення)
# =================================================
@operator_router.message(or_f(Command("trips"), F.text == BTN_TRIPS_DASHBOARD))
async def cmd_trips_dashboard(message: Message, db_user: dict | None, state: FSMContext):
    await state.clear()
    if not is_operator_or_admin(db_user):
        await message.answer("⛔️ Ця панель доступна лише операторам моніторингу та адміністраторам.")
        return
    trips = await get_all_monitored_trips(DB_PATH)
    await message.answer(
        text=format_dashboard_text(trips),
        reply_markup=build_operator_dashboard_keyboard(trips)
    )


@operator_router.callback_query(F.data == "op_dashboard_refresh")
async def cb_refresh_dashboard(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Немає доступу.", show_alert=True)
        return
    trips = await get_all_monitored_trips(DB_PATH)
    await callback.answer("Дашборд оновлено")
    try:
        await callback.message.edit_text(
            text=format_dashboard_text(trips),
            reply_markup=build_operator_dashboard_keyboard(trips)
        )
    except Exception:
        pass


# =======================================
# 2. ПЕРЕГЛЯД КАРТКИ КОНКРЕТНОЇ ПОЇЗДКИ
# =======================================
@operator_router.callback_query(F.data.startswith("op_view_trip:"))
async def cb_view_trip_details(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Немає доступу.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("ACTIVE", "PLANNED"):
        await callback.answer("Ця поїздка вже завершена або скасована.", show_alert=True)
        trips = await get_all_monitored_trips(DB_PATH)
        await callback.message.edit_text(
            text=format_dashboard_text(trips),
            reply_markup=build_operator_dashboard_keyboard(trips)
        )
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            text=format_operator_trip_details(trip),
            reply_markup=build_operator_trip_manage_keyboard(trip)
        )
    except Exception:
        pass


# ===============================
# 3. НАДСИЛАННЯ ЗАПИТУ НА ЧЕКІН 
# ===============================
@operator_router.callback_query(F.data.startswith("op_card_ping:"))
async def cb_ping_from_card(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    '''Надсилання запиту на чекін зсередини детальної картки дашборду.'''
    if not is_operator_or_admin(db_user):
        await callback.answer("⛔️ Тільки для операторів моніторингу.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка вже не активна.", show_alert=True)
        return
    await mark_operator_ping_sent(DB_PATH, trip_id, db_user["id"])
    sent_count = await send_ping_to_crew(bot, trip, db_user["full_name"])
    await callback.answer(f"📡 Запит на чекін надіслано ({sent_count} учасн.)!", show_alert=True)
    # Оновлюємо картку поїздки, щоб одразу з'явився жовтий статус очікування
    updated_trip = await get_trip_details(DB_PATH, trip_id)
    try:
        await callback.message.edit_text(
            text=format_operator_trip_details(updated_trip),
            reply_markup=build_operator_trip_manage_keyboard(updated_trip)
        )
    except Exception:
        pass


# =====================================================================
# 4. РУЧНЕ КЕРУВАННЯ ПОЇЗДКОЮ ОПЕРАТОРОМ (Старт / Скасування / Закриття)
# =====================================================================
@operator_router.callback_query(F.data.startswith("op_force_start:"))
async def cb_operator_force_start(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        return
    trip_id = int(callback.data.split(":")[1])
    if not await activate_trip(DB_PATH, trip_id, actor_user_id=db_user["id"]):
        await callback.answer("Поїздка вже активна або скасована.", show_alert=True)
        return
    await callback.answer("🚀 Моніторинг поїздки розпочато!")
    trip = await get_trip_details(DB_PATH, trip_id)
    # Розсилаємо активну картку всім учасникам
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=-1, # -1 щоб сповіщення отримали всі члени екіпажу, включно зі старшим
        text=(
            f"🚀 <b>Оператор {db_user['full_name']} активував моніторинг поїздки #{trip_id}!</b>\n\n"
            + format_trip_card(trip)
        ),
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    await callback.message.edit_text(
        text=format_operator_trip_details(trip),
        reply_markup=build_operator_trip_manage_keyboard(trip)
    )


@operator_router.callback_query(F.data.startswith("op_force_cancel:"))
async def cb_operator_force_cancel(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or not await cancel_planned_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("Не вдалося скасувати поїздку.", show_alert=True)
        return
    await callback.answer("Заплановану поїздку скасовано.")
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=-1,
        text=f"❌ Заплановану поїздку #{trip_id} ({trip['route_code']}) скасовано оператором {db_user['full_name']}."
    )
    trips = await get_all_monitored_trips(DB_PATH)
    await callback.message.edit_text(
        text=format_dashboard_text(trips),
        reply_markup=build_operator_dashboard_keyboard(trips)
    )


@operator_router.callback_query(F.data.startswith("op_force_close_ask:"))
async def cb_operator_force_close_ask(callback: CallbackQuery, db_user: dict | None):
    if not is_operator_or_admin(db_user):
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка вже завершена.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🏁 <b>Примусове закриття поїздки #{trip_id}</b>\n\n"
        f"Маршрут: <b>{trip['route_code']}</b>\n"
        "Ви впевнені, що хочете закрити цю поїздку від імені відділу моніторингу?",
        reply_markup=build_operator_close_confirm_keyboard(trip_id)
    )


@operator_router.callback_query(F.data.startswith("op_force_close_confirm:"))
async def cb_operator_force_close_confirm(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not is_operator_or_admin(db_user):
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or not await close_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("Поїздку вже було закрито.", show_alert=True)
        return
    await callback.answer("🏁 Поїздку закрито.")
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=-1,
        text=f"🏁 <b>Поїздку #{trip_id} ({trip['route_code']}) завершено оператором {db_user['full_name']}.</b>"
    )
    # Повертаємо оператора на оновлений дашборд
    trips = await get_all_monitored_trips(DB_PATH)
    await callback.message.edit_text(
        text=format_dashboard_text(trips),
        reply_markup=build_operator_dashboard_keyboard(trips)
    )
