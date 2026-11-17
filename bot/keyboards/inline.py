from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


def build_traveler_trip_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Основна клавіатура керування поїздкою для мандрівника.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Чекін: Все гаразд", callback_data=f"trv_checkin:{trip_id}")
    builder.button(text="💬 Чекін з приміткою", callback_data=f"trv_note:{trip_id}")
    builder.button(text="🔄 Оновити статус", callback_data=f"trv_refresh:{trip_id}")
    builder.button(text="🏁 Прибули (Закрити поїздку)", callback_data=f"trv_close_ask:{trip_id}")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def build_close_confirm_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Клавіатура підтвердження закриття поїздки (захист від випадкового кліку).'''
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Так, ми прибули", callback_data=f"trv_close_confirm:{trip_id}")
    builder.button(text="⬅️ Назад до поїздки", callback_data=f"trv_refresh:{trip_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_cancel_note_keyboard(trip_id: int) -> InlineKeyboardMarkup:
    '''Кнопка скасування введення текстової примітки.'''
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Скасувати", callback_data=f"trv_refresh:{trip_id}")
    return builder.as_markup()
