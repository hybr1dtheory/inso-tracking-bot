import logging
from pathlib import Path
import aiosqlite
from config import ADMIN_TG_ID, ADMIN_FULL_NAME


logger = logging.getLogger(__name__)


async def create_tables(db_path: str):
    """
    Створює необхідні таблиці в базі даних SQLite, якщо вони ще не існують.
    """
    # SQL-запити для створення таблиць
    CREATE_USERS_TABLE = """
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
    """
    CREATE_TRIPS_TABLE = """
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
        last_checkin_by INTEGER REFERENCES users(id) ON DELETE SET NULL,

        operator_reminded_at TIMESTAMP,
        pending_request_sent_at TIMESTAMP,
        pending_request_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    );
    """
    CREATE_TRIP_PARTICIPANTS_TABLE = """
    CREATE TABLE IF NOT EXISTS trip_participants (
        trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
        is_leader INTEGER DEFAULT 0, -- 0 = False, 1 = True (Старший групи)
        PRIMARY KEY (trip_id, user_id)
    );
    """
    CREATE_CHECKIN_EVENTS_TABLE = """
    CREATE TABLE IF NOT EXISTS checkin_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trip_id INTEGER REFERENCES trips(id) ON DELETE CASCADE,
        actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        event_type TEXT NOT NULL, -- 'TRIP_PLANNED', 'TRIP_STARTED', 'SELF_CHECKIN', 'OPERATOR_PING_SENT', 'REQUESTED_CHECKIN', 'HELP_REQUESTED', 'INTERVAL_CHANGED', 'TRIP_CLOSED', 'TRIP_CANCELLED'
        note TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """
    try:
        # Гарантуємо існування батьківської директорії перед створенням файлу БД
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(db_path) as db:
            # Вмикаємо WAL-режим (зменшує блокування при паралельній роботі бота й Watchdog) та зовнішні ключі
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA foreign_keys = ON;")
            # Виконуємо запити створення таблиць
            await db.execute(CREATE_USERS_TABLE)
            await db.execute(CREATE_TRIPS_TABLE)
            await db.execute(CREATE_TRIP_PARTICIPANTS_TABLE)
            await db.execute(CREATE_CHECKIN_EVENTS_TABLE)
            # Індекси для швидкого пошуку воркером, дашбордом оператора та карткою мандрівника
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trips_status ON trips(status);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_trip_participants_user ON trip_participants(user_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_checkin_events_trip ON checkin_events(trip_id);")
            # Додаємо або синхронізуємо головного адміністратора з .env
            if ADMIN_TG_ID:
                await db.execute(
                    """
                    INSERT INTO users (tg_id, full_name, role)
                    VALUES (?, ?, 'ADMIN')
                    ON CONFLICT(tg_id) DO UPDATE SET role = 'ADMIN';
                    """,
                    (ADMIN_TG_ID, ADMIN_FULL_NAME)
                )
                logger.info(f"Main administrator (tg_id={ADMIN_TG_ID}) synchronized.")
            else:
                logger.warning("ADMIN_TG_ID is not specified in .env! Initial admin was not created.")
            await db.commit()
            logger.info("SQLite database successfully initialized.")
    except Exception as e:
        logger.error(f"Error while initializing the database: {e}")
        raise
