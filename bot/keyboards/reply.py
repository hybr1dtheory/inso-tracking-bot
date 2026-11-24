from aiogram.types import ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder


# Константи текстів кнопок (використовуються і в клавіатурі, і у фільтрах хендлерів)
BTN_NEW_TRIP = "🚗 New trip"
BTN_MY_TRIP = "📍 My trip"
BTN_TRIPS_DASHBOARD = "📋 Trips monitoring"
BTN_ADMIN_PANEL = "🛠 Admin panel"


def get_main_menu_keyboard(role: str) -> ReplyKeyboardMarkup:
    """
    Формує постійну нижню клавіатуру залежно від ролі користувача:
    - TRAVELER: New trip | My trip
    - OPERATOR: кнопки мандрівника + Trips monitoring
    - ADMIN: усі кнопки + Admin panel
    """
    builder = ReplyKeyboardBuilder()
    # Базові кнопки доступні всім
    builder.button(text=BTN_NEW_TRIP)
    builder.button(text=BTN_MY_TRIP)
    if role in ("OPERATOR", "ADMIN"):
        builder.button(text=BTN_TRIPS_DASHBOARD)
    if role == "ADMIN":
        builder.button(text=BTN_ADMIN_PANEL)
    # Розкладка по 2 кнопки в ряд: (2), (2, 1) або (2, 2) залежно від ролі
    builder.adjust(2)

    return builder.as_markup(
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Select an option from the menu..."
    )
