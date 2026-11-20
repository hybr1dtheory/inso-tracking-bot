from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


USERS_PER_PAGE = 8

def _paginate_slice(items: list, page: int, per_page: int = USERS_PER_PAGE) -> tuple[list, int, int]:
    """
    Повертає: (зріз_елементів_сторінки, безпечний_індекс_сторінки, всього_сторінок).
    Автоматично коригує page, якщо кількість елементів зменшилася (напр. після видалення).
    """
    total_pages = max(1, (len(items) + per_page - 1) // per_page)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * per_page
    return items[start:start + per_page], safe_page, total_pages


def _build_pagination_row(prefix: str, page: int, total_pages: int) -> list[InlineKeyboardButton]:
    """Формує фіксований ряд навігації з 3 кнопок."""
    prev_btn = (
        InlineKeyboardButton(text="⬅️", callback_data=f"{prefix}:{page - 1}")
        if page > 0
        else InlineKeyboardButton(text="·", callback_data="noop")
    )
    counter_btn = InlineKeyboardButton(
        text=f"📄 {page + 1}/{total_pages}",
        callback_data="noop"
    )
    next_btn = (
        InlineKeyboardButton(text="➡️", callback_data=f"{prefix}:{page + 1}")
        if page < total_pages - 1
        else InlineKeyboardButton(text="·", callback_data="noop")
    )
    return [prev_btn, counter_btn, next_btn]


def build_planned_trip_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Клавіатура для запланованої поїздки.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Вирушили (Почати зараз)", callback_data=f"trv_start_now:{trip_id}")
    builder.button(text="❌ Скасувати поїздку", callback_data=f"trv_cancel_planned:{trip_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_traveler_trip_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    """Основна клавіатура керування активною поїздкою."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📍 Зробити чекін (вказати локацію)", callback_data=f"trv_note:{trip_id}")
    builder.button(text="🆘 Потрібна допомога", callback_data=f"trv_help_ask:{trip_id}")
    builder.button(text="🔄 Оновити статус", callback_data=f"trv_refresh:{trip_id}")
    builder.button(text="🏁 Прибули (Закрити поїздку)", callback_data=f"trv_close_ask:{trip_id}")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def build_close_confirm_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Підтвердження закриття поїздки.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, ми прибули", callback_data=f"trv_close_confirm:{trip_id}")
    builder.button(text="⬅️ Назад до поїздки", callback_data=f"trv_refresh:{trip_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_cancel_note_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Скасування вводу примітки.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data=f"trv_refresh:{trip_id}")
    return builder.as_markup()


def build_checkin_response_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    """Клавіатура у повідомленні запиту на чекін від оператора або Watchdog."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📍 Зробити чекін (вказати локацію)",
        callback_data=f"trv_note:{trip_id}"
    )
    builder.button(
        text="🆘 Потрібна допомога",
        callback_data=f"trv_help_ask:{trip_id}"
    )
    builder.adjust(1)
    return builder.as_markup()


def build_help_comment_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    """Клавіатура під час запиту про допомогу"""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚨 Надіслати БЕЗ коментаря",
        callback_data=f"trv_help_send_empty:{trip_id}"
    )
    builder.button(
        text="❌ Скасувати",
        callback_data=f"trv_refresh:{trip_id}"
    )
    builder.adjust(1)
    return builder.as_markup()


def get_trip_status_icon(trip: dict) -> str:
    '''Визначає іконку стану поїздки для кнопок та списків.'''
    if trip["status"] == "PLANNED":
        return "📅"
    minutes_ago = max(0, trip["minutes_ago"] or 0)
    if minutes_ago >= trip["checkin_interval_min"]:
        return "🔴"
    if trip["pending_request_sent_at"]:
        return "🟡"
    return "🟢"


def build_operator_dashboard_keyboard(trips: list[dict]) -> InlineKeyboardMarkup:
    '''Будує список інлайн-кнопок для переходу в деталі кожної поїздки.'''
    builder = InlineKeyboardBuilder()
    for t in trips:
        icon = get_trip_status_icon(t)
        leader_name = next((m["full_name"] for m in t["crew"] if m["is_leader"]), "Екіпаж")
        if t["status"] == "ACTIVE":
            mins = max(0, t["minutes_ago"] or 0)
            time_info = f"{mins} хв тому"
        else:
            time_info = "Заплановано"
        builder.button(
            text=f"{icon} #{t['id']} {t['route_code']} | {leader_name} ({time_info})",
            callback_data=f"op_view_trip:{t['id']}"
        )
    builder.button(text="🔄 Оновити дашборд", callback_data="op_dashboard_refresh")
    builder.adjust(1)
    return builder.as_markup()


def build_operator_trip_manage_keyboard(trip: dict) -> InlineKeyboardMarkup:
    '''Клавіатура керування конкретною поїздкою в режимі перегляду оператора.'''
    builder = InlineKeyboardBuilder()
    trip_id = trip["id"]
    if trip["status"] == "ACTIVE":
        builder.button(text="📡 Надіслати запит на чекін", callback_data=f"op_card_ping:{trip_id}")
        builder.button(text="🔄 Оновити картку", callback_data=f"op_view_trip:{trip_id}")
        builder.button(text="🏁 Примусово закрити поїздку", callback_data=f"op_force_close_ask:{trip_id}")
        builder.adjust(1, 1, 1)
    elif trip["status"] == "PLANNED":
        builder.button(text="🚀 Розпочати моніторинг зараз", callback_data=f"op_force_start:{trip_id}")
        builder.button(text="❌ Скасувати заплановану поїздку", callback_data=f"op_force_cancel:{trip_id}")
        builder.adjust(1)
    builder.button(text="⬅️ Назад до списку поїздок", callback_data="op_dashboard_refresh")
    builder.adjust(1)
    return builder.as_markup()


def build_operator_close_confirm_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Підтвердження примусового закриття поїздки оператором.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, закрити поїздку", callback_data=f"op_force_close_confirm:{trip_id}")
    builder.button(text="⬅️ Скасувати", callback_data=f"op_view_trip:{trip_id}")
    builder.adjust(1)
    return builder.as_markup()


# =====================================================================
# КЛАВІАТУРИ СТВОРЕННЯ ПОЇЗДКИ (Мандрівник)
# =====================================================================
def build_cancel_creation_keyboard() -> InlineKeyboardMarkup:
    """Кнопка скасування під час текстових кроків оформлення поїздки."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати створення", callback_data="trip_create_cancel")
    return builder.as_markup()


def build_companions_keyboard(
    other_users: list[dict], selected_ids: list[int], page: int = 0
) -> InlineKeyboardMarkup:
    """Клавіатура мульти-вибору супутників із пагінацією при створенні поїздки."""
    builder = InlineKeyboardBuilder()
    # Верхня кнопка підтвердження (відображає загальну кількість обраних по всіх сторінках)
    if not selected_ids:
        confirm_text = "👤 Їду сам(а) — Завершити оформлення"
    else:
        confirm_text = f"✅ Підтвердити групу (супутників: {len(selected_ids)})"
    builder.row(
        InlineKeyboardButton(text=confirm_text, callback_data="trip_confirm_companions")
    )
    # Список користувачів на поточній сторінці
    page_users, safe_page, total_pages = _paginate_slice(other_users, page)
    for u in page_users:
        mark = "✅" if u["id"] in selected_ids else "⬜️"
        builder.row(
            InlineKeyboardButton(
                text=f"{mark} {u['full_name']}",
                callback_data=f"trip_toggle_comp:{u['id']}"
            )
        )
    # Ряд пагінації (з'являється автоматично, коли людей більше ніж USERS_PER_PAGE)
    if total_pages > 1:
        builder.row(*_build_pagination_row("trip_comp_page", safe_page, total_pages))
    # Нижня кнопка скасування
    builder.row(
        InlineKeyboardButton(text="❌ Скасувати", callback_data="trip_create_cancel")
    )
    return builder.as_markup()


# =====================================================================
# КЛАВІАТУРИ ПАНЕЛІ АДМІНІСТРАТОРА (/admin)
# =====================================================================
def build_users_list_keyboard(users: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    """Головний список усіх користувачів в адмін-панелі із пагінацією."""
    builder = InlineKeyboardBuilder()
    page_users, safe_page, total_pages = _paginate_slice(users, page)
    for u in page_users:
        builder.row(
            InlineKeyboardButton(
                text=f"👤 {u['full_name']} ({u['role']})",
                callback_data=f"admin_user:{u['id']}"
            )
        )
    if total_pages > 1:
        builder.row(*_build_pagination_row("admin_users_page", safe_page, total_pages))
    builder.row(
        InlineKeyboardButton(text="➕ Додати нового користувача", callback_data="admin_add_new_user")
    )
    return builder.as_markup()


def build_user_actions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Кнопки дій у картці конкретного користувача."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🎭 Змінити роль", callback_data=f"admin_role_menu:{user_id}")
    builder.button(text="✏️ Змінити ПІБ", callback_data=f"admin_rename_ask:{user_id}")
    builder.button(text="🗑 Видалити з бази", callback_data=f"admin_del_ask:{user_id}")
    builder.button(text="⬅️ Назад до списку", callback_data="admin_back_to_list")
    builder.adjust(2, 1, 1)
    return builder.as_markup()


def build_admin_role_keyboard(user_id: int, current_role: str) -> InlineKeyboardMarkup:
    """Меню вибору нової ролі для користувача."""
    builder = InlineKeyboardBuilder()
    roles = [
        ("TRAVELER", "👤 TRAVELER"),
        ("OPERATOR", "🎧 OPERATOR"),
        ("ADMIN", "🛡 ADMIN")
    ]
    for role_code, label in roles:
        current_mark = "✅ " if current_role == role_code else ""
        builder.button(
            text=f"{current_mark}{label}",
            callback_data=f"admin_set_role:{user_id}:{role_code}"
        )
    builder.button(text="⬅️ Назад до картки", callback_data=f"admin_user:{user_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_admin_delete_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Підтвердження остаточного видалення користувача."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Так, видалити остаточно", callback_data=f"admin_del_confirm:{user_id}")
    builder.button(text="⬅️ Скасувати", callback_data=f"admin_user:{user_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_cancel_admin_input_keyboard(back_callback: str = "admin_back_to_list") -> InlineKeyboardMarkup:
    """Кнопка скасування під час текстового вводу в адмінці."""
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data=back_callback)
    return builder.as_markup()
