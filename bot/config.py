import os
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


# Корінь проєкту (на рівень вище від папки bot/)
BASE_DIR = Path(__file__).resolve().parent.parent
# Завантажуємо змінні з файлу .env у пам'ять
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in .env file!")

# Прив'язуємо шлях до БД відносно кореня проєкту, щоб уникнути проблем із робочою директорією
_raw_db_path = os.getenv("DB_PATH", "data/journey_db.sqlite3").strip()
_db_path_obj = Path(_raw_db_path)
DB_PATH = str(_db_path_obj if _db_path_obj.is_absolute() else BASE_DIR / _db_path_obj)

# Безпечний парсинг ID групи моніторингу (підтримує від'ємні ID супергруп, напр. -100...)
_raw_group_id = os.getenv("MONITORING_GROUP_ID", "").strip()
try:
    MONITORING_GROUP_ID: int | None = int(_raw_group_id) if _raw_group_id else None
except ValueError:
    raise RuntimeError(f"Invalid MONITORING_GROUP_ID in .env: '{_raw_group_id}'")

# Інтервал чекіну за замовчуванням та доступні опції вибору (0 = Start / Finish only)
CHECKIN_INTERVAL_MIN = 120
CHECKIN_INTERVAL_OPTIONS = (
    (30, "🔴 Every 30 min (High risk)"),
    (60, "🟠 Every 60 min (Elevated risk)"),
    (120, "🟢 Every 120 min (Standard)"),
    (0, "⚪️ Start / Finish only"),
)

KYIV_TZ = ZoneInfo("Europe/Kyiv")
UTC_TZ = ZoneInfo("UTC")

# Налаштування першого адміністратора з .env
_raw_admin_id = os.getenv("ADMIN_TG_ID", "").strip()
ADMIN_TG_ID: int | None = int(_raw_admin_id) if _raw_admin_id.isdigit() else None
ADMIN_FULL_NAME: str = os.getenv("ADMIN_FULL_NAME", "").strip() or "Administrator"
