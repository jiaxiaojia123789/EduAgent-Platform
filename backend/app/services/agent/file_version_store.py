"""
FileVersionStore — 文件版本快照（MVCC）

为每个文件维护不可变版本链：
- reader 在任务开始时 pin 住当前版本号，读取该版本快照，
  writer 提交新版本不影响在途 reader（读不阻塞、读写并行）
- writer 基于 base_version 提交：
    base_version == 最新版本 → 直接提交
    base_version < 最新版本 且变更区域与后续版本重叠 → FileConflictError，
      由调度器 rebase（只重跑冲突任务节点）
    区域不重叠 → 提交成功

存储：与用户/会话共用 backend/data/edu_platform.db（原生 sqlite3）。
"""
import os
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.services.auth.user_storage import DB_PATH, DATA_DIR
from app.services.agent.file_resource_manager import WHOLE_REGION, regions_overlap

logger = logging.getLogger(__name__)


class FileConflictError(RuntimeError):
    """提交时发现 base 版本落后且变更区域冲突，需要 rebase。"""

    def __init__(self, file_id: str, current_version: int, conflicting: List[int]):
        super().__init__(
            f"文件 '{file_id}' 提交冲突：base 版本落后，当前 v{current_version}，"
            f"冲突版本 {conflicting}，请 rebase 后重试"
        )
        self.file_id = file_id
        self.current_version = current_version
        self.conflicting_versions = conflicting


def _region_to_text(region: Tuple[int, int]) -> str:
    return f"{region[0]},{region[1]}"


def _text_to_region(text: str) -> Tuple[int, int]:
    s, e = text.split(",")
    return int(s), int(e)


class FileVersionStore:
    """文件版本存储（SQLite）。"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT,
                    changed_region TEXT NOT NULL DEFAULT '-1,-1',
                    created_by TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(file_id, version)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_versions_file ON file_versions(file_id, version)"
            )

    # ------------------------------------------------------------------
    # 初始化 / 查询
    # ------------------------------------------------------------------
    def init_file(
        self, file_id: str, content: str, created_by: str = "system", message: str = "初始版本"
    ) -> int:
        """文件首次入库：建立 v1；已存在则返回当前最新版本号。"""
        latest = self.latest_version(file_id)
        if latest is not None:
            return latest
        return self.commit(file_id, content, created_by, base_version=0,
                          changed_region=WHOLE_REGION, message=message)

    def latest_version(self, file_id: str) -> Optional[int]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM file_versions WHERE file_id = ?", (file_id,)
            ).fetchone()
            return int(row["v"]) if row and row["v"] is not None else None

    def get_snapshot(self, file_id: str, version: Optional[int] = None) -> Dict[str, Any]:
        """读取快照：version=None → 最新版本。返回 {version, content, created_at, created_by}。"""
        with self._get_connection() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM file_versions WHERE file_id = ? "
                    "ORDER BY version DESC LIMIT 1", (file_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM file_versions WHERE file_id = ? AND version = ?",
                    (file_id, version),
                ).fetchone()
            if not row:
                raise KeyError(f"文件 '{file_id}' 不存在版本 {version or 'latest'}")
            return {
                "file_id": file_id,
                "version": row["version"],
                "content": row["content"],
                "changed_region": _text_to_region(row["changed_region"]),
                "created_by": row["created_by"],
                "message": row["message"],
                "created_at": row["created_at"],
            }

    def list_versions(self, file_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT version, changed_region, created_by, message, created_at "
                "FROM file_versions WHERE file_id = ? ORDER BY version", (file_id,)
            ).fetchall()
            return [
                {
                    "version": r["version"],
                    "changed_region": _text_to_region(r["changed_region"]),
                    "created_by": r["created_by"],
                    "message": r["message"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # 提交（含冲突检测）
    # ------------------------------------------------------------------
    def commit(
        self,
        file_id: str,
        content: str,
        created_by: str,
        base_version: int,
        changed_region: Tuple[int, int] = WHOLE_REGION,
        message: str = "",
    ) -> int:
        """
        提交新版本，返回新版本号。
        base_version: writer 开始工作时 pin 的版本（0 表示新文件）。
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS v FROM file_versions WHERE file_id = ?", (file_id,)
            ).fetchone()
            current = int(row["v"]) if row and row["v"] is not None else 0

            if base_version > current:
                raise RuntimeError(
                    f"base_version {base_version} 大于当前版本 v{current}，数据异常"
                )

            if base_version < current:
                # 检查 base 之后的版本变更区域是否与本次重叠
                newer = conn.execute(
                    "SELECT version, changed_region FROM file_versions "
                    "WHERE file_id = ? AND version > ? ORDER BY version",
                    (file_id, base_version),
                ).fetchall()
                conflicts = [
                    int(r["version"])
                    for r in newer
                    if regions_overlap(changed_region, _text_to_region(r["changed_region"]))
                ]
                if conflicts:
                    raise FileConflictError(file_id, current, conflicts)

            new_version = current + 1
            conn.execute(
                "INSERT INTO file_versions "
                "(file_id, version, content, changed_region, created_by, message, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_id, new_version, content, _region_to_text(changed_region),
                 created_by, message, now),
            )
            logger.info(
                f"[FileVersionStore] '{file_id}' v{new_version} 提交成功 "
                f"(base v{base_version}, region={changed_region}, by={created_by})"
            )
            return new_version


file_version_store = FileVersionStore()
