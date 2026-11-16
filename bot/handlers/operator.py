import logging
from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import DB_PATH
from database.queries import get_all_users, create_trip


logger = logging.getLogger(__name__)
operator_router = Router()


class NewTripFSM(StatesGroup):
    waiting_for_origin = State()
    waiting_for_destination = State()
    waiting_for_crew = State()
    waiting_for_interval = State()


def build_crew_keyboard(users: list[dict], selected_ids: list[int]):
    '''Будує інлайн-клавіатуру з чекбоксами для мульти-вибору екіпажу.'''
    builder = InlineKeyboardBuilder()
    for u in users:
        is_selected = u["id"] in selected_ids
        mark = "✅" if is_selected else "⬜️"
        builder.button(
            text=f"{mark} {u['full_name']} ({u['role']})",
            callback_data=f"trip_toggle_user:{u['id']}"
        )
    builder.adjust(1)
    # Нижній ряд керування
    controls = InlineKeyboardBuilder()
    controls.button(
        text=f"➡️ Далі (обрано: {len(selected_ids)})",
        callback_data="trip_confirm_crew"
    )
    controls.button(text="❌ Скасувати", callback_data="trip_cancel")
    controls.adjust(2)
    builder.attach(controls)
    return builder.as_markup()


def build_cancel_keyboard():
    '''Кнопка скасування для текстових кроків (1 та 2).'''
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data="trip_cancel")
    return builder.as_markup()


# --- Скасування командою /cancel ---

@operator_router.message(Command("cancel"))
async def cmd_cancel_trip(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Немає активного процесу для скасування.")
        return
    await state.clear()
    await message.answer("❌ Створення поїздки скасовано.")


# --- КРОК 0: Старт створення поїздки ---
@operator_router.message(Command("new_trip"))
async def cmd_new_trip(message: Message, db_user: dict | None, state: FSMContext):
    if not db_user or db_user["role"] not in ("OPERATOR", "ADMIN"):
        await message.answer("⛔️ Створювати поїздки можуть лише оператори моніторингу або адміністратори.")
        return
    await state.clear()
    await message.answer(
        "🚗 <b>Створення нової поїздки</b>\n\n"
        "Крок 1/4. Введіть <b>пункт відправлення</b> (наприклад, <i>Київ</i>):",
        reply_markup=build_cancel_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_origin)


# --- КРОК 1: Пункт відправлення ---
@operator_router.message(NewTripFSM.waiting_for_origin, F.text)
async def process_origin(message: Message, state: FSMContext):
    origin = message.text.strip()
    await state.update_data(origin=origin)
    await message.answer(
        f"Відправлення: <b>{origin}</b>\n\n"
        "Крок 2/4. Введіть <b>пункт призначення</b> (наприклад, <i>Дніпро</i>):",
        reply_markup=build_cancel_keyboard()
    )
    await state.set_state(NewTripFSM.waiting_for_destination)


# --- КРОК 2: Пункт призначення та перехід до вибору екіпажу ---
@operator_router.message(NewTripFSM.waiting_for_destination, F.text)
async def process_destination(message: Message, state: FSMContext):
    destination = message.text.strip()
    users = await get_all_users(DB_PATH)
    if not users:
        await message.answer("❌ У базі немає зареєстрованих користувачів. Додайте їх через /admin.")
        await state.clear()
        return
    # Зберігаємо пункт призначення, повний список користувачів та порожній список обраних ID
    await state.update_data(
        destination=destination,
        all_users=users,
        selected_ids=[]
    )
    keyboard = build_crew_keyboard(users, selected_ids=[])
    await message.answer(
        f"Пункт призначення: <b>{destination}</b>\n\n"
        "Крок 3/4. Оберіть учасників поїздки (перший обраний стане старшим групи):",
        reply_markup=keyboard
    )
    await state.set_state(NewTripFSM.waiting_for_crew)


# --- КРОК 3: Мульти-вибір учасників ---
@operator_router.callback_query(NewTripFSM.waiting_for_crew, F.data.startswith("trip_toggle_user:"))
async def toggle_crew_member(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected_ids: list[int] = data.get("selected_ids", [])
    users: list[dict] = data.get("all_users", [])
    # Додаємо або прибираємо ID зі списку обраних
    if user_id in selected_ids:
        selected_ids.remove(user_id)
    else:
        selected_ids.append(user_id)
    await state.update_data(selected_ids=selected_ids)
    # Перемальовуємо лише клавіатуру без відправки нового повідомлення
    keyboard = build_crew_keyboard(users, selected_ids)
    await callback.message.edit_reply_markup(reply_markup=keyboard)


@operator_router.callback_query(NewTripFSM.waiting_for_crew, F.data == "trip_confirm_crew")
async def confirm_crew(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected_ids: list[int] = data.get("selected_ids", [])
    if not selected_ids:
        await callback.answer("Оберіть хоча б одного учасника поїздки!", show_alert=True)
        return

    await callback.answer()
    # Формуємо кнопки інтервалів чекіну
    builder = InlineKeyboardBuilder()
    for minutes in (30, 60, 90, 120):
        builder.button(text=f"⏱ {minutes} хв", callback_data=f"trip_interval:{minutes}")
    builder.button(text="❌ Скасувати", callback_data="trip_cancel")
    builder.adjust(2, 2, 1)
    await callback.message.edit_text(
        f"Обрано учасників: <b>{len(selected_ids)}</b>\n\n"
        "Крок 4/4. Оберіть інтервал контролю (чекіну):",
        reply_markup=builder.as_markup()
    )
    await state.set_state(NewTripFSM.waiting_for_interval)


# --- КРОК 4: Збереження поїздки та сповіщення екіпажу ---
@operator_router.callback_query(NewTripFSM.waiting_for_interval, F.data.startswith("trip_interval:"))
async def save_new_trip(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    await callback.answer()
    interval_min = int(callback.data.split(":")[1])
    data = await state.get_data()
    origin = data["origin"]
    destination = data["destination"]
    selected_ids: list[int] = data["selected_ids"]
    all_users: list[dict] = data["all_users"]
    # Записуємо поїздку в БД
    trip_id = await create_trip(
        db_path=DB_PATH,
        origin=origin,
        destination=destination,
        participant_ids=selected_ids,
        interval_min=interval_min,
        operator_db_id=db_user["id"]
    )
    if not trip_id:
        await callback.message.edit_text("❌ Не вдалося зберегти поїздку в базі даних.")
        await state.clear()
        return
    # Відбираємо профілі обраних учасників у порядку вибору
    users_by_id = {u["id"]: u for u in all_users}
    crew = [users_by_id[uid] for uid in selected_ids if uid in users_by_id]
    # Розсилаємо стартові сповіщення кожному учаснику
    failed_notifications = []
    crew_names = []
    for idx, member in enumerate(crew):
        label = f"{member['full_name']} (Старший)" if idx == 0 else member["full_name"]
        crew_names.append(label)
        try:
            await bot.send_message(
                chat_id=member["tg_id"],
                text=(
                    f"🚗 <b>Вас додано до активної поїздки #{trip_id}</b>\n"
                    f"📍 Маршрут: <b>{origin} → {destination}</b>\n"
                    f"⏱ Інтервал чекіну: кожні <b>{interval_min} хв</b>\n\n"
                    "Початковий чекін зафіксовано. Гарної дороги!"
                )
            )
        except Exception as e:
            # Спрацює, якщо тестовий або новий юзер ще ніколи не натискав /start у боті
            logger.warning(f"Не вдалося надіслати сповіщення користувачу {member['tg_id']}: {e}")
            failed_notifications.append(member["full_name"])
    # Формуємо звіт для оператора
    summary = (
        f"✅ <b>Поїздку #{trip_id} активовано!</b>\n\n"
        f"📍 Маршрут: <b>{origin} → {destination}</b>\n"
        f"👥 Екіпаж: {', '.join(crew_names)}\n"
        f"⏱ Інтервал чекіну: <b>{interval_min} хв</b>"
    )
    if failed_notifications:
        summary += (
            "\n\n⚠️ <b>Не доставлено сповіщення:</b> "
            f"{', '.join(failed_notifications)} "
            "(користувач ще не запускав бота через /start або заблокував його)."
        )
    await callback.message.edit_text(summary)
    await state.clear()


# --- Скасування на будь-якому етапі ---
@operator_router.callback_query(F.data == "trip_cancel")
async def cancel_trip_creation(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await callback.answer("Процес уже завершено або скасовано.", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return
    await callback.answer("Скасовано")
    await state.clear()
    await callback.message.edit_text("❌ Створення поїздки скасовано.")
