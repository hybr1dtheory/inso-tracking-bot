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


async def create_trip(
    db_path: str, origin: str, destination: str,
    participant_ids: list[int], interval_min: int, operator_db_id: int
) -> int | None:
    '''
    Атомарно створює поїздку, прив'язує екіпаж та пише подію старту в аудит-лог.
    Повертає ID створеної поїздки або None у разі помилки.
    '''
    route_code = f"{origin} → {destination}"

    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON;")

            # 1. Створюємо запис поїздки
            cursor = await db.execute(
                """
                INSERT INTO trips (route_code, origin, destination, status, checkin_interval_min, last_checkin_by)
                VALUES (?, ?, ?, 'ACTIVE', ?, ?)
                """,
                (route_code, origin, destination, interval_min, operator_db_id)
            )
            trip_id = cursor.lastrowid
            # 2. Додаємо учасників (перший обраний автоматично стає старшим групи)
            participants_data = [
                (trip_id, user_id, 1 if idx == 0 else 0)
                for idx, user_id in enumerate(participant_ids)
            ]
            await db.executemany(
                "INSERT INTO trip_participants (trip_id, user_id, is_leader) VALUES (?, ?, ?)",
                participants_data
            )
            # 3. Фіксуємо старт в аудит-лозі
            await db.execute(
                """
                INSERT INTO checkin_events (trip_id, actor_user_id, event_type, note)
                VALUES (?, ?, 'TRIP_STARTED', ?)
                """,
                (trip_id, operator_db_id, f"Інтервал чекіну: {interval_min} хв")
            )
            # Підтверджуємо всі 3 операції разом
            await db.commit()
            return trip_id

    except Exception as e:
        logger.error(f"Помилка при створенні поїздки {route_code}: {e}")
        return None


async def get_trip_details(db_path: str, trip_id: int) -> dict | None:
    '''
    Повертає повну інформацію про поїздку, включно зі списком екіпажу
    та кількістю хвилин, що минули з останнього чекіну.
    '''
    trip_query = '''
    SELECT 
        t.id, t.route_code, t.origin, t.destination, t.status,
        t.checkin_interval_min, t.started_at, t.last_checkin_at,
        t.pending_request_sent_at,
        u.full_name AS last_checkin_user_name,
        CAST((julianday('now') - julianday(t.last_checkin_at)) * 1440 AS INTEGER) AS minutes_ago
    FROM trips t
    LEFT JOIN users u ON t.last_checkin_by = u.id
    WHERE t.id = ?
    '''
    crew_query = '''
    SELECT u.id, u.tg_id, u.full_name, tp.is_leader
    FROM trip_participants tp
    JOIN users u ON tp.user_id = u.id
    WHERE tp.trip_id = ?
    ORDER BY tp.is_leader DESC, u.full_name ASC
    '''

    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(trip_query, (trip_id,)) as cursor:
                trip_row = await cursor.fetchone()
                if not trip_row:
                    return None
                trip = dict(trip_row)
            async with db.execute(crew_query, (trip_id,)) as cursor:
                crew_rows = await cursor.fetchall()
                trip["crew"] = [dict(r) for r in crew_rows]
            return trip
    except Exception as e:
        logger.error(f"Помилка отримання деталей поїздки #{trip_id}: {e}")
        return None


async def get_user_active_trips(db_path: str, user_id: int) -> list[dict]:
    '''Повертає всі активні поїздки, до яких залучений конкретний користувач.'''
    query = '''
    SELECT t.id
    FROM trips t
    JOIN trip_participants tp ON t.id = tp.trip_id
    WHERE tp.user_id = ? AND t.status = 'ACTIVE'
    ORDER BY t.started_at DESC
    '''
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(query, (user_id,)) as cursor:
                rows = await cursor.fetchall()
                trip_ids = [row[0] for row in rows]
        trips = []
        for tid in trip_ids:
            details = await get_trip_details(db_path, tid)
            if details:
                trips.append(details)
        return trips
    except Exception as e:
        logger.error(f"Помилка отримання активних поїздок користувача {user_id}: {e}")
        return []


async def record_checkin(
    db_path: str, trip_id: int, actor_user_id: int, note: str | None = None
) -> str | None:
    '''
    Фіксує чекін для поїздки, скидає прапорці очікування запиту від оператора
    та записує подію в checkin_events.
    Повертає тип події ('SELF_CHECKIN' або 'REQUESTED_CHECKIN') або None, якщо поїздка вже закрита.
    '''
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON;")
            db.row_factory = aiosqlite.Row
            # Перевіряємо, чи поїздка активна і чи висів запит від оператора
            async with db.execute(
                "SELECT status, pending_request_sent_at FROM trips WHERE id = ?",
                (trip_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or row["status"] != "ACTIVE":
                    return None
                event_type = "REQUESTED_CHECKIN" if row["pending_request_sent_at"] else "SELF_CHECKIN"
            # Оновлюємо таймер поїздки та очищаємо нагадування/пінги
            await db.execute(
                '''
                UPDATE trips
                SET last_checkin_at = CURRENT_TIMESTAMP,
                    last_checkin_by = ?,
                    operator_reminded_at = NULL,
                    pending_request_sent_at = NULL,
                    pending_request_by = NULL
                WHERE id = ?
                ''',
                (actor_user_id, trip_id)
            )
            # Додаємо запис в аудит-лог
            await db.execute(
                '''INSERT INTO checkin_events (trip_id, actor_user_id, event_type, note)
                VALUES (?, ?, ?, ?)''',
                (trip_id, actor_user_id, event_type, note)
            )
            await db.commit()
            return event_type
    except Exception as e:
        logger.error(f"Помилка запису чекіну для поїздки #{trip_id}: {e}")
        return None


async def close_trip(db_path: str, trip_id: int, actor_user_id: int) -> bool:
    '''Переводить активну поїздку в статус COMPLETED та фіксує подію в логах.'''
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON;")
            cursor = await db.execute(
                '''UPDATE trips
                SET status = 'COMPLETED',
                    ended_at = CURRENT_TIMESTAMP,
                    last_checkin_at = CURRENT_TIMESTAMP,
                    last_checkin_by = ?,
                    operator_reminded_at = NULL,
                    pending_request_sent_at = NULL,
                    pending_request_by = NULL
                WHERE id = ? AND status = 'ACTIVE' ''',
                (actor_user_id, trip_id)
            )
            if cursor.rowcount == 0:
                return False
            await db.execute(
                '''
                INSERT INTO checkin_events (trip_id, actor_user_id, event_type, note)
                VALUES (?, ?, 'TRIP_CLOSED', 'Екіпаж прибув у пункт призначення')
                ''',
                (trip_id, actor_user_id)
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Помилка закриття поїздки #{trip_id}: {e}")
        return False


async def get_operators_tg_ids(db_path: str) -> list[int]:
    '''Повертає Telegram ID усіх операторів та адмінів (використовується, якщо не задано груповий чат).'''
    query = "SELECT tg_id FROM users WHERE role IN ('OPERATOR', 'ADMIN')"
    try:
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [r[0] for r in rows]
    except Exception as e:
        logger.error(f"Помилка отримання списку операторів: {e}")
        return []
