from datetime import datetime
from app.databases.config import get_connection
import logging
import math

logger = logging.getLogger(__name__)


# ---------------------------
# CREATE TABLE (run once at startup)
# ---------------------------
def init_db():
    conn = None
    try:
        conn = get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                message TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        logger.info("DATABASE | initialized successfully")
    except Exception:
        logger.exception("DATABASE | initialization failed")
    finally:
        if conn:
            conn.close()


# ---------------------------
# SAVE MESSAGE
# ---------------------------
def save_message(session_id: str, role: str, message: str):
    conn = None
    try:
        conn = get_connection()

        conn.execute(
            """
            INSERT INTO chat_messages (session_id, role, message, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, message, datetime.utcnow())
        )

        conn.commit()

        logger.info(
            "DATABASE | message saved | session=%s role=%s length=%s",
            session_id, role, len(message)
        )

    except Exception:
        logger.exception(
            "DATABASE | save message failed | session=%s role=%s",
            session_id, role
        )

    finally:
        if conn:
            conn.close()


# ---------------------------
# GET HISTORY
# ---------------------------
PAGE_SIZE = 20

def get_history(session_id: str, page: int = 1):
    conn = None
    try:
        conn = get_connection()

        # ------------------------
        # 1. Count total messages
        # ------------------------
        count_cursor = conn.execute(
            """
            SELECT COUNT(*) as total
            FROM chat_messages
            WHERE session_id = ?
            """,
            (session_id,)
        )
        total_items = count_cursor.fetchone()["total"]

        total_pages = math.ceil(total_items / PAGE_SIZE) if total_items else 1

        # ------------------------
        # 2. Pagination calculation
        # ------------------------
        offset = (page - 1) * PAGE_SIZE

        # ------------------------
        # 3. Fetch paginated data
        # ------------------------
        cursor = conn.execute(
            """
            SELECT role, message, timestamp
            FROM chat_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (session_id, PAGE_SIZE, offset)
        )

        rows = cursor.fetchall()

        logger.info(
            "DATABASE | history fetched | session=%s page=%s count=%s",
            session_id, page, len(rows)
        )

        return {
            "data": [
                {
                    "role": row["role"],
                    "message": row["message"],
                    "timestamp": row["timestamp"],
                }
                for row in rows
            ],
            "pagination": {
                "total_items": total_items,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": PAGE_SIZE,
            }
        }

    except Exception:
        logger.exception("DATABASE | get history failed | session=%s", session_id)
        return {
            "data": [],
            "pagination": {
                "total_items": 0,
                "total_pages": 0,
                "current_page": page,
                "page_size": PAGE_SIZE,
            }
        }

    finally:
        if conn:
            conn.close()