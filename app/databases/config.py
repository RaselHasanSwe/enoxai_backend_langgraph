import sqlite3
from app.config import get_settings

settings = get_settings()

DB_PATH = settings.chat_store_path

def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn