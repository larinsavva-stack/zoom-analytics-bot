import os
from dotenv import load_dotenv

load_dotenv()

RECALL_API_KEY = os.environ.get("RECALL_API_KEY", "")

# Базовый URL Recall.ai API
RECALL_BASE_URL = "https://us-west-2.recall.ai/api/v1"

# Имя бота в Zoom встрече
BOT_NAME = os.environ.get("BOT_NAME", "Nechto Zoom Analytics")

# Путь к SQLite базе данных
DB_PATH = os.environ.get("DB_PATH", "analytics.db")

# Порт FastAPI сервера
SERVER_PORT = int(os.environ.get("PORT", 8000))

WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "")

MATERIALS_DIR = os.environ.get(
    "MATERIALS_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "materials_files"),
)
