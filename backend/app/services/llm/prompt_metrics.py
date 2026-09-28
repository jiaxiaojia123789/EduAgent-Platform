"""
Prompt 迭代与评估数据层 (Prompt Iteration & Evaluation)
========================================================
记录每次「结构化输出调用」的运行指标，为 Prompt 版本迭代提供数据支撑：

- prompt_versions 表：注册表快照（prompt_id / version / 模板哈希），识别线上生效版本
- prompt_call_metrics 表：每次结构化调用的解析成功率、重试次数、API JSON 模式、
  模型档位、耗时 —— 衡量某个 Prompt 版本「可控性与格式一致性」的量化依据

存储遵循项目既有约定：独立 SQLite 文件（与 user_storage / memory_service 一致），
全部接口 best-effort：写失败只记日志，绝不阻塞推理主链路。
"""
import os
import json
import sqlite3
import hashlib
import logging
import threading
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from app.prompts.registry import prompt_registry

logger = logging.getLogger(__name__)

# 与 user_storage.DB_PATH 同目录（backend/data）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(_BASE_DIR, "data", "prompt_metrics.db")


class PromptMetricsService:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        self.sync_registry_snapshot()

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prompt_versions (
                        prompt_id    TEXT PRIMARY KEY,
                        version      TEXT NOT NULL,
                        role_key     TEXT,
                        template_md5 TEXT NOT NULL,
                        updated_at   TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prompt_call_metrics (
                        id             INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts             TEXT NOT NULL,
                        prompt_id      TEXT,
                        version        TEXT,
                        schema_name    TEXT,
                        agent          TEXT,
                        model          TEXT,
                        parse_ok       INTEGER NOT NULL,
                        attempts       INTEGER NOT NULL,
                        retried        INTEGER NOT NULL,
                        api_json_mode  INTEGER NOT NULL,
                        latency_ms     REAL,
                        session_id     TEXT,
                        user_id       TEXT,
                        error         TEXT
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_metrics_prompt_ts
                    ON prompt_call_metrics(prompt_id, ts)
                """)
                # P3-15: 增加 token usage 列（幂等迁移：检测列缺失则补加）
                self._ensure_columns(conn, [
                    ("prompt_tokens",  "INTEGER NOT NULL DEFAULT 0"),
                    ("completion_tokens","INTEGER NOT NULL DEFAULT 0"),
                    ("total_tokens",    "INTEGER NOT NULL DEFAULT 0"),
                    ("cached",          "INTEGER NOT NULL DEFAULT 0"),
                ])
                conn.commit()

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, columns: list) -> None:
        """幂等加列：检测列不存在则 ALTER TABLE 补加。"""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(prompt_call_metrics)")}
        for col_name, col_def in columns:
            if col_name not in existing:
                conn.execute(
                    f"ALTER TABLE prompt_call_metrics ADD COLUMN {col_name} {col_def}"
                )

    # ------------------------------------------------------------------
    def sync_registry_snapshot(self) -> None:
        """把注册表当前版本快照落库（每次进程启动时对账，识别线上生效版本）"""
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                with self._connect() as conn:
                    for pid, info in prompt_registry.describe().items():
                        spec = prompt_registry.get(pid)
                        md5 = hashlib.md5(spec.template.encode("utf-8")).hexdigest()
                        conn.execute("""
                            INSERT INTO prompt_versions (prompt_id, version, role_key, template_md5, updated_at)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(prompt_id) DO UPDATE SET
                                version=excluded.version,
                                role_key=excluded.role_key,
                                template_md5=excluded.template_md5,
                                updated_at=excluded.updated_at
                        """, (pid, info["version"], info["role"], md5, now))
        except Exception as e:
            logger.warning(f"[PromptMetrics] 注册表快照同步失败: {e}")

    # ------------------------------------------------------------------
    def record_call(
        self,
        prompt_id: Optional[str],
        schema_name: str,
        parse_ok: bool,
        attempts: int,
        retried: bool,
        api_json_mode: bool,
        model: Optional[str],
        latency_ms: float,
        agent: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        error: Optional[str] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached: bool = False,
    ) -> None:
        """记录一次结构化输出调用（best-effort，失败不影响主流程）。"""
        try:
            version: Optional[str] = None
            try:
                if prompt_id:
                    version = prompt_registry.get(prompt_id).version
            except KeyError:
                pass
            with self._lock:
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO prompt_call_metrics (
                            ts, prompt_id, version, schema_name, agent, model,
                            parse_ok, attempts, retried, api_json_mode,
                            latency_ms, session_id, user_id, error,
                            prompt_tokens, completion_tokens, total_tokens, cached
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        datetime.now(timezone.utc).isoformat(),
                        prompt_id, version, schema_name, agent, model,
                        int(parse_ok), int(attempts), int(retried), int(api_json_mode),
                        round(latency_ms, 1), session_id, user_id,
                        (error or "")[:500] or None,
                        int(prompt_tokens), int(completion_tokens), int(total_tokens), int(cached),
                    ))
        except Exception as e:
            logger.warning(f"[PromptMetrics] 指标写入失败: {e}")

    # ------------------------------------------------------------------
    def stats(self, prompt_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """聚合指标：总调用数、解析成功率、平均尝试次数、平均耗时（按 prompt 维度）"""
        where, params = "", []
        if prompt_id:
            where = "WHERE prompt_id = ?"
            params = [prompt_id]
        with self._connect() as conn:
            rows = conn.execute(f"""
                SELECT prompt_id,
                       COUNT(*)                       AS total_calls,
                       AVG(parse_ok)                  AS parse_success_rate,
                       AVG(attempts)                  AS avg_attempts,
                       SUM(retried)                   AS retry_count,
                       AVG(latency_ms)                AS avg_latency_ms,
                       SUM(prompt_tokens)             AS total_prompt_tokens,
                       SUM(completion_tokens)         AS total_completion_tokens,
                       SUM(total_tokens)              AS total_tokens,
                       SUM(cached)                    AS cached_calls,
                       AVG(latency_ms)                AS avg_latency_ms
                FROM prompt_call_metrics {where}
                GROUP BY prompt_id
                ORDER BY total_calls DESC
            """, params).fetchall()
            recent = conn.execute(f"""
                SELECT ts, prompt_id, schema_name, agent, model, parse_ok,
                       attempts, api_json_mode, latency_ms, error,
                       prompt_tokens, completion_tokens, total_tokens, cached
                FROM prompt_call_metrics {where}
                ORDER BY id DESC LIMIT ?
            """, (*params, limit)).fetchall()

        def _row(r: sqlite3.Row) -> Dict[str, Any]:
            d = dict(r)
            for k in ("parse_success_rate", "avg_attempts", "avg_latency_ms"):
                if d.get(k) is not None:
                    d[k] = round(float(d[k]), 4)
            return d

        return {
            "summary": [_row(r) for r in rows],
            "recent": [dict(r) for r in recent],
        }

    # ------------------------------------------------------------------
    def agent_stats(self, since_hours: int = 24) -> List[Dict[str, Any]]:
        """
        P3-15: 按 agent 维度聚合最近 N 小时的指标：
        - 调用数、缓存命中率、错误率
        - p50/p95 延迟（用 NTILE 简化实现）
        - token 总成本
        供 /metrics Prometheus 端点 + 看板使用。
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    COALESCE(agent, 'unknown')              AS agent,
                    COUNT(*)                                 AS total_calls,
                    SUM(cached)                              AS cached_calls,
                    SUM(CASE WHEN parse_ok = 0 THEN 1 ELSE 0 END) AS failed_calls,
                    AVG(latency_ms)                         AS avg_latency_ms,
                    SUM(total_tokens)                       AS total_tokens,
                    SUM(prompt_tokens)                      AS prompt_tokens,
                    SUM(completion_tokens)                  AS completion_tokens
                FROM prompt_call_metrics
                WHERE ts >= datetime('now', ?)
                GROUP BY agent
                ORDER BY total_calls DESC
                """,
                (f"-{since_hours} hours",),
            ).fetchall()

            # p50/p95 用窗口函数（SQLite 3.25+ 支持）
            p_rows = conn.execute(
                """
                WITH ranked AS (
                    SELECT agent, latency_ms,
                           ROW_NUMBER() OVER (PARTITION BY agent ORDER BY latency_ms) AS rn,
                           COUNT(*)   OVER (PARTITION BY agent)                       AS cnt
                    FROM prompt_call_metrics
                    WHERE ts >= datetime('now', ?) AND latency_ms IS NOT NULL
                )
                SELECT agent,
                       AVG(CASE WHEN rn <= cnt * 0.5  THEN latency_ms END) AS p50,
                       AVG(CASE WHEN rn <= cnt * 0.95 THEN latency_ms END) AS p95
                FROM ranked
                GROUP BY agent
                """,
                (f"-{since_hours} hours",),
            ).fetchall()

        p_map = {r["agent"]: (r["p50"], r["p95"]) for r in p_rows}
        result: List[Dict[str, Any]] = []
        for r in rows:
            agent = r["agent"]
            p50, p95 = p_map.get(agent, (None, None))
            total = r["total_calls"] or 0
            cached = r["cached_calls"] or 0
            failed = r["failed_calls"] or 0
            result.append({
                "agent": agent,
                "total_calls": total,
                "cached_calls": cached,
                "cache_hit_rate": round(cached / total, 4) if total else 0.0,
                "failed_calls": failed,
                "error_rate": round(failed / total, 4) if total else 0.0,
                "avg_latency_ms": round(r["avg_latency_ms"], 1) if r["avg_latency_ms"] else 0.0,
                "p50_latency_ms": round(p50, 1) if p50 else None,
                "p95_latency_ms": round(p95, 1) if p95 else None,
                "total_tokens": r["total_tokens"] or 0,
                "prompt_tokens": r["prompt_tokens"] or 0,
                "completion_tokens": r["completion_tokens"] or 0,
            })
        return result


prompt_metrics = PromptMetricsService()
