import asyncio
import logging
from aiogram import Bot
from config import DB_PATH
from database.queries import (
    get_due_planned_trips,
    get_overdue_active_trips,
    mark_trip_reminded,
    get_trip_details
)
from handlers.traveler import (
    notify_monitoring,
    format_trip_card,
    utc_str_to_kyiv_str
)
from keyboards.inline import (
    build_planned_trip_keyboard,
    build_checkin_response_keyboard
)


logger = logging.getLogger(__name__)


async def check_planned_departures(bot: Bot):
    """
    Перевіряє заплановані поїздки, де настав час виїзду.
    НЕ активує поїздку автоматично, а надсилає нагадування мандрівникам і в моніторинг.
    """
    due_trip_ids = await get_due_planned_trips(DB_PATH)
    for trip_id in due_trip_ids:
        trip = await get_trip_details(DB_PATH, trip_id)
        if not trip or trip["status"] != "PLANNED":
            continue
        # Ставимо мітку, що сповіщення про час виїзду вже надіслано
        await mark_trip_reminded(DB_PATH, trip_id)

        crew_names = ", ".join(
            f"{m['full_name']} (Team Leader)" if m["is_leader"] else m["full_name"]
            for m in trip["crew"]
        )
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        # 1. Сповіщаємо групу моніторингу (статус залишається PLANNED до підтвердження)
        await notify_monitoring(
            bot,
            text=(
                f"⏰ <b>PLANNED DEPARTURE TIME REACHED — Trip #{trip_id}</b>\n"
                f"📍 Route: <b>{trip['route_code']}</b>\n"
                f"👥 Crew: {crew_names}\n"
                f"🕒 Planned time: <b>{dep_str}</b>\n\n"
                "⏳ <i>Awaiting departure confirmation from the travelers. "
                "Monitoring will start automatically once the crew taps \"On the Road\".</i>"
            )
        )
        # 2. Сповіщаємо учасників поїздки з кнопкою підтвердження старту
        for member in trip["crew"]:
            try:
                await bot.send_message(
                    chat_id=member["tg_id"],
                    text=(
                        f"⏰ <b>Planned departure time reached for trip #{trip_id}!</b>\n\n"
                        "If you are already heading out — tap the <b>\"🚀 On the Road (Start Now)\"</b> button below "
                        "to activate monitoring.\n"
                        "<i>(If departure is delayed, tap the button when you actually set off).</i>\n\n"
                        + format_trip_card(trip)
                    ),
                    reply_markup=build_planned_trip_keyboard(trip_id)
                )
            except Exception as e:
                logger.warning(f"Unable to send a departure reminder to the user {member['tg_id']}: {e}")


async def check_overdue_checkins(bot: Bot):
    """
    Перевіряє активні поїздки, де минуло 120+ хвилин (checkin_interval_min) з останнього чекіну.
    Надсилає алерт у групу моніторингу та нагадування екіпажу.
    """
    overdue_trip_ids = await get_overdue_active_trips(DB_PATH)
    for trip_id in overdue_trip_ids:
        trip = await get_trip_details(DB_PATH, trip_id)
        if not trip or trip["status"] != "ACTIVE":
            continue
        # Фіксуємо, що алерт по поточному 120-хвилинному циклу надіслано
        await mark_trip_reminded(DB_PATH, trip_id)
        crew_names = ", ".join(
            f"{m['full_name']} (Team Leader)" if m["is_leader"] else m["full_name"]
            for m in trip["crew"]
        )
        mins = max(0, trip["minutes_ago"] or 0)
        last_by = trip["last_checkin_user_name"] or "Trip start"
        # 1. Червоний алерт у групу моніторингу з кнопкою запиту на чекін
        await notify_monitoring(
            bot,
            text=(
                f"🔴 <b>ATTENTION: OVERDUE CHECK-IN — Trip #{trip_id}</b>\n"
                f"📍 Route: <b>{trip['route_code']}</b>\n"
                f"👥 Crew: {crew_names}\n"
                f"🕒 Last check-in: <b>{mins} min ago</b> ({last_by})\n"
                f"⏱ Standard interval: <b>{trip['checkin_interval_min']} min</b>\n\n"
                "Go to the bot to send a check-in request"
            )
        )
        # 2. Автоматичне нагадування подорожуючим
        for member in trip["crew"]:
            try:
                await bot.send_message(
                    chat_id=member["tg_id"],
                    text=(
                        f"⚠️ <b>Scheduled check-in time! (Trip #{trip_id})</b>\n\n"
                        f"Route: <b>{trip['route_code']}</b>\n"
                        f"<b>{mins} min</b> have passed since your last check-in.\n"
                        "Please confirm that everything is okay:"
                    ),
                    reply_markup=build_checkin_response_keyboard(trip_id)
                )
            except Exception as e:
                logger.warning(f"Failed to send a check-in reminder to the user {member['tg_id']}: {e}")


async def watchdog_loop(bot: Bot):
    """Головний фоновий цикл моніторингу (кожні 30 секунд)."""
    logger.info("⏱ The watchdog service has been started (monitoring departures and intervals).")
    while True:
        try:
            await check_planned_departures(bot)
            await check_overdue_checkins(bot)
        except Exception as e:
            logger.error(f"Watchdog loop error: {e}")

        await asyncio.sleep(30)
