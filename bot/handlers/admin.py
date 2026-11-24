from html import escape
import logging
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from config import ADMIN_TG_ID, DB_PATH
from database.queries import (
    add_new_user,
    delete_user,
    get_all_users,
    get_user_by_id,
    get_user_by_tg_id,
    update_user_name,
    update_user_role,
    update_user_team,
)
from keyboards.inline import (
    build_admin_delete_confirm_keyboard,
    build_admin_role_keyboard,
    build_cancel_admin_input_keyboard,
    build_user_actions_keyboard,
    build_users_list_keyboard,
)
from keyboards.reply import BTN_ADMIN_PANEL, get_main_menu_keyboard


logger = logging.getLogger(__name__)
admin_router = Router()

VALID_ROLES = {"TRAVELER", "OPERATOR", "ADMIN"}
MAX_NAME_LENGTH = 80
MAX_TEAM_LENGTH = 10


class AdminMenu(StatesGroup):
    waiting_for_new_user_tgid = State()
    waiting_for_new_user_name = State()
    waiting_for_new_user_team = State()
    waiting_for_renamed_fullname = State()
    waiting_for_edited_team = State()


# =====================================================================
# ДОПОМІЖНІ ФУНКЦІЇ АДМІНКИ
# =====================================================================
def format_user_card_text(user: dict) -> str:
    """Текст картки вибраного користувача (HTML-безпечно)."""
    name_safe = escape(user["full_name"])
    team_safe = escape(user["team"]) if user.get("team") else "Not specified"
    return (
        f"👤 <b>User Card</b>\n\n"
        f"• <b>Full Name:</b> {name_safe}\n"
        f"• <b>Team:</b> {team_safe}\n"
        f"• <b>Telegram ID:</b> <code>{user['tg_id']}</code>\n"
        f"• <b>Role:</b> <b>{user['role']}</b>\n\n"
        "Select an action:"
    )


async def _reset_state_keep_page(state: FSMContext) -> int:
    """Скидає активний крок FSM, але зберігає номер поточної сторінки списку."""
    data = await state.get_data()
    page = data.get("admin_page", 0)
    await state.clear()
    await state.update_data(admin_page=page)
    return page


async def _show_users_list(callback: CallbackQuery, state: FSMContext, prefix: str = ""):
    """Оновлює повідомлення списком користувачів зі збереженням поточної сторінки."""
    page = await _reset_state_keep_page(state)
    users = await get_all_users(DB_PATH)
    header = f"{prefix}\n\n" if prefix else ""
    text = (
        f"{header}🛠 <b>Administrator Panel</b>\n\n"
        f"Registered users: <b>{len(users)}</b>.\n"
        "Select an employee to edit or add a new one:"
    )
    try:
        await callback.message.edit_text(
            text,
            reply_markup=build_users_list_keyboard(users, page=page)
        )
    except TelegramBadRequest:
        pass


# =====================================================================
# 1ГОЛОВНЕ МЕНЮ АДМІНІСТРАТОРА (/admin та пагінація)
# =====================================================================
@admin_router.message(or_f(Command("admin"), F.text == BTN_ADMIN_PANEL))
async def cmd_admin_panel(message: Message, state: FSMContext):
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
    await callback.answer()
    await _show_users_list(callback, state)


@admin_router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    """
    Закриває індикатор завантаження при натисканні на лічильник сторінок чи неактивну стрілку.
    Обслуговує пагінацію глобально (і в адмінці, і у виборі супутників).
    """
    await callback.answer()


@admin_router.callback_query(F.data.startswith("admin_users_page:"))
async def cb_paginate_users_list(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    page = int(callback.data.split(":")[1])
    await state.update_data(admin_page=page)
    users = await get_all_users(DB_PATH)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=build_users_list_keyboard(users, page=page)
        )
    except TelegramBadRequest:
        pass


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
        await _show_users_list(callback, state)
        return
    await callback.answer()
    try:
        await callback.message.edit_text(
            text=format_user_card_text(user),
            reply_markup=build_user_actions_keyboard(user_id)
        )
    except TelegramBadRequest:
        pass


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
        f"🎭 <b>Role change for {escape(user['full_name'])}</b>\n"
        f"Current role: <b>{user['role']}</b>\n\n"
        "Select a new role:",
        reply_markup=build_admin_role_keyboard(user_id, user["role"])
    )


@admin_router.callback_query(F.data.startswith("admin_set_role:"))
async def cb_save_new_role(callback: CallbackQuery, db_user: dict, bot: Bot):
    _, user_id_str, new_role = callback.data.split(":")
    user_id = int(user_id_str)
    if new_role not in VALID_ROLES:
        await callback.answer("❌ Invalid role.", show_alert=True)
        return
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("User not found.", show_alert=True)
        return
    if target_user["role"] == new_role:
        await callback.answer(f"Role is already set to {new_role}.")
        await callback.message.edit_text(
            text=format_user_card_text(target_user),
            reply_markup=build_user_actions_keyboard(user_id)
        )
        return
    # Захист від зняття прав адміністратора із самого себе або головного адміна з .env
    if new_role != "ADMIN" and (target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID):
        await callback.answer(
            "⛔️ You cannot demote your own account or the main administrator!",
            show_alert=True
        )
        return
    success = await update_user_role(DB_PATH, user_id, new_role)
    if not success:
        await callback.answer("❌ Error updating role.", show_alert=True)
        return
    logger.info(
        f"Admin {db_user['full_name']} (id={db_user['id']}) changed role of "
        f"{target_user['full_name']} (id={user_id}) from {target_user['role']} to {new_role}."
    )
    await callback.answer(f"✅ Role changed to {new_role}")
    # Оновлюємо нижнє меню кнопок у самого користувача
    try:
        await bot.send_message(
            chat_id=target_user["tg_id"],
            text=f"🔔 Your role in the system has been changed to <b>{new_role}</b>.",
            reply_markup=get_main_menu_keyboard(new_role)
        )
    except Exception as e:
        logger.warning(f"Failed to notify user tg_id={target_user['tg_id']} about role change: {e}")
    # Повертаємо оновлену картку користувача
    updated_user = await get_user_by_id(DB_PATH, user_id)
    if not updated_user:
        return
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
        f"Current name: <b>{escape(user['full_name'])}</b>\n\n"
        "Enter the employee's new <b>First Name and Last Name</b> separated by a space:",
        reply_markup=build_cancel_admin_input_keyboard(f"admin_user:{user_id}")
    )


@admin_router.message(AdminMenu.waiting_for_renamed_fullname, F.text)
async def process_renamed_fullname(message: Message, db_user: dict, state: FSMContext):
    new_name = message.text.strip()
    data = await state.get_data()
    user_id = data.get("rename_user_id")
    if not user_id:
        await _reset_state_keep_page(state)
        return
    if len(new_name) < 3 or len(new_name) > MAX_NAME_LENGTH:
        await message.answer(
            f"❌ Full name must be between 3 and {MAX_NAME_LENGTH} characters. Please try again:",
            reply_markup=build_cancel_admin_input_keyboard(f"admin_user:{user_id}")
        )
        return
    await _reset_state_keep_page(state)
    success = await update_user_name(DB_PATH, user_id, new_name)
    user = await get_user_by_id(DB_PATH, user_id)
    if success and user:
        logger.info(
            f"Admin {db_user['full_name']} (id={db_user['id']}) renamed user id={user_id} to '{new_name}'."
        )
        await message.answer(
            f"✅ Full name updated to <b>{escape(new_name)}</b>!\n\n" + format_user_card_text(user),
            reply_markup=build_user_actions_keyboard(user_id)
        )
    else:
        await message.answer("❌ Failed to update user name.")


# =====================================================================
# ЗМІНА ВІДДІЛУ КОРИСТУВАЧА (FSM)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_team_ask:"))
async def cb_ask_new_team(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data.split(":")[1])
    user = await get_user_by_id(DB_PATH, user_id)
    if not user:
        await callback.answer("User not found.", show_alert=True)
        return
    await callback.answer()
    await state.update_data(edit_team_user_id=user_id)
    await state.set_state(AdminMenu.waiting_for_edited_team)
    current_team = escape(user["team"]) if user.get("team") else "Not specified"
    await callback.message.edit_text(
        f"🏢 <b>Edit Team</b>\n"
        f"User: <b>{escape(user['full_name'])}</b>\n"
        f"Current team: <b>{current_team}</b>\n\n"
        "Enter the new <b>Team / Department</b>:",
        reply_markup=build_cancel_admin_input_keyboard(f"admin_user:{user_id}")
    )


@admin_router.message(AdminMenu.waiting_for_edited_team, F.text)
async def process_edited_team(message: Message, db_user: dict, state: FSMContext):
    new_team = message.text.strip()
    data = await state.get_data()
    user_id = data.get("edit_team_user_id")
    if not user_id:
        await _reset_state_keep_page(state)
        return
    if len(new_team) < 2 or len(new_team) > MAX_TEAM_LENGTH:
        await message.answer(
            f"❌ Team name must be between 2 and {MAX_TEAM_LENGTH} characters. Please try again:",
            reply_markup=build_cancel_admin_input_keyboard(f"admin_user:{user_id}")
        )
        return
    await _reset_state_keep_page(state)
    success = await update_user_team(DB_PATH, user_id, new_team)
    user = await get_user_by_id(DB_PATH, user_id)
    if success and user:
        logger.info(
            f"Admin {db_user['full_name']} (id={db_user['id']}) updated team of user id={user_id} to '{new_team}'."
        )
        await message.answer(
            f"✅ Team updated to <b>{escape(new_team)}</b>!\n\n" + format_user_card_text(user),
            reply_markup=build_user_actions_keyboard(user_id)
        )
    else:
        await message.answer("❌ Failed to update user team.")


# =====================================================================
# ВИДАЛЕННЯ КОРИСТУВАЧА З БАЗИ (З підтвердженням)
# =====================================================================
@admin_router.callback_query(F.data.startswith("admin_del_ask:"))
async def cb_ask_delete_user(callback: CallbackQuery, db_user: dict):
    user_id = int(callback.data.split(":")[1])
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("User has already been deleted.", show_alert=True)
        return
    if target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID:
        await callback.answer(
            "⛔️ You cannot delete your own account or the main administrator!",
            show_alert=True
        )
        return
    await callback.answer()
    await callback.message.edit_text(
        f"⚠️ <b>Deletion Confirmation</b>\n\n"
        f"Are you sure you want to delete user <b>{escape(target_user['full_name'])}</b> "
        f"(ID: <code>{target_user['tg_id']}</code>) from the system?\n\n"
        "<i>The user will immediately lose access to the bot, while past trip history will be preserved.</i>",
        reply_markup=build_admin_delete_confirm_keyboard(user_id)
    )


@admin_router.callback_query(F.data.startswith("admin_del_confirm:"))
async def cb_confirm_delete_user(callback: CallbackQuery, db_user: dict, state: FSMContext, bot: Bot):
    user_id = int(callback.data.split(":")[1])
    target_user = await get_user_by_id(DB_PATH, user_id)
    if not target_user:
        await callback.answer("User has already been deleted.", show_alert=True)
        await _show_users_list(callback, state)
        return
    if target_user["id"] == db_user["id"] or target_user["tg_id"] == ADMIN_TG_ID:
        await callback.answer(
            "⛔️ You cannot delete your own account or the main administrator!",
            show_alert=True
        )
        return
    deleted = await delete_user(DB_PATH, user_id)
    if not deleted:
        await callback.answer("❌ Error while deleting from the database.", show_alert=True)
        return
    logger.info(
        f"Admin {db_user['full_name']} (id={db_user['id']}) deleted user "
        f"{target_user['full_name']} (id={user_id}, tg_id={target_user['tg_id']})."
    )
    await callback.answer(f"🗑 User {target_user['full_name']} deleted.")
    # Прибираємо нижню клавіатуру у видаленого користувача
    try:
        await bot.send_message(
            chat_id=target_user["tg_id"],
            text="⛔️ Your access to the trip monitoring system has been revoked by an administrator.",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.warning(f"Failed to notify deleted user tg_id={target_user['tg_id']}: {e}")
    # Повертаємо адміна до оновленого списку користувачів
    await _show_users_list(
        callback,
        state,
        prefix=f"✅ User <b>{escape(target_user['full_name'])}</b> has been deleted."
    )


# =====================================================================
# ДОДАВАННЯ НОВОГО КОРИСТУВАЧА (FSM)
# =====================================================================
@admin_router.callback_query(F.data == "admin_add_new_user")
async def process_add_new_user(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "➕ <b>Adding a New User</b>\n\n"
        "Step 1/3. Enter the employee's <b>Telegram ID</b> (numbers only, e.g., <code>1234567890</code>):",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_tgid)


@admin_router.message(AdminMenu.waiting_for_new_user_tgid, F.text.regexp(r"^\s*\d+\s*$"))
async def process_new_user_tgid(message: Message, state: FSMContext):
    tg_id = int(message.text.strip())
    existing_user = await get_user_by_tg_id(DB_PATH, tg_id)
    if existing_user:
        await message.answer(
            f"⚠️ A user with Telegram ID <code>{tg_id}</code> already exists "
            f"(<b>{escape(existing_user['full_name'])}</b>).\n"
            "Enter a different Telegram ID or cancel:",
            reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
        )
        return
    await state.update_data(new_user_tgid=tg_id)
    await message.answer(
        f"Telegram ID: <code>{tg_id}</code> accepted.\n\n"
        "Step 2/3. Enter the employee's <b>First Name and Last Name</b> separated by a space:",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_name)


@admin_router.message(AdminMenu.waiting_for_new_user_tgid)
async def process_invalid_tgid(message: Message):
    await message.answer(
        "❌ Invalid format. A Telegram ID must contain only numbers. Please try again:",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )


@admin_router.message(AdminMenu.waiting_for_new_user_name, F.text)
async def process_new_user_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 3 or len(full_name) > MAX_NAME_LENGTH:
        await message.answer(
            f"❌ Full name must be between 3 and {MAX_NAME_LENGTH} characters. Please try again:",
            reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
        )
        return
    await state.update_data(new_user_name=full_name)
    await message.answer(
        f"Full Name: <b>{escape(full_name)}</b> accepted.\n\n"
        "Step 3/3. Now enter the employee's <b>Team / Department</b>:",
        reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
    )
    await state.set_state(AdminMenu.waiting_for_new_user_team)


@admin_router.message(AdminMenu.waiting_for_new_user_team, F.text)
async def process_new_user_team(message: Message, db_user: dict, state: FSMContext, bot: Bot):
    team = message.text.strip()
    if len(team) < 2 or len(team) > MAX_TEAM_LENGTH:
        await message.answer(
            f"❌ Team name must be between 2 and {MAX_TEAM_LENGTH} characters. Please try again:",
            reply_markup=build_cancel_admin_input_keyboard("admin_back_to_list")
        )
        return
    user_data = await state.get_data()
    tg_id = user_data.get("new_user_tgid")
    full_name = user_data.get("new_user_name")
    await _reset_state_keep_page(state)
    if not tg_id or not full_name:
        await message.answer("❌ Session expired. Please try again via /admin.")
        return
    added_user = await add_new_user(
        DB_PATH,
        tg_id=tg_id,
        full_name=full_name,
        team=team,
        role="TRAVELER"
    )
    if added_user:
        logger.info(
            f"Admin {db_user['full_name']} (id={db_user['id']}) added new user "
            f"{full_name} (tg_id={tg_id}, team='{team}')."
        )
        try:
            await bot.send_message(
                chat_id=tg_id,
                text=(
                    "✅ <b>Your account has been activated by an administrator!</b>\n"
                    f"Name: <b>{escape(full_name)}</b>\n"
                    f"Team: <b>{escape(team)}</b>\n\n"
                    "You can now use the menu buttons below:"
                ),
                reply_markup=get_main_menu_keyboard("TRAVELER")
            )
        except Exception as e:
            logger.info(
                f"Could not send welcome message to new user tg_id={tg_id} "
                f"(user may not have started the bot yet): {e}"
            )
        await message.answer(
            f"✅ User <b>{escape(full_name)}</b> ({escape(team)}) successfully created!\n\n"
            + format_user_card_text(added_user),
            reply_markup=build_user_actions_keyboard(added_user["id"])
        )
    else:
        await message.answer("❌ Error. A user with this Telegram ID already exists in the database.")
