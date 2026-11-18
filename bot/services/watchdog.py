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
            f"{m['full_name']} (Старший)" if m["is_leader"] else m["full_name"]
            for m in trip["crew"]
        )
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        # 1. Сповіщаємо групу моніторингу (статус залишається PLANNED до підтвердження)
        await notify_monitoring(
            bot,
            text=(
                f"⏰ <b>НАСТАВ ПЛАНОВИЙ ЧАС ВИЇЗДУ — Поїздка #{trip_id}</b>\n"
                f"📍 Маршрут: <b>{trip['route_code']}</b>\n"
                f"👥 Екіпаж: {crew_names}\n"
                f"🕒 Запланований час: <b>{dep_str}</b>\n\n"
                "⏳ <i>Очікується підтвердження початку руху від подорожуючих. "
                "Моніторинг увімкнеться автоматично, щойно екіпаж натисне «Вирушили».</i>"
            )
        )
        # 2. Сповіщаємо учасників поїздки з кнопкою підтвердження старту
        for member in trip["crew"]:
            try:
                await bot.send_message(
                    chat_id=member["tg_id"],
                    text=(
                        f"⏰ <b>Настав запланований час виїзду по поїздці #{trip_id}!</b>\n\n"
                        "Якщо ви вже рушаєте — натисніть кнопку <b>«🚀 Вирушили (Почати зараз)»</b> нижче, "
                        "щоб активувати моніторинг.\n"
                        "<i>(Якщо виїзд затримується, натисніть кнопку тоді, коли фактично вирушите).</i>\n\n"
                        + format_trip_card(trip)
                    ),
                    reply_markup=build_planned_trip_keyboard(trip_id)
                )
            except Exception as e:
                logger.warning(f"Не вдалося надіслати нагадування виїзду учаснику {member['tg_id']}: {e}")


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
            f"{m['full_name']} (Старший)" if m["is_leader"] else m["full_name"]
            for m in trip["crew"]
        )
        mins = max(0, trip["minutes_ago"] or 0)
        last_by = trip["last_checkin_user_name"] or "Старт поїздки"
        # 1. Червоний алерт у групу моніторингу з кнопкою запиту на чекін
        await notify_monitoring(
            bot,
            text=(
                f"🔴 <b>УВАГА: ПРОСТРОЧЕНО ЧЕКІН — Поїздка #{trip_id}</b>\n"
                f"📍 Маршрут: <b>{trip['route_code']}</b>\n"
                f"👥 Екіпаж: {crew_names}\n"
                f"🕒 Останній чекін: <b>{mins} хв тому</b> ({last_by})\n"
                f"⏱ Нормативний інтервал: <b>{trip['checkin_interval_min']} хв</b>\n\n"
                "Перейдіть в бот, щоб надіслати запит на чекін"
            )
        )
        # 2. Автоматичне нагадування подорожуючим
        for member in trip["crew"]:
            try:
                await bot.send_message(
                    chat_id=member["tg_id"],
                    text=(
                        f"⚠️ <b>Час планового чекіну! (Поїздка #{trip_id})</b>\n\n"
                        f"Маршрут: <b>{trip['route_code']}</b>\n"
                        f"Минуло <b>{mins} хв</b> з моменту останнього зв'язку.\n"
                        "Будь ласка, підтвердіть, що у вас все гаразд:"
                    ),
                    reply_markup=build_checkin_response_keyboard(trip_id)
                )
            except Exception as e:
                logger.warning(f"Не вдалося надіслати нагадування чекіну учаснику {member['tg_id']}: {e}")


async def watchdog_loop(bot: Bot):
    """Головний фоновий цикл моніторингу (кожні 30 секунд)."""
    logger.info("⏱ Watchdog-сервіс запущено (контроль виїздів та інтервалу 120 хв).")
    while True:
        try:
            await check_planned_departures(bot)
            await check_overdue_checkins(bot)
        except Exception as e:
            logger.error(f"Помилка у циклі watchdog: {e}")

        await asyncio.sleep(30)
