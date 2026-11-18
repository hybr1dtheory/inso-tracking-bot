from aiogram import Bot, Router, F
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import DB_PATH, ADMIN_TG_ID
from keyboards.reply import BTN_ADMIN_PANEL, get_main_menu_keyboard
from database.queries import (
    get_all_users,
    get_user_by_id,
    update_user_role,
    update_user_name,
    delete_user,
    add_new_user
)


admin_router = Router()


class AdminMenu(StatesGroup):
    waiting_for_new_user_tgid = State()
    waiting_for_new_user_name = State()
    waiting_for_renamed_fullname = State()


# --- Допоміжні функції побудови клавіатур та текстів адмінки ---
def build_users_list_keyboard(users: list[dict]):
    """Головний список усіх користувачів + кнопка додавання."""
    builder = InlineKeyboardBuilder()
    for u in users:
        builder.button(
            text=f"👤 {u['full_name']} ({u['role']})",
            callback_data=f"admin_user:{u['id']}"
        )
    builder.button(text="➕ Додати нового користувача", callback_data="admin_add_new_user")
    builder.adjust(1)
    return builder.as_markup()


def format_user_card_text(user: dict) -> str:
    """Текст картки вибраного користувача."""
    username_str = f"@{user['username']}" if user.get("username") else "не вказано"
    return (
        f"👤 <b>Картка користувача</b>\n\n"
        f"• <b>ПІБ:</b> {user['full_name']}\n"
        f"• <b>Telegram ID:</b> <code>{user['tg_id']}</code>\n"
        f"• <b>Username:</b> {username_str}\n"
        f"• <b>Роль:</b> <b>{user['role']}</b>\n\n"
        "Оберіть дію:"
    )


def build_user_actions_keyboard(user_id: int):
    """Кнопки дій у картці конкретного користувача."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎭 Змінити роль", callback_data=f"admin_role_menu:{user_id}")
    builder.button(text="✏️ Змінити ПІБ", callback_data=f"admin_rename_ask:{user_id}")
    builder.button(text="🗑 Видалити з бази", callback_data=f"admin_del_ask:{user_id}")
    builder.button(text="⬅️ Назад до списку", callback_data="admin_back_to_list")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def build_cancel_admin_input_keyboard(back_callback: str = "admin_back_to_list"):
    """Кнопка скасування під час текстового вводу в адмінці."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data=back_callback)
    return builder.as_markup()


# =====================================================================
# ГОЛОВНЕ МЕНЮ АДМІНІСТРАТОРА (/admin та кнопка нижнього меню)
# =====================================================================
@admin_router.message(or_f(Command("admin"), F.text == BTN_ADMIN_PANEL))
async def cmd_admin_panel(message: Message, db_user: dict, state: FSMContext):
    await state.clear()
    users = await get_all_users(DB_PATH)
    text = (
        "🛠 <b>Панель адміністратора</b>\n\n"
        f"Зареєстровано користувачів: <b>{len(users)}</b>.\n"
        "Оберіть співробітника для редагування або додайте нового:"
    )
    await message.answer(text, reply_markup=build_users_list_keyboard(users))


@admin_router.callback_query(F.data == "admin_back_to_list")
async def cb_back_to_users_list(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    users = await get_all_users(DB_PATH)
    text = (
        "🛠 <b>Панель адміністратора</b>\n\n"
        f"Зареєстровано користувачів: <b>{len(users)}</b>.\n"
        "Оберіть співробітника для редагування або додайте нового:"
    )
    await callback.message.edit_text(text, reply_markup=build_users_list_keyboard(users))


# =====================================================================
# КАРТКА КОРИСТУВАЧА (Вибір дії)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_user:"))
async def cb_open_user_card(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = int(callback.data.split(":")[1])
    user = await get_user_by_id(DB_PATH, user_id)
    if not user:
        await callback.answer("Користувача не знайдено.", show_alert=True)
        return await cb_back_to_users_list(callback, state)
    await callback.answer()
    await callback.message.edit_text(
        text=format_user_card_text(user),
        reply_markup=build_user_actions_keyboard(user_id)
    )


# =====================================================================
# ЗМІНА РОЛІ КОРИСТУВАЧА
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_role_menu:"))
async def cb_role_selection_menu(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    user = await get_user_by_id(DB_PATH, user_id)
    if not user:
        await callback.answer("Користувача не знайдено.", show_alert=True)
        return
    await callback.answer()
    builder = InlineKeyboardBuilder()
    for role_code, label in [
        ("TRAVELER", "👤 TRAVELER"),
        ("OPERATOR", "🎧 OPERATOR"),
        ("ADMIN", "🛡 ADMIN")
    ]:
        current_mark = "✅ " if user["role"] == role_code else ""
        builder.button(
            text=f"{current_mark}{label}",
            callback_data=f"admin_set_role:{user_id}:{role_code}"
        )
    builder.button(text="⬅️ Назад до картки", callback_data=f"admin_user:{user_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"🎭 <b>Зміна ролі для {user['full_name']}</b>\n"
        f"Поточна роль: <b>{user['role']}</b>\n\n"
        "Оберіть нову роль:",
        reply_markup=builder.as_markup()
    )


@admin_router.callback_query(F.data.startswith("admin_set_role:"))
async def cb_save_new_role(callback: CallbackQuery, db_user: dict, bot: Bot):
    _, user_id_str, new_role = callback.data.split(":")
    user_id = int(user_id_str)
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("Користувача не знайдено.", show_alert=True)
        return
    # Захист від зняття прав адміністратора із самого себе або головного адміна з .env
    if new_role != "ADMIN" and (target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID):
        await callback.answer("⛔️ Не можна понизити роль власного акаунту або головного адміна!", show_alert=True)
        return
    success = await update_user_role(DB_PATH, user_id, new_role)
    if not success:
        await callback.answer("❌ Помилка при оновленні ролі.", show_alert=True)
        return
    await callback.answer(f"✅ Роль змінено на {new_role}")
    # Оновлюємо нижнє меню кнопок у самого користувача
    try:
        await bot.send_message(
            chat_id=target_user["tg_id"],
            text=f"🔔 Вашу роль у системі змінено на <b>{new_role}</b>. Меню кнопок оновлено.",
            reply_markup=get_main_menu_keyboard(new_role)
        )
    except Exception:
        pass
    # Повертаємо оновлену картку користувача
    updated_user = await get_user_by_id(DB_PATH, user_id)
    await callback.message.edit_text(
        text=format_user_card_text(updated_user),
        reply_markup=build_user_actions_keyboard(user_id)
    )


# =====================================================================
# РЕДАГУВАННЯ ПІБ КОРИСТУВАЧА (FSM)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_rename_ask:"))
async def cb_ask_new_fullname(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[1])
    user = await get_user_by_id(DB_PATH, user_id)
    if not user:
        await callback.answer("Користувача не знайдено.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(rename_user_id=user_id)
    await state.set_state(AdminMenu.waiting_for_renamed_fullname)
    await callback.message.edit_text(
        f"✏️ <b>Редагування ПІБ</b>\n"
        f"Поточне ім'я: <b>{user['full_name']}</b>\n\n"
        "Введіть нове <b>Ім'я та Прізвище</b> співробітника:",
        reply_markup=build_cancel_admin_input_keyboard(f"admin_user:{user_id}")
    )


@admin_router.message(AdminMenu.waiting_for_renamed_fullname, F.text)
async def process_renamed_fullname(message: Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    user_id = data.get("rename_user_id")
    await state.clear()
    if len(new_name) < 2:
        await message.answer("❌ Ім'я занадто коротке. Спробуйте ще раз через /admin.")
        return
    success = await update_user_name(DB_PATH, user_id, new_name)
    user = await get_user_by_id(DB_PATH, user_id)
    if success and user:
        await message.answer(
            f"✅ ПІБ успішно змінено на <b>{new_name}</b>!\n\n" + format_user_card_text(user),
            reply_markup=build_user_actions_keyboard(user_id)
        )
    else:
        await message.answer("❌ Не вдалося оновити ім'я користувача.")


# =====================================================================
# 5. ВИДАЛЕННЯ КОРИСТУВАЧА З БАЗИ (З підтвердженням)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_del_ask:"))
async def cb_ask_delete_user(callback: CallbackQuery, db_user: dict):
    user_id = int(callback.data.split(":")[1])
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("Користувача вже видалено.", show_alert=True)
        return
    # Захист від самовидалення та видалення головного адміна з .env
    if target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID:
        await callback.answer("⛔️ Ви не можете видалити власний акаунт або головного адміністратора!", show_alert=True)
        return
    await callback.answer()
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Так, видалити остаточно", callback_data=f"admin_del_confirm:{user_id}")
    builder.button(text="⬅️ Скасувати", callback_data=f"admin_user:{user_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        f"⚠️ <b>Підтвердження видалення</b>\n\n"
        f"Ви дійсно хочете видалити користувача <b>{target_user['full_name']}</b> "
        f"(ID: <code>{target_user['tg_id']}</code>) із системи?\n\n"
        "<i>Користувач одразу втратить доступ до бота, але історія його попередніх поїздок збережеться.</i>",
        reply_markup=builder.as_markup()
    )


@admin_router.callback_query(F.data.startswith("admin_del_confirm:"))
async def cb_confirm_delete_user(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    user_id = int(callback.data.split(":")[1])
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("Користувача вже видалено.", show_alert=True)
        return await cb_back_to_users_list(callback, state)
    if target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID:
        await callback.answer("⛔️ Неможливо видалити цього адміністратора!", show_alert=True)
        return
    deleted = await delete_user(DB_PATH, user_id)
    if not deleted:
        await callback.answer("❌ Помилка при видаленні з бази даних.", show_alert=True)
        return
    await callback.answer(f"🗑 Користувача {target_user['full_name']} видалено.")
    # Прибираємо нижню клавіатуру у видаленого користувача
    try:
        await bot.send_message(
            chat_id=target_user["tg_id"],
            text="⛔️ Ваш доступ до системи моніторингу поїздок було відкликано адміністратором.",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception:
        pass
    # Повертаємо адміна до оновленого списку користувачів
    users = await get_all_users(DB_PATH)
    await callback.message.edit_text(
        f"✅ Користувача <b>{target_user['full_name']}</b> видалено з бази.\n\n"
        f"🛠 <b>Панель адміністратора</b> (усього: {len(users)}):",
        reply_markup=build_users_list_keyboard(users)
    )


# =====================================================================
# 6. ДОДАВАННЯ НОВОГО КОРИСТУВАЧА (FSM)
# =====================================================================
@admin_router.callback_query(F.data == "admin_add_new_user")
async def process_add_new_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Додавання нового користувача</b>\n\n"
        "Введіть <b>Telegram ID</b> співробітника (лише цифри, наприклад <code>123456789</code>):",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_tgid)


@admin_router.message(AdminMenu.waiting_for_new_user_tgid, F.text.regexp(r"^\d+$"))
async def process_new_user_tgid(message: Message, state: FSMContext):
    tg_id = int(message.text.strip())
    await state.update_data(new_user_tgid=tg_id)
    await message.answer(
        f"Telegram ID: <code>{tg_id}</code> прийнято.\n"
        "Тепер введіть <b>Ім'я та Прізвище</b> співробітника:",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_name)


@admin_router.message(AdminMenu.waiting_for_new_user_tgid)
async def process_invalid_tgid(message: Message):
    await message.answer(
        "❌ Помилка! Telegram ID має складатися лише з цифр. Спробуйте ще раз:",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )


@admin_router.message(AdminMenu.waiting_for_new_user_name, F.text)
async def process_new_user_name(message: Message, state: FSMContext, bot: Bot):
    full_name = message.text.strip()
    user_data = await state.get_data()
    tg_id = user_data.get("new_user_tgid")
    await state.clear()
    added_user = await add_new_user(DB_PATH, tg_id=tg_id, full_name=full_name, role="TRAVELER")
    if added_user:
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=(
                    f"✅ <b>Ваш акаунт активовано адміністратором!</b>\n"
                    f"Ім'я в системі: <b>{full_name}</b>\n\n"
                    "Тепер ви можете користуватися кнопками меню нижче:"
                ),
                reply_markup=get_main_menu_keyboard("TRAVELER")
            )
        except Exception:
            pass
        # Одразу відкриваємо картку щойно створеного користувача (щоб за потреби змінити роль)
        await message.answer(
            f"✅ Користувача <b>{full_name}</b> успішно створено!\n\n"
            + format_user_card_text(added_user),
            reply_markup=build_user_actions_keyboard(added_user["id"])
        )
    else:
        await message.answer("❌ Помилка. Користувач із таким Telegram ID вже існує в базі.")
