import os
from dotenv import load_dotenv


# Завантажуємо змінні з файлу .env у пам'ять
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Якщо DB_PATH не вказано в .env, беремо шлях за замовчуванням
DB_PATH = os.getenv("DB_PATH", "data/journey_db.sqlite3")
MONITORING_GROUP_ID = os.getenv("MONITORING_GROUP_ID")
