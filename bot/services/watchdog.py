import asyncio
import logging
from aiogram import Bot
from config import DB_PATH
from database.queries import get_due_planned_trips, activate_trip, get_trip_details
from handlers.traveler import notify_monitoring, format_trip_card
from keyboards.inline import build_operator_ping_keyboard, build_traveler_trip_keyboard


logger = logging.getLogger(__name__)


async def watchdog_loop(bot: Bot):
    '''Фоновий процес: автоматично стартує заплановані поїздки, коли настає час виїзду.'''
    logger.info("⏱ Watchdog-сервіс запущено.")
    while True:
        try:
            due_trip_ids = await get_due_planned_trips(DB_PATH)
            for trip_id in due_trip_ids:
                activated = await activate_trip(DB_PATH, trip_id, actor_user_id=None)
                if not activated:
                    continue
                trip = await get_trip_details(DB_PATH, trip_id)
                if not trip:
                    continue
                crew_names = ", ".join(
                    f"{m['full_name']} (Старший)" if m["is_leader"] else m["full_name"]
                    for m in trip["crew"]
                )
                # 1. Сповіщаємо групу моніторингу про настання часу виїзду + даємо кнопку запиту на чекін
                await notify_monitoring(
                    bot,
                    text=(
                        f"🚀 <b>ПОЧАТОК ПОЇЗДКИ ЗА РОЗКЛАДОМ #{trip_id}</b>\n"
                        f"📍 Маршрут: <b>{trip['route_code']}</b>\n"
                        f"👥 Екіпаж: {crew_names}\n"
                        f"⏱ Інтервал чекіну (СОП): <b>{trip['checkin_interval_min']} хв</b>\n\n"
                        "<i>Настав запланований час відправлення. Ви можете одразу надіслати запит на чекін для підтвердження виїзду:</i>"
                    ),
                    reply_markup=build_operator_ping_keyboard(trip_id)
                )
                # 2. Розсилаємо активну картку поїздки всім учасникам екіпажу
                for member in trip["crew"]:
                    try:
                        await bot.send_message(
                            chat_id=member["tg_id"],
                            text=(
                                f"🚀 <b>Настав час відправлення по поїздці #{trip_id}!</b>\n"
                                "Моніторинг активовано. Будь ласка, зробіть чекін після виїзду:\n\n"
                                + format_trip_card(trip)
                            ),
                            reply_markup=build_traveler_trip_keyboard(trip_id)
                        )
                    except Exception as e:
                        logger.warning(f"Не вдалося надіслати старт учаснику {member['tg_id']}: {e}")
        except Exception as e:
            logger.error(f"Помилка у циклі watchdog: {e}")

        await asyncio.sleep(30)
