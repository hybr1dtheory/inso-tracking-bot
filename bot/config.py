import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


# Завантажуємо змінні з файлу .env у пам'ять
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Якщо DB_PATH не вказано в .env, беремо шлях за замовчуванням
DB_PATH = os.getenv("DB_PATH", "data/journey_db.sqlite3")
MONITORING_GROUP_ID = os.getenv("MONITORING_GROUP_ID")

CHECKIN_INTERVAL_MIN = 120
KYIV_TZ = ZoneInfo("Europe/Kyiv")
UTC_TZ = ZoneInfo("UTC")

# Налаштування першого адміністратора з .env
_raw_admin_id = os.getenv("ADMIN_TG_ID", "").strip()
ADMIN_TG_ID: int | None = int(_raw_admin_id) if _raw_admin_id.isdigit() else None
ADMIN_FULL_NAME: str = os.getenv("ADMIN_FULL_NAME", "Адміністратор").strip()
