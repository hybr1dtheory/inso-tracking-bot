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
        full_name TEXT NOT NULL,
        team TEXT,
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
            # Авто-міграція існуючої таблиці users (додаємо team, видаляємо username)
            async with db.execute("PRAGMA table_info(users);") as cursor:
                existing_cols = {row[1] for row in await cursor.fetchall()}
            if "team" not in existing_cols:
                await db.execute("ALTER TABLE users ADD COLUMN team TEXT;")
                logger.info("🔄 Міграція БД: додано колонку 'team' у таблицю users.")
            if "username" in existing_cols:
                await db.execute("ALTER TABLE users DROP COLUMN username;")
                logger.info("🔄 Міграція БД: видалено колонку 'username' із таблиці users.")
            await db.execute(CREATE_TRIPS_TABLE)
            await db.execute(CREATE_TRIP_PARTICIPANTS_TABLE)
            await db.execute(CREATE_CHECKIN_EVENTS_TABLE)
            # Створюємо індекси для прискорення пошуку прострочених поїздок воркером
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
                logger.info(f"🛡 The main administrator (tg_id={ADMIN_TG_ID}) has been synchronized.")
            else:
                logger.warning("⚠️ ADMIN_TG_ID is not specified in .env! The first admin has not been created.")
            # Зберігаємо зміни
            await db.commit()
            logger.info("✅ The SQLite database has been successfully initialized.")
    except Exception as e:
        logger.error(f"❌ Error while creating the database: {e}")
        raise
