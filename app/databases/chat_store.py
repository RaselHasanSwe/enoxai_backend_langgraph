from datetime import datetime
from app.databases.config import get_connection
import logging
import math
from uuid import uuid4
import random

logger = logging.getLogger(__name__)


# ---------------------------
# CREATE TABLE (run once at startup)
# ---------------------------
def init_db():
    conn = None
    try:
        conn = get_connection()

        # Enable foreign keys in SQLite
        conn.execute("PRAGMA foreign_keys = ON")

        # Users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                session_id TEXT UNIQUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Chat messages table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
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
# SAVE or GET USER
# ---------------------------


def get_welcome_message(user_name: str) -> str:
    messages = [
        f"Hi **{user_name}**! I'm **EnoX**, your assistant from **PFD Enorsia UK LTD**. How may I help you today?",
        f"Welcome back, **{user_name}**! I'm **EnoX** from **PFD Enorsia UK LTD**. Got a question about your order or anything else? I'm here!",
        f"Hey **{user_name}**! Great to have you here. I'm **EnoX**, PFD Enorsia's virtual assistant. What can I do for you today?",
        f"Hello **{user_name}**! I'm **EnoX** from **PFD Enorsia UK LTD**. Whether it's orders, returns, or products — I've got you covered. What do you need?",
        f"Hi there, **{user_name}**! I'm **EnoX**, your dedicated assistant at **PFD Enorsia UK LTD**. How can I assist you today?",
    ]
    return random.choice(messages)


def get_or_create_user(name: str, email: str) -> dict:
    conn = None
    needs_greeting = False

    try:
        conn = get_connection()

        # Check existing user
        cursor = conn.execute(
            """
            SELECT id, name, email, session_id
            FROM users
            WHERE email = ?
            """,
            (email,)
        )

        row = cursor.fetchone()

        if row:
            logger.info(
                "DATABASE | existing user found | email=%s user_id=%s",
                email,
                row["id"]
            )
            user_id = row["id"]

            # Check last message timestamp
            last_msg_cursor = conn.execute(
                """
                SELECT timestamp
                FROM chat_messages
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,)
            )

            last_msg = last_msg_cursor.fetchone()

            if last_msg is None:
                # Existing user but no messages yet
                needs_greeting = True
            else:
                last_time = datetime.fromisoformat(str(last_msg["timestamp"]))
                hours_since = (datetime.utcnow() - last_time).total_seconds() / 3600
                needs_greeting = hours_since >= 24

            if needs_greeting:
                welcome_message = get_welcome_message(name)
                save_message(row["session_id"], "ai", welcome_message)

            logger.info(
                "DATABASE | existing user found | email=%s user_id=%s needs_greeting=%s",
                email, row["id"], needs_greeting
            )

            return {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "session_id": row["session_id"]
            }

        # Generate session ID
        session_id = str(uuid4())
        needs_greeting = True

        # Create user
        cursor = conn.execute(
            """
            INSERT INTO users (name, email, session_id)
            VALUES (?, ?, ?)
            """,
            (name, email, session_id)
        )

        conn.commit()

        user_id = cursor.lastrowid

        if needs_greeting:
            welcome_message = get_welcome_message(name)
            save_message(session_id, "ai", welcome_message)

        logger.info(
            "DATABASE | new user created | user_id=%s email=%s",
            user_id,
            email
        )

        return {
            "id": user_id,
            "name": name,
            "email": email,
            "session_id": session_id
        }

    except Exception:
        logger.exception(
            "DATABASE | get_or_create_user failed | email=%s",
            email
        )
        raise

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

        # Find user by session_id
        cursor = conn.execute(
            """
            SELECT id
            FROM users
            WHERE session_id = ?
            """,
            (session_id,)
        )

        row = cursor.fetchone()

        if not row:
            logger.error(
                "DATABASE | user not found | session_id=%s",
                session_id
            )
            return False

        user_id = row["id"]

        # Save message
        conn.execute(
            """
            INSERT INTO chat_messages (
                user_id,
                role,
                message,
                timestamp
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                user_id,
                role,
                message,
                datetime.utcnow()
            )
        )

        conn.commit()

        logger.info(
            "DATABASE | message saved | user_id=%s role=%s length=%s",
            user_id,
            role,
            len(message)
        )

        return True

    except Exception:
        logger.exception(
            "DATABASE | save message failed | session_id=%s role=%s",
            session_id,
            role
        )
        raise

    finally:
        if conn:
            conn.close()


# ---------------------------
# GET HISTORY
# ---------------------------
PAGE_SIZE = 20

def get_history(user_id: int, page: int = 1):
    conn = None

    try:
        conn = get_connection()

        # ------------------------
        # 1. Get user
        # ------------------------
        user_cursor = conn.execute(
            """
            SELECT id, name, email, session_id
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        )

        user = user_cursor.fetchone()

        if not user:
            logger.warning(
                "DATABASE | user not found | user_id=%s",
                user_id
            )

            return {
                "user": None,
                "data": [],
                "pagination": {
                    "total_items": 0,
                    "total_pages": 0,
                    "current_page": page,
                    "page_size": PAGE_SIZE,
                }
            }

        # ------------------------
        # 2. Count total messages
        # ------------------------
        count_cursor = conn.execute(
            """
            SELECT COUNT(*) as total
            FROM chat_messages
            WHERE user_id = ?
            """,
            (user_id,)
        )

        total_items = count_cursor.fetchone()["total"]
        total_pages = max(1, math.ceil(total_items / PAGE_SIZE))

        # ------------------------
        # 3. Pagination calculation
        # ------------------------
        offset = (page - 1) * PAGE_SIZE

        # ------------------------
        # 4. Fetch messages
        # ------------------------
        cursor = conn.execute(
            """
            SELECT role, message, timestamp
            FROM chat_messages
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, PAGE_SIZE, offset)
        )

        rows = cursor.fetchall()

        logger.info(
            "DATABASE | history fetched | user_id=%s page=%s count=%s",
            user_id,
            page,
            len(rows)
        )

        return {
            "user": {
                "id": user["id"],
                "name": user["name"],
                "email": user["email"],
                "session_id": user["session_id"]
            },
            "data": [
                {
                    "role": row["role"],
                    "message": row["message"],
                    "timestamp": row["timestamp"]
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
        logger.exception(
            "DATABASE | get history failed | user_id=%s",
            user_id
        )

        return {
            "user": None,
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