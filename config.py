import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
ML_CLIENT_ID: str = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET: str = os.getenv("ML_CLIENT_SECRET", "")
DEFAULT_CHECK_INTERVAL: int = int(os.getenv("DEFAULT_CHECK_INTERVAL", "6"))
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./ml_tracker.db")
