"""
历史对话持久化存储（原生 SQLite）
与 UserStorage 共用 backend/data/edu_platform.db，不依赖外部 PostgreSQL。

数据组织：
- chat_sessions：一条对话会话，归属一个 agent（agent_type 实现按智能体隔离）
- chat_messages：会话内的每条消息，extra JSON 保存 plan_dag/artifact 等扩展数据
"""
import json
import os
import sqlite3
import uuid
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 复用 UserStorage 的数据目录与数据库路径
DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "data"
)
DB_PATH = os.path.join(DATA_DIR, "edu_platform.db")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStorage:
    """历史对话的 CRUD 存储"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '新对话',
                    agent_type TEXT NOT NULL DEFAULT 'supervisor',
                    thread_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    thought_process TEXT,
                    citations TEXT,
                    extra TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_agent
                    ON chat_sessions(user_id, agent_type);
                CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                    ON chat_messages(session_id);
            """)
            conn.commit()

    # ==================== Session ====================

    def create_session(
        self,
        user_id: str,
        agent_type: str,
        title: str = "新对话",
        session_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        session_id = session_id or str(uuid.uuid4())
        thread_id = thread_id or str(uuid.uuid4())
        now = _now_iso()
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO chat_sessions (id, user_id, title, agent_type, thread_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, user_id, title, agent_type, thread_id, now, now)
            )
            conn.commit()
        return {
            "id": session_id, "user_id": user_id, "title": title,
            "agent_type": agent_type, "thread_id": thread_id,
            "created_at": now, "updated_at": now,
        }

    def touch_session(self, session_id: str, title: Optional[str] = None):
        """更新会话的 updated_at（历史排序依据），可选更新标题"""
        with self._get_connection() as conn:
            if title:
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ?, title = ? WHERE id = ?",
                    (_now_iso(), title, session_id)
                )
            else:
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                    (_now_iso(), session_id)
                )
            conn.commit()

    def auto_title_if_needed(self, session_id: str, content: str) -> bool:
        """首条用户消息时自动生成标题（取前 20 字），返回是否更新"""
        title = (content or "").strip().replace("\n", " ")[:20]
        if not title:
            return False
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT title FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row and (row["title"] in (None, "", "新对话")):
                conn.execute(
                    "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (title, _now_iso(), session_id)
                )
                conn.commit()
                return True
        return False

    def list_sessions(
        self, user_id: str, agent_type: str
    ) -> List[Dict[str, Any]]:
        """按 agent 列出历史会话（含消息数），最近更新在前"""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT s.*, COUNT(m.id) AS message_count
                FROM chat_sessions s
                LEFT JOIN chat_messages m ON m.session_id = s.id
                WHERE s.user_id = ? AND s.agent_type = ?
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                """,
                (user_id, agent_type)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_session(self, session_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            conn.commit()
            return cur.rowcount > 0

    # ==================== Message ====================

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        thought_process: Optional[str] = None,
        citations: Optional[List[Dict[str, Any]]] = None,
        extra: Optional[Dict[str, Any]] = None,
        message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        message_id = message_id or str(uuid.uuid4())
        now = _now_iso()
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO chat_messages
                   (id, session_id, role, content, thought_process, citations, extra, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id, session_id, role, content,
                    thought_process,
                    json.dumps(citations or [], ensure_ascii=False),
                    json.dumps(extra or {}, ensure_ascii=False),
                    now,
                )
            )
            conn.commit()
        self.touch_session(session_id)
        return {"id": message_id, "session_id": session_id, "role": role, "created_at": now}

    def list_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,)
            ).fetchall()
            messages = []
            for r in rows:
                d = dict(r)
                d["citations"] = self._safe_json(d.get("citations"), [])
                d["extra"] = self._safe_json(d.get("extra"), {})
                messages.append(d)
            return messages

    @staticmethod
    def _safe_json(raw: Optional[str], default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default


# 全局单例
conversation_storage = ConversationStorage()
