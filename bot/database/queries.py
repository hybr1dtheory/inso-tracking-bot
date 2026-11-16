import aiosqlite
import logging


logger = logging.getLogger(__name__)

async def get_user_by_tg_id(db_path: str, tg_id: int) -> dict | None:
    '''
    Повертає профіль користувача з бази за його Telegram ID.
    Якщо користувач не знайдений, повертає None.
    '''
    query = '''
    SELECT id, tg_id, username, full_name, phone, role, is_on_shift 
    FROM users 
    WHERE tg_id = ?
    '''
    
    try:
        async with aiosqlite.connect(db_path) as db:
            # Налаштовуємо row_factory, щоб результати повертались у вигляді словника (dict-like object)
            db.row_factory = aiosqlite.Row 
            async with db.execute(query, (tg_id,)) as cursor:
                user = await cursor.fetchone()
                if user:
                    return dict(user) # Перетворюємо aiosqlite.Row на звичайний dict
                return None
    except Exception as e:
        logger.error(f"Помилка при пошуку користувача {tg_id}: {e}")
        return None


async def add_new_user(
    db_path: str, tg_id: int, full_name: str, 
    username: str | None = None, role: str = 'TRAVELER'
) -> dict | None:
    '''
    Універсальна функція для додавання користувача в БД.
    Повертає профіль створеного користувача, або None у разі помилки/дублікату.
    '''
    query = '''
    INSERT INTO users (tg_id, username, full_name, role) 
    VALUES (?, ?, ?, ?)
    '''
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(query, (tg_id, username, full_name, role))
            await db.commit()
            return await get_user_by_tg_id(db_path, tg_id)        
    except aiosqlite.IntegrityError:
        logger.warning(f"Спроба додати користувача {tg_id}, який вже існує.")
        return None
    except Exception as e:
        logger.error(f"Помилка при додаванні користувача {tg_id}: {e}")
        return None


async def get_all_users(db_path: str) -> list[dict]:
    '''Повертає список усіх зареєстрованих користувачів.'''
    query = "SELECT id, tg_id, full_name, role FROM users ORDER BY id ASC"
    
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query) as cursor:
                users = await cursor.fetchall()
                return [dict(user) for user in users]
    except Exception as e:
        logger.error(f"Помилка отримання списку користувачів: {e}")
        return []


async def update_user_role(db_path: str, user_id: int, new_role: str) -> bool:
    '''Оновлює роль користувача за його внутрішнім id в базі.'''
    query = "UPDATE users SET role = ? WHERE id = ?"
    
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(query, (new_role, user_id))
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Помилка оновлення ролі користувача {user_id}: {e}")
        return False
