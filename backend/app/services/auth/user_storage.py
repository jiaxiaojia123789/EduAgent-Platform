import os
import sqlite3
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from app.core.security import get_password_hash, verify_password

logger = logging.getLogger(__name__)

# Base directory for backend data
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "data")
DB_PATH = os.path.join(DATA_DIR, "edu_platform.db")


class UserStorage:
    """
    Thread-safe SQLite persistent user storage.
    Guarantees user persistence across restarts without requiring external PostgreSQL.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    hashed_password TEXT NOT NULL,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'teacher',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

        # Seed default accounts if empty
        self._seed_default_users()

    def _seed_default_users(self):
        default_users = [
            {
                "id": "u-001",
                "username": "teacher_demo",
                "email": "teacher@edu.ai",
                "password": "password123",
                "full_name": "张老师 (高级教师)",
                "role": "teacher"
            },
            {
                "id": "u-000",
                "username": "admin",
                "email": "admin@edu.ai",
                "password": "admin123",
                "full_name": "系统管理员",
                "role": "admin"
            }
        ]

        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).isoformat()
            for u in default_users:
                cursor.execute("SELECT id FROM users WHERE username = ?", (u["username"],))
                if not cursor.fetchone():
                    cursor.execute("""
                        INSERT INTO users (id, username, email, hashed_password, full_name, role, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """, (
                        u["id"],
                        u["username"],
                        u["email"],
                        get_password_hash(u["password"]),
                        u["full_name"],
                        u["role"],
                        now,
                        now
                    ))
            conn.commit()
            logger.info("[UserStorage] Initialized SQLite database and verified default users.")

    def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            row = cursor.fetchone()
            if row:
                return dict(row)
        return None

    def create_user(
        self,
        username: str,
        email: str,
        password: str,
        full_name: Optional[str] = None,
        role: str = "teacher",
        custom_id: Optional[str] = None
    ) -> Dict[str, Any]:
        user_id = custom_id or f"u-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        now = datetime.now(timezone.utc).isoformat()
        hashed = get_password_hash(password)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (id, username, email, hashed_password, full_name, role, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (
                user_id,
                username,
                email,
                hashed,
                full_name or username,
                role,
                now,
                now
            ))
            conn.commit()

        return {
            "id": user_id,
            "username": username,
            "email": email,
            "full_name": full_name or username,
            "role": role,
            "is_active": True,
            "created_at": now
        }

    def verify_credentials(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        user = self.get_by_username(username)
        if not user:
            return None
        if not verify_password(password, user["hashed_password"]):
            return None
        return user

    def list_users(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, email, full_name, role, is_active, created_at FROM users")
            return [dict(row) for row in cursor.fetchall()]


user_storage = UserStorage()
