import logging
import re
from datetime import datetime, timedelta
from aiogram import Bot, Router, F
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
    get_operators_tg_ids
)
from keyboards.inline import (
    build_planned_trip_keyboard,
    build_traveler_trip_keyboard,
    build_close_confirm_keyboard,
    build_cancel_note_keyboard,
    build_operator_ping_keyboard
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


# --- Допоміжні функції форматування часу та карток ---
def utc_str_to_kyiv_str(utc_str: str) -> str:
    """Конвертує рядок часу з БД (UTC) у зручний формат за Києвом (ДД.ММ о ГГ:ХХ)."""
    try:
        dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC_TZ)
        dt_kyiv = dt_utc.astimezone(KYIV_TZ)
        # Кирилична 'о' винесена за межі strftime для сумісності з Windows
        return f"{dt_kyiv.strftime('%d.%m')} о {dt_kyiv.strftime('%H:%M')}"
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
        kyiv_label = f"{dt_kyiv.strftime('%d.%m.%Y')} о {dt_kyiv.strftime('%H:%M')}"
        return dt_utc, kyiv_label
    except ValueError as e:
        # Логуємо точну причину, якщо ввели неіснуючу дату (напр. 32.11 або 25:00)
        logger.warning(f"Не вдалося розпарсити час '{raw}': {e}")
        return None, None


def build_cancel_creation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати створення", callback_data="trip_create_cancel")
    return builder.as_markup()


def build_companions_keyboard(other_users: list[dict], selected_ids: list[int]):
    '''Клавіатура вибору супутників (ініціатор вже є старшим групи за замовчуванням).'''
    builder = InlineKeyboardBuilder()
    # Головна швидка кнопка для індивідуальної поїздки
    if not selected_ids:
        builder.button(
            text="👤 Їду сам(а) — Завершити оформлення",
            callback_data="trip_confirm_companions"
        )
    else:
        builder.button(
            text=f"✅ Підтвердити групу (супутників: {len(selected_ids)})",
            callback_data="trip_confirm_companions"
        )

    for u in other_users:
        mark = "✅" if u["id"] in selected_ids else "⬜️"
        builder.button(
            text=f"{mark} {u['full_name']}",
            callback_data=f"trip_toggle_comp:{u['id']}"
        )
    builder.button(text="❌ Скасувати", callback_data="trip_create_cancel")
    builder.adjust(1)
    return builder.as_markup()


def format_trip_card(trip: dict) -> str:
    '''Формує картку поїздки залежно від її статусу (PLANNED або ACTIVE).'''
    crew_names = [
        f"{m['full_name']} (Старший)" if m["is_leader"] else m["full_name"]
        for m in trip["crew"]
    ]
    if trip["status"] == "PLANNED":
        dep_str = utc_str_to_kyiv_str(trip["started_at"])
        return (
            f"📅 <b>Запланована поїздка #{trip['id']}</b>\n"
            f"📍 Маршрут: <b>{trip['origin']} → {trip['destination']}</b>\n"
            f"👥 Екіпаж: {', '.join(crew_names)}\n"
            f"🕒 Виїзд: <b>{dep_str}</b>\n"
            f"⏱ Інтервал чекіну: <b>{trip['checkin_interval_min']} хв</b>\n\n"
            "<i>Моніторинг розпочнеться автоматично у вказаний час виїзду, "
            "або натисніть кнопку нижче, якщо вирушаєте раніше.</i>"
        )
    minutes_ago = max(0, trip["minutes_ago"] or 0)
    last_by = trip["last_checkin_user_name"] or "Старт поїздки"
    ping_warning = (
        "\n⚠️ <b>Моніторинг очікує вашого підтвердження!</b>\n"
        if trip["pending_request_sent_at"] else ""
    )

    return (
        f"🚗 <b>Активна поїздка #{trip['id']}</b>\n"
        f"📍 Маршрут: <b>{trip['origin']} → {trip['destination']}</b>\n"
        f"👥 Екіпаж: {', '.join(crew_names)}\n"
        f"⏱ Інтервал чекіну: кожні <b>{trip['checkin_interval_min']} хв</b>\n"
        f"🕒 Останній чекін: <b>{minutes_ago} хв тому</b> ({last_by})"
        f"{ping_warning}"
    )


async def notify_monitoring(bot: Bot, text: str, reply_markup=None):
    '''Надсилає повідомлення в групу моніторингу (або операторам у приват, якщо група не задана).'''
    if MONITORING_GROUP_ID:
        try:
            await bot.send_message(
                chat_id=int(MONITORING_GROUP_ID),
                text=text,
                reply_markup=reply_markup
            )
            return
        except Exception as e:
            logger.warning(f"Не вдалося надіслати в MONITORING_GROUP_ID: {e}")
    operator_ids = await get_operators_tg_ids(DB_PATH)
    for op_tg_id in operator_ids:
        try:
            await bot.send_message(chat_id=op_tg_id, text=text, reply_markup=reply_markup)
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
# 1. ГЛОБАЛЬНІ КОМАНДИ ТА КНОПКИ НИЖНЬОГО МЕНЮ (МАЮТЬ БУТИ НАЙПЕРШИМИ!)
# Перебивають будь-який активний стан FSM через await state.clear()
# =====================================================================

@traveler_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("Немає активного процесу для скасування.")
        return
    await state.clear()
    await message.answer("❌ Дію скасовано.")


@traveler_router.message(or_f(Command("new_trip"), F.text == BTN_NEW_TRIP))
async def cmd_new_trip(message: Message, db_user: dict | None, state: FSMContext):
    if not db_user:
        await message.answer("Спочатку зареєструйтесь командою /start.")
        return
    await state.clear()
    await message.answer(
        "🚗 <b>Оформлення нової поїздки</b>\n\n"
        "Крок 1/4. Введіть <b>пункт відправлення</b> (наприклад, <i>Київ</i>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_origin)


@traveler_router.message(or_f(Command("my_trip"), F.text == BTN_MY_TRIP))
async def cmd_my_trip(message: Message, db_user: dict | None, state: FSMContext):
    await state.clear()
    if not db_user:
        await message.answer("Спочатку зареєструйтесь командою /start.")
        return
    trips = await get_user_active_trips(DB_PATH, db_user["id"])
    if not trips:
        await message.answer(
            "🟢 У вас немає активних або запланованих поїздок.\n"
            "Натисніть <b>«🚗 Нова поїздка»</b> в меню, щоб створити відрядження."
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
# 2. КРОКИ FSM СТВОРЕННЯ ПОЇЗДКИ (Текстові + Інлайн-кнопки)
# =====================================================================

@traveler_router.callback_query(F.data == "trip_create_cancel")
async def cb_cancel_creation(callback: CallbackQuery, state: FSMContext):
    if await state.get_state() is None:
        await callback.answer("Процес вже завершено.", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.answer("Скасовано")
    await state.clear()
    await callback.message.edit_text("❌ Оформлення поїздки скасовано.")


@traveler_router.message(NewTripFSM.waiting_for_origin, F.text)
async def process_origin(message: Message, state: FSMContext):
    origin = message.text.strip()
    await state.update_data(origin=origin)
    await message.answer(
        f"Відправлення: <b>{origin}</b>\n\n"
        "Крок 2/4. Введіть <b>пункт призначення</b> (наприклад, <i>Дніпро</i>):",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_destination)


@traveler_router.message(NewTripFSM.waiting_for_destination, F.text)
async def process_destination(message: Message, state: FSMContext):
    destination = message.text.strip()
    await state.update_data(destination=destination)
    now_str = datetime.now(KYIV_TZ).strftime("%d.%m %H:%M")
    await message.answer(
        f"Маршрут: <b>{(await state.get_data())['origin']} → {destination}</b>\n\n"
        "Крок 3/4. Введіть <b>час відправлення</b> за київським часом:\n"
        "• Формат <code>ГГ:ХХ</code> (наприклад, <code>09:30</code>) — на сьогодні/завтра\n"
        f"• Формат <code>ДД.ММ ГГ:ХХ</code> (наприклад, <code>{now_str}</code>) — на конкретну дату",
        reply_markup=build_cancel_creation_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_departure_time)


@traveler_router.message(NewTripFSM.waiting_for_departure_time, F.text)
async def process_departure_time(message: Message, db_user: dict, state: FSMContext):
    dt_utc, kyiv_label = parse_kyiv_departure_time(message.text)
    if not dt_utc:
        await message.answer(
            "❌ Невірний формат часу. Введіть, наприклад, <code>08:30</code> або <code>18.11 08:30</code>:",
            reply_markup=build_cancel_creation_keyboard()
        )
        return
    all_users = await get_all_users(DB_PATH)
    other_users = [u for u in all_users if u["id"] != db_user["id"]]
    await state.update_data(
        planned_start_utc=dt_utc.strftime("%Y-%m-%d %H:%M:%S"),
        kyiv_departure_label=kyiv_label,
        other_users=other_users,
        selected_companion_ids=[]
    )
    await message.answer(
        f"🕒 Час виїзду: <b>{kyiv_label}</b>\n\n"
        f"Крок 4/4. Ви (<b>{db_user['full_name']}</b>) автоматично призначені старшим поїздки.\n"
        "Якщо їдете самі — натисніть верхню кнопку. Якщо групою — відмітьте супутників:",
        reply_markup=build_companions_keyboard(other_users, [])
    )
    await state.set_state(NewTripFSM.waiting_for_companions)


@traveler_router.callback_query(NewTripFSM.waiting_for_companions, F.data.startswith("trip_toggle_comp:"))
async def toggle_companion(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    comp_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected_ids: list[int] = data.get("selected_companion_ids", [])
    other_users: list[dict] = data.get("other_users", [])
    if comp_id in selected_ids:
        selected_ids.remove(comp_id)
    else:
        selected_ids.append(comp_id)
    await state.update_data(selected_companion_ids=selected_ids)
    await callback.message.edit_reply_markup(
        reply_markup=build_companions_keyboard(other_users, selected_ids)
    )


@traveler_router.callback_query(NewTripFSM.waiting_for_companions, F.data == "trip_confirm_companions")
async def finalize_planned_trip(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    await callback.answer("Поїздку заплановано!")
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
        await callback.message.edit_text("❌ Помилка збереження поїздки в базі даних.")
        return
    trip = await get_trip_details(DB_PATH, trip_id)
    await callback.message.edit_text(
        text=format_trip_card(trip),
        reply_markup=build_planned_trip_keyboard(trip_id)
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=(
            f"📅 <b>{db_user['full_name']} додав(ла) вас до запланованої поїздки #{trip_id}</b>\n\n"
            + format_trip_card(trip)
        ),
        reply_markup=build_planned_trip_keyboard(trip_id)
    )


# =====================================================================
# 3. ДІЇ ІЗ ЗАПЛАНОВАНОЮ ПОЇЗДКОЮ (Ручний старт або Скасування)
# =====================================================================

@traveler_router.callback_query(F.data.startswith("trv_start_now:"))
async def process_manual_start(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    activated = await activate_trip(DB_PATH, trip_id, actor_user_id=db_user["id"])
    if not activated:
        await callback.answer("Поїздка вже активна або була скасована.", show_alert=True)
        return
    await callback.answer("🚀 Поїздку розпочато! Гарної дороги!")
    trip = await get_trip_details(DB_PATH, trip_id)
    await callback.message.edit_text(
        text=format_trip_card(trip) + "\n\n🚀 <i>Моніторинг розпочато!</i>",
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    crew_names = ", ".join(
        f"{m['full_name']} (Старший)" if m["is_leader"] else m["full_name"]
        for m in trip["crew"]
    )
    await notify_monitoring(
        bot,
        text=(
            f"🚀 <b>СТАРТ ПОЇЗДКИ #{trip_id}</b> ({trip['route_code']})\n"
            f"👥 Екіпаж: {crew_names}\n"
            f"▶️ Розпочав(ла): <b>{db_user['full_name']}</b>\n"
            f"⏱ Інтервал чекіну (СОП): <b>{trip['checkin_interval_min']} хв</b>"
        ),
        reply_markup=build_operator_ping_keyboard(trip_id)
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🚀 <b>Поїздку #{trip_id} розпочато!</b> ({db_user['full_name']})\n\n" + format_trip_card(trip),
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )


@traveler_router.callback_query(F.data.startswith("trv_cancel_planned:"))
async def process_cancel_planned_trip(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "PLANNED":
        await callback.answer("Поїздку вже розпочато або скасовано раніше.", show_alert=True)
        return
    cancelled = await cancel_planned_trip(DB_PATH, trip_id, db_user["id"])
    if not cancelled:
        await callback.answer("Не вдалося скасувати поїздку.", show_alert=True)
        return
    await callback.answer("Поїздку скасовано")
    await callback.message.edit_text(
        f"❌ <b>Заплановану поїздку #{trip_id} ({trip['route_code']}) скасовано.</b>"
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"❌ Заплановану поїздку #{trip_id} ({trip['route_code']}) скасовано користувачем {db_user['full_name']}."
    )


# =====================================================================
# 4. ЧЕКІНИ, ПРИМІТКИ ТА ЗАКРИТТЯ АКТИВНОЇ ПОЇЗДКИ
# =====================================================================

@traveler_router.callback_query(F.data.startswith("trv_refresh:"))
async def refresh_trip_card(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] not in ("ACTIVE", "PLANNED"):
        await callback.answer("Ця поїздка вже завершена.", show_alert=True)
        await callback.message.edit_text("🏁 Поїздку завершено.")
        return
    kb = (
        build_planned_trip_keyboard(trip_id)
        if trip["status"] == "PLANNED"
        else build_traveler_trip_keyboard(trip_id)
    )
    await callback.answer("Статус оновлено")
    try:
        await callback.message.edit_text(text=format_trip_card(trip), reply_markup=kb)
    except Exception:
        pass


@traveler_router.callback_query(F.data.startswith("trv_checkin:"))
async def process_quick_checkin(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        await callback.answer("Помилка авторизації.", show_alert=True)
        return
    trip_id = int(callback.data.split(":")[1])
    event_type = await record_checkin(DB_PATH, trip_id, db_user["id"])
    if not event_type:
        await callback.answer("Поїздка вже закрита або ще не розпочата.", show_alert=True)
        return
    await callback.answer("✅ Чекін прийнято!")
    trip = await get_trip_details(DB_PATH, trip_id)
    await callback.message.edit_text(
        text=format_trip_card(trip) + "\n\n✅ <i>Ваш чекін щойно зафіксовано!</i>",
        reply_markup=build_traveler_trip_keyboard(trip_id)
    )
    kind_label = "підтвердження за запитом" if event_type == "REQUESTED_CHECKIN" else "самостійний"
    await notify_monitoring(
        bot,
        f"🟢 <b>Чекін по поїздці #{trip_id}</b> ({trip['route_code']})\n"
        f"👤 Від: <b>{db_user['full_name']}</b> ({kind_label})\n"
        f"⏱ Наступний контроль через {trip['checkin_interval_min']} хв."
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"ℹ️ <b>Поїздка #{trip_id} ({trip['route_code']}):</b>\n{db_user['full_name']} підтвердив(ла) чекін для групи."
    )


@traveler_router.callback_query(F.data.startswith("trv_note:"))
async def ask_checkin_note(callback: CallbackQuery, state: FSMContext):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка не активна.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(note_trip_id=trip_id)
    await state.set_state(TravelerNoteFSM.waiting_for_note)
    await callback.message.edit_text(
        f"💬 <b>Чекін з приміткою для поїздки #{trip_id}</b>\n\n"
        "Введіть коротке повідомлення для моніторингу:",
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
        text=f"ℹ️ <b>Поїздка #{trip_id}:</b> {db_user['full_name']} зробив(ла) чекін: <i>«{note_text}»</i>"
    )


@traveler_router.callback_query(F.data.startswith("trv_close_ask:"))
async def ask_close_confirmation(callback: CallbackQuery):
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздка вже завершена.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🏁 <b>Підтвердження прибуття</b>\n\n"
        f"Ви дійсно прибули в <b>{trip['destination']}</b> і хочете закрити поїздку #{trip_id}?",
        reply_markup=build_close_confirm_keyboard(trip_id)
    )


@traveler_router.callback_query(F.data.startswith("trv_close_confirm:"))
async def confirm_close_trip(callback: CallbackQuery, db_user: dict | None, bot: Bot):
    if not db_user:
        return
    trip_id = int(callback.data.split(":")[1])
    trip = await get_trip_details(DB_PATH, trip_id)
    if not trip or trip["status"] != "ACTIVE":
        await callback.answer("Поїздку вже закрито раніше.", show_alert=True)
        return
    if not await close_trip(DB_PATH, trip_id, db_user["id"]):
        await callback.answer("Помилка при закритті.", show_alert=True)
        return
    await callback.answer("🏁 З прибуттям!")
    await callback.message.edit_text(
        f"🏁 <b>Поїздку #{trip_id} ({trip['route_code']}) успішно завершено!</b>\n"
        f"Закрив(ла): {db_user['full_name']}."
    )
    await notify_monitoring(
        bot,
        f"🏁 <b>Поїздку #{trip_id} ЗАВЕРШЕНО</b>\n"
        f"📍 Маршрут: <b>{trip['route_code']}</b>\n"
        f"👤 Підтвердив прибуття: <b>{db_user['full_name']}</b>"
    )
    await notify_other_crew_members(
        bot,
        trip,
        actor_user_id=db_user["id"],
        text=f"🏁 <b>Поїздку #{trip_id} ({trip['route_code']}) завершено!</b> ({db_user['full_name']})"
    )
