from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import DB_PATH
from database.queries import get_all_users, update_user_role, add_new_user


admin_router = Router()

# Визначаємо стани для FSM
class AdminMenu(StatesGroup):
    waiting_for_user_selection = State()
    waiting_for_role_selection = State()
    waiting_for_new_user_tgid = State()
    waiting_for_new_user_name = State()


@admin_router.message(Command("admin"))
async def cmd_admin_panel(message: Message, db_user: dict, state: FSMContext):
    '''Головне меню адмінки (доступне лише для ADMIN)'''
    users = await get_all_users(DB_PATH)
    builder = InlineKeyboardBuilder()
    # Кнопки для існуючих користувачів
    if users:
        for u in users:
            builder.button(text=f"{u['full_name']} ({u['role']})", callback_data=f"edit_user:{u['id']}")
    # Додавання нового користувача
    builder.button(text="➕ Додати нового користувача", callback_data="admin_add_new_user")
    builder.adjust(1) # Усі кнопки в один стовпчик
    text = "🛠 <b>Панель адміністратора</b>\n\nОберіть користувача для зміни ролі або додайте нового:"
    if not users:
         text = "🛠 <b>Панель адміністратора</b>\n\nУ базі немає користувачів. Додайте їх:"
         
    await message.answer(text, reply_markup=builder.as_markup())
    await state.set_state(AdminMenu.waiting_for_user_selection)


# --- ОБРОБКА РЕДАГУВАННЯ ІСНУЮЧОГО КОРИСТУВАЧА ---
@admin_router.callback_query(AdminMenu.waiting_for_user_selection, F.data.startswith("edit_user:"))
async def select_user_for_edit(callback: CallbackQuery, state: FSMContext):
    """Обробка вибору користувача"""
    # Витягуємо ID користувача з callback_data
    user_id = int(callback.data.split(":")[1])
    # Зберігаємо вибраний ID в пам'ять FSM для наступного кроку
    await state.update_data(selected_user_id=user_id)
    # Створюємо кнопки з доступними ролями
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 TRAVELER", callback_data="set_role:TRAVELER")
    builder.button(text="🎧 OPERATOR", callback_data="set_role:OPERATOR")
    builder.button(text="🛡 ADMIN", callback_data="set_role:ADMIN")
    builder.adjust(1)
    await callback.message.edit_text(
        "Оберіть нову роль для цього користувача:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(AdminMenu.waiting_for_role_selection)


@admin_router.callback_query(AdminMenu.waiting_for_role_selection, F.data.startswith("set_role:"))
async def save_new_role(callback: CallbackQuery, state: FSMContext):
    """Збереження нової ролі"""
    new_role = callback.data.split(":")[1]
    # Дістаємо збережений ID користувача з FSM
    user_data = await state.get_data()
    user_id = user_data.get("selected_user_id")
    # Оновлюємо базу
    success = await update_user_role(DB_PATH, user_id, new_role)
    if success:
        await callback.message.edit_text(f"✅ Роль успішно змінено на <b>{new_role}</b>.")
    else:
        await callback.message.edit_text("❌ Сталася помилка при оновленні.")
    # Очищаємо стан FSM
    await state.clear()


# --- ОБРОБКА ДОДАВАННЯ НОВОГО КОРИСТУВАЧА ---
@admin_router.callback_query(AdminMenu.waiting_for_user_selection, F.data == "admin_add_new_user")
async def process_add_new_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "Введіть <b>Telegram ID</b> нового користувача (це мають бути лише цифри, наприклад 123456789):"
    )
    await state.set_state(AdminMenu.waiting_for_new_user_tgid)


# Ловимо текст (ID) від адміна
@admin_router.message(AdminMenu.waiting_for_new_user_tgid, F.text.regexp(r'^\d+$'))
async def process_new_user_tgid(message: Message, state: FSMContext):
    tg_id = int(message.text)
    await state.update_data(new_user_tgid=tg_id)
    await message.answer(
        f"Telegram ID: {tg_id} прийнято.\n"
        "Тепер введіть <b>Ім'я та Прізвище</b> співробітника:"
    )
    await state.set_state(AdminMenu.waiting_for_new_user_name)


# Захист від тексту замість цифр (якщо ввели не валідний ID)
@admin_router.message(AdminMenu.waiting_for_new_user_tgid)
async def process_invalid_tgid(message: Message):
    await message.answer("Помилка! Telegram ID має складатися лише з цифр. Спробуйте ще раз:")


# Ловимо текст (Ім'я) та записуємо в БД
@admin_router.message(AdminMenu.waiting_for_new_user_name, F.text)
async def process_new_user_name(message: Message, state: FSMContext):
    full_name = message.text
    user_data = await state.get_data()
    tg_id = user_data.get("new_user_tgid")
    # Викликаємо універсальну функцію
    added_user = await add_new_user(DB_PATH, tg_id=tg_id, full_name=full_name, role='TRAVELER')
    if added_user:
        await message.answer(
            f"✅ Користувача <b>{full_name}</b> (ID: {tg_id}) успішно додано!\n"
            "За замовчуванням йому призначено роль TRAVELER.\n\n"
            "Викличте /admin ще раз, якщо потрібно змінити його роль."
        )
    else:
        await message.answer("❌ Помилка. Користувач із таким Telegram ID вже існує.")
        
    await state.clear()
