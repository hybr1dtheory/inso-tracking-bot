import asyncio
from html import escape
import logging
from aiogram import Bot
from config import DB_PATH
from database.queries import (
    get_due_planned_trips,
    get_overdue_active_trips,
    get_trip_details,
    mark_trip_reminded,
)
from handlers.traveler import (
    format_crew_list,
    format_trip_card,
    notify_monitoring,
    utc_str_to_kyiv_str,
)
from keyboards.inline import (
    build_checkin_response_keyboard,
    build_planned_trip_keyboard,
)


logger = logging.getLogger(__name__)

WATCHDOG_POLL_INTERVAL_SEC = 30


async def check_planned_departures(bot: Bot):
    """
    Перевіряє заплановані поїздки, де настав час виїзду.
    НЕ активує поїздку автоматично, а надсилає нагадування мандрівникам і в моніторинг.
    """
    due_trip_ids = await get_due_planned_trips(DB_PATH)
    for trip_id in due_trip_ids:
        try:
            trip = await get_trip_details(DB_PATH, trip_id)
            if not trip or trip["status"] != "PLANNED":
                continue
            # Ставимо мітку до відправки; якщо БД не оновилась — пропускаємо, щоб уникнути спаму
            if not await mark_trip_reminded(DB_PATH, trip_id):
                logger.warning(f"Skipping departure reminder for trip #{trip_id}: failed to mark reminded.")
                continue
            route_safe = escape(trip["route_code"])
            crew_names = format_crew_list(trip["crew"])
            dep_str = utc_str_to_kyiv_str(trip["started_at"])
            # Сповіщаємо групу моніторингу (статус залишається PLANNED до підтвердження)
            await notify_monitoring(
                bot,
                text=(
                    f"⏰ <b>PLANNED DEPARTURE TIME REACHED — Trip #{trip_id}</b>\n"
                    f"📍 Route: <b>{route_safe}</b>\n"
                    f"👥 Crew: {crew_names}\n"
                    f"🕒 Planned time: <b>{dep_str}</b>\n\n"
                    "⏳ <i>Awaiting departure confirmation from the travelers. "
                    "Monitoring will start automatically once the crew taps \"On the Road\".</i>"
                )
            )
            # Сповіщаємо учасників поїздки з кнопкою підтвердження старту
            trip_card = format_trip_card(trip)
            keyboard = build_planned_trip_keyboard(trip_id)
            for member in trip["crew"]:
                try:
                    await bot.send_message(
                        chat_id=member["tg_id"],
                        text=(
                            f"⏰ <b>Planned departure time reached for trip #{trip_id}!</b>\n\n"
                            "If you are already heading out — tap the <b>\"🚀 On the Road (Start Now)\"</b> button below "
                            "to activate monitoring.\n"
                            "<i>(If departure is delayed, tap the button when you actually set off).</i>\n\n"
                            + trip_card
                        ),
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to send departure reminder to user tg_id={member['tg_id']} "
                        f"(trip #{trip_id}): {e}"
                    )
        except Exception as e:
            logger.error(f"Error processing planned departure for trip #{trip_id}: {e}")


async def check_overdue_checkins(bot: Bot):
    """
    Перевіряє активні поїздки з інтервалом > 0, де минуло >= checkin_interval_min
    з останнього чекіну. Надсилає алерт у групу моніторингу та нагадування екіпажу.
    """
    overdue_trip_ids = await get_overdue_active_trips(DB_PATH)
    for trip_id in overdue_trip_ids:
        try:
            trip = await get_trip_details(DB_PATH, trip_id)
            interval_min = (trip.get("checkin_interval_min") or 0) if trip else 0
            if not trip or trip["status"] != "ACTIVE" or interval_min <= 0:
                continue
            # Фіксуємо, що алерт по поточному інтервалу надіслано
            if not await mark_trip_reminded(DB_PATH, trip_id):
                logger.warning(f"Skipping overdue alert for trip #{trip_id}: failed to mark reminded.")
                continue

            route_safe = escape(trip["route_code"])
            crew_names = format_crew_list(trip["crew"])
            mins = max(0, trip["minutes_ago"] or 0)
            last_by_safe = escape(trip["last_checkin_user_name"] or "Trip start")
            # Червоний алерт у групу моніторингу (read-only стрічка)
            await notify_monitoring(
                bot,
                text=(
                    f"🔴 <b>ATTENTION: OVERDUE CHECK-IN — Trip #{trip_id}</b>\n"
                    f"📍 Route: <b>{route_safe}</b>\n"
                    f"👥 Crew: {crew_names}\n"
                    f"🕒 Last check-in: <b>{mins} min ago</b> ({last_by_safe})\n"
                    f"⏱ Check-in interval: <b>{interval_min} min</b>\n\n"
                    "Open the bot to send a check-in request."
                )
            )
            # Автоматичне нагадування подорожуючим
            keyboard = build_checkin_response_keyboard(trip_id)
            for member in trip["crew"]:
                try:
                    await bot.send_message(
                        chat_id=member["tg_id"],
                        text=(
                            f"⚠️ <b>Scheduled check-in time! (Trip #{trip_id})</b>\n\n"
                            f"Route: <b>{route_safe}</b>\n"
                            f"<b>{mins} min</b> have passed since your last check-in.\n"
                            "Please confirm that everything is okay:"
                        ),
                        reply_markup=keyboard
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to send check-in reminder to user tg_id={member['tg_id']} "
                        f"(trip #{trip_id}): {e}"
                    )
        except Exception as e:
            logger.error(f"Error processing overdue check-in for trip #{trip_id}: {e}")


async def watchdog_loop(bot: Bot):
    """Головний фоновий цикл моніторингу."""
    logger.info(
        f"Watchdog service started (polling every {WATCHDOG_POLL_INTERVAL_SEC}s "
        "for planned departures and dynamic check-in intervals)."
    )
    try:
        while True:
            try:
                await check_planned_departures(bot)
                await check_overdue_checkins(bot)
            except Exception as e:
                logger.error(f"Watchdog loop error: {e}")
            await asyncio.sleep(WATCHDOG_POLL_INTERVAL_SEC)
    except asyncio.CancelledError:
        logger.info("Watchdog service stopped.")
        raise
