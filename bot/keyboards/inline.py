from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


def build_planned_trip_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Клавіатура для запланованої поїздки.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Вирушили (Почати зараз)", callback_data=f"trv_start_now:{trip_id}")
    builder.button(text="❌ Скасувати поїздку", callback_data=f"trv_cancel_planned:{trip_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_traveler_trip_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Основна клавіатура керування активною поїздкою.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Чекін: Все гаразд", callback_data=f"trv_checkin:{trip_id}")
    builder.button(text="💬 Чекін з приміткою", callback_data=f"trv_note:{trip_id}")
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
    '''Кнопка швидкої відповіді мандрівника на запит оператора.'''
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Підтверджую: Все гаразд (У дорозі)",
        callback_data=f"trv_checkin:{trip_id}"
    )
    builder.button(
        text="💬 Відповісти з приміткою",
        callback_data=f"trv_note:{trip_id}"
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
