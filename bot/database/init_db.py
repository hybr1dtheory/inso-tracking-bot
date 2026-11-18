import aiosqlite
import logging
from config import ADMIN_TG_ID, ADMIN_FULL_NAME


logger = logging.getLogger(__name__)

async def create_tables(db_path: str):
    '''
    Створює необхідні таблиці в базі даних SQLite, якщо вони ще не існують.
    '''
    # SQL-запити для створення таблиць
    CREATE_USERS_TABLE = '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        full_name TEXT NOT NULL,
        phone TEXT,
        role TEXT DEFAULT 'TRAVELER', -- 'TRAVELER', 'OPERATOR', 'ADMIN'
        is_on_shift INTEGER DEFAULT 0, -- 0 = False, 1 = True
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    '''
    CREATE_TRIPS_TABLE = '''
    CREATE TABLE IF NOT EXISTS trips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        route_code TEXT NOT NULL,
        origin TEXT NOT NULL,
        destination TEXT NOT NULL,
        status TEXT DEFAULT 'PLANNED', -- 'PLANNED', 'ACTIVE', 'COMPLETED', 'CANCELLED'
        
        checkin_interval_min INTEGER DEFAULT 120,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ended_at TIMESTAMP,
        
        last_checkin_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_checkin_by INTEGER REFERENCES users(id),
        
        operator_reminded_at TIMESTAMP,
        pending_request_sent_at TIMESTAMP,
        pending_request_by INTEGER REFERENCES users(id)
    );
    '''
    CREATE_TRIP_PARTICIPANTS_TABLE = '''
    CREATE TABLE IF NOT EXISTS trip_participants (
        trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        is_leader INTEGER DEFAULT 0, -- 0 = False, 1 = True (Старший групи)
        PRIMARY KEY (trip_id, user_id)
    );
    '''
    CREATE_CHECKIN_EVENTS_TABLE = '''
    CREATE TABLE IF NOT EXISTS checkin_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
        actor_user_id INTEGER REFERENCES users(id),
        event_type TEXT NOT NULL, -- 'TRIP_STARTED', 'SELF_CHECKIN', 'OPERATOR_PING_SENT', 'REQUESTED_CHECKIN', 'TRIP_CLOSED'
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    '''

    try:
        # Підключаємось до бази (якщо файлу немає, aiosqlite створить його автоматично)
        async with aiosqlite.connect(db_path) as db:
            # Вмикаємо підтримку зовнішніх ключів (Foreign Keys)
            await db.execute("PRAGMA foreign_keys = ON;")
            # Виконуємо запити
            await db.execute(CREATE_USERS_TABLE)
            await db.execute(CREATE_TRIPS_TABLE)
            await db.execute(CREATE_TRIP_PARTICIPANTS_TABLE)
            await db.execute(CREATE_CHECKIN_EVENTS_TABLE)
            # Створюємо індекси для прискорення пошуку прострочених поїздок ворКером
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trips_active ON trips(status);")
            # Додаємо першого адміна
            if ADMIN_TG_ID:
                await db.execute(
                    """
                    INSERT INTO users (tg_id, full_name, role)
                    VALUES (?, ?, 'ADMIN')
                    ON CONFLICT(tg_id) DO UPDATE SET role = 'ADMIN';
                    """,
                    (ADMIN_TG_ID, ADMIN_FULL_NAME)
                )
                logger.info(f"🛡 Головного адміністратора (tg_id={ADMIN_TG_ID}) синхронізовано.")
            else:
                logger.warning("⚠️ ADMIN_TG_ID не вказано в .env! Першого адміна не створено.")
            # Зберігаємо зміни
            await db.commit()
            logger.info("✅ База даних SQLite успішно ініціалізована.")
    except Exception as e:
        logger.error(f"❌ Помилка при створенні бази даних: {e}")
        raise
