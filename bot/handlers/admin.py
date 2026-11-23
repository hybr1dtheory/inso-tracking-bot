from aiogram import Bot, Router, F
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from config import DB_PATH, ADMIN_TG_ID
from keyboards.reply import BTN_ADMIN_PANEL, get_main_menu_keyboard
from keyboards.inline import (
    build_users_list_keyboard,
    build_user_actions_keyboard,
    build_admin_role_keyboard,
    build_admin_delete_confirm_keyboard,
    build_cancel_admin_input_keyboard
)
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


# --- Допоміжні функції адмінки ---
def format_user_card_text(user: dict) -> str:
    """Текст картки вибраного користувача."""
    username_str = f"@{user['username']}" if user.get("username") else "not specified"
    return (
        f"👤 <b>User information</b>\n\n"
        f"• <b>Full Name:</b> {user['full_name']}\n"
        f"• <b>Telegram ID:</b> <code>{user['tg_id']}</code>\n"
        f"• <b>Username:</b> {username_str}\n"
        f"• <b>Role:</b> <b>{user['role']}</b>\n\n"
        "Select an action:"
    )


# =====================================================================
# ГОЛОВНЕ МЕНЮ АДМІНІСТРАТОРА (/admin та кнопка нижнього меню)
# =====================================================================
@admin_router.message(or_f(Command("admin"), F.text == BTN_ADMIN_PANEL))
async def cmd_admin_panel(message: Message, db_user: dict, state: FSMContext):
    await state.clear()
    await state.update_data(admin_page=0)
    users = await get_all_users(DB_PATH)
    text = (
        "🛠 <b>Administrator Panel</b>\n\n"
        f"Registered users: <b>{len(users)}</b>.\n"
        "Select an employee to edit or add a new one:"
    )
    await message.answer(text, reply_markup=build_users_list_keyboard(users, page=0))


@admin_router.callback_query(F.data == "admin_back_to_list")
async def cb_back_to_users_list(callback: CallbackQuery, state: FSMContext):
    page = await _reset_state_keep_page(state)
    await callback.answer()
    users = await get_all_users(DB_PATH)
    text = (
        "🛠 <b>Administrator Panel</b>\n\n"
        f"Registered users: <b>{len(users)}</b>.\n"
        "Select an employee to edit or add a new one:"
    )
    await callback.message.edit_text(text, reply_markup=build_users_list_keyboard(users, page=page))


async def _reset_state_keep_page(state: FSMContext) -> int:
    """Скидає активний крок FSM, але зберігає номер поточної сторінки списку."""
    data = await state.get_data()
    page = data.get("admin_page", 0)
    await state.clear()
    await state.update_data(admin_page=page)
    return page


@admin_router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    """Закриває індикатор завантаження при натисканні на лічильник сторінок чи неактивну стрілку."""
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_users_page:"))
async def cb_paginate_users_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.split(":")[1])
    await state.update_data(admin_page=page)
    users = await get_all_users(DB_PATH)
    await callback.message.edit_reply_markup(
        reply_markup=build_users_list_keyboard(users, page=page)
    )


# =====================================================================
# КАРТКА КОРИСТУВАЧА (Вибір дії)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_user:"))
async def cb_open_user_card(callback: CallbackQuery, state: FSMContext):
    await _reset_state_keep_page(state)
    user_id = int(callback.data.split(":")[1])
    user = await get_user_by_id(DB_PATH, user_id)
    if not user:
        await callback.answer("User not found.", show_alert=True)
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
        await callback.answer("User not found.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"🎭 <b>Role change for {user['full_name']}</b>\n"
        f"Current role: <b>{user['role']}</b>\n\n"
        "Select new role:",
        reply_markup=build_admin_role_keyboard(user_id, user["role"])
    )


@admin_router.callback_query(F.data.startswith("admin_set_role:"))
async def cb_save_new_role(callback: CallbackQuery, db_user: dict, bot: Bot):
    _, user_id_str, new_role = callback.data.split(":")
    user_id = int(user_id_str)
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("User not found.", show_alert=True)
        return
    # Захист від зняття прав адміністратора із самого себе або головного адміна з .env
    if new_role != "ADMIN" and (target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID):
        await callback.answer("⛔️ You cannot demote the role of your own account or the main admin!", show_alert=True)
        return
    success = await update_user_role(DB_PATH, user_id, new_role)
    if not success:
        await callback.answer("❌ Error updating a role.", show_alert=True)
        return
    await callback.answer(f"✅ The role has been changed to {new_role}")
    # Оновлюємо нижнє меню кнопок у самого користувача
    try:
        await bot.send_message(
            chat_id=target_user["tg_id"],
            text=f"🔔 Your role in the system has been changed to <b>{new_role}</b>.",
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
        await callback.answer("User not found.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(rename_user_id=user_id)
    await state.set_state(AdminMenu.waiting_for_renamed_fullname)
    await callback.message.edit_text(
        f"✏️ <b>Editing Full Name</b>\n"
        f"Current name: <b>{user['full_name']}</b>\n\n"
        "Enter the employee's new <b>First Name and Last Name</b> separated by a space:",
        reply_markup=build_cancel_admin_input_keyboard(f"admin_user:{user_id}")
    )


@admin_router.message(AdminMenu.waiting_for_renamed_fullname, F.text)
async def process_renamed_fullname(message: Message, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    user_id = data.get("rename_user_id")
    await state.clear()
    if len(new_name) < 3:
        await message.answer("❌ The name is too short. Please try again via /admin")
        return
    success = await update_user_name(DB_PATH, user_id, new_name)
    user = await get_user_by_id(DB_PATH, user_id)
    if success and user:
        await message.answer(
            f"✅ The full name has been successfully changed to <b>{new_name}</b>!\n\n" + format_user_card_text(user),
            reply_markup=build_user_actions_keyboard(user_id)
        )
    else:
        await message.answer("❌ Unable to update the user name.")


# =====================================================================
# 5. ВИДАЛЕННЯ КОРИСТУВАЧА З БАЗИ (З підтвердженням)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_del_ask:"))
async def cb_ask_delete_user(callback: CallbackQuery, db_user: dict):
    user_id = int(callback.data.split(":")[1])
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("The user has already been deleted.", show_alert=True)
        return
    if target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID:
        await callback.answer("⛔️ You cannot delete your own account or the main administrator!", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"⚠️ <b>Deletion Confirmation</b>\n\n"
        f"Are you sure you want to delete this user <b>{target_user['full_name']}</b> "
        f"(ID: <code>{target_user['tg_id']}</code>) from the system?\n\n"
        "<i>The user will immediately lose access to the bot, but their travel history will be saved.</i>",
        reply_markup=build_admin_delete_confirm_keyboard(user_id)
    )


@admin_router.callback_query(F.data.startswith("admin_del_confirm:"))
async def cb_confirm_delete_user(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    user_id = int(callback.data.split(":")[1])
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("The user has already been deleted.", show_alert=True)
        return await cb_back_to_users_list(callback, state)
    if target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID:
        await callback.answer("⛔️ Unable to remove this administrator!", show_alert=True)
        return
    deleted = await delete_user(DB_PATH, user_id)
    if not deleted:
        await callback.answer("❌ Error while deleting from the database.", show_alert=True)
        return
    await callback.answer(f"🗑 The user {target_user['full_name']} has been deleted.")
    # Прибираємо нижню клавіатуру у видаленого користувача
    try:
        await bot.send_message(
            chat_id=target_user["tg_id"],
            text="⛔️ Your access to the trip monitoring system has been revoked by the administrator.",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception:
        pass
    # Повертаємо адміна до оновленого списку користувачів
    page = await _reset_state_keep_page(state)
    users = await get_all_users(DB_PATH)
    await callback.message.edit_text(
        f"✅ The user <b>{target_user['full_name']}</b> has been deleted.\n\n"
        f"🛠 <b>Administrator Panel</b> (users: {len(users)}):",
        reply_markup=build_users_list_keyboard(users, page=page)
    )


# =====================================================================
# 6. ДОДАВАННЯ НОВОГО КОРИСТУВАЧА (FSM)
# =====================================================================
@admin_router.callback_query(F.data == "admin_add_new_user")
async def process_add_new_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Adding a New User</b>\n\n"
        "Enter the employee's <b>Telegram ID</b> (numbers only, for example <code>1234567890</code>):",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_tgid)


@admin_router.message(AdminMenu.waiting_for_new_user_tgid, F.text.regexp(r"^\d+$"))
async def process_new_user_tgid(message: Message, state: FSMContext):
    tg_id = int(message.text.strip())
    await state.update_data(new_user_tgid=tg_id)
    await message.answer(
        f"Telegram ID: <code>{tg_id}</code> accepted.\n"
        "Enter employee's <b>First Name and Last Name</b> separated by space:",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_name)


@admin_router.message(AdminMenu.waiting_for_new_user_tgid)
async def process_invalid_tgid(message: Message):
    await message.answer(
        "❌ Error! A Telegram ID must consist only of numbers. Please try again:",
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
                    f"✅ <b>Your account has been activated by an administrator!</b>\n"
                    f"Name: <b>{full_name}</b>\n\n"
                    "You can now use the menu buttons below:"
                ),
                reply_markup=get_main_menu_keyboard("TRAVELER")
            )
        except Exception:
            pass
        # Одразу відкриваємо картку щойно створеного користувача (щоб за потреби змінити роль)
        await message.answer(
            f"✅ The user <b>{full_name}</b> was successfully added!\n\n"
            + format_user_card_text(added_user),
            reply_markup=build_user_actions_keyboard(added_user["id"])
        )
    else:
        await message.answer("❌ Error. A user with this Telegram ID already exists in the database.")
