import os
import sqlite3
import uuid
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from app.services.auth.user_storage import DB_PATH

logger = logging.getLogger(__name__)


class MemoryService:
    """
    Teacher Pedagogical Memory Management Service.
    Stores and manages personalized teacher profile and memory items persistently in SQLite.
    Provides memory retrieval and prompt augmentation for Multi-Agent execution.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # 1. User pedagogical profiles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL DEFAULT '高中数学',
                    grade TEXT NOT NULL DEFAULT '高二理科',
                    textbook_version TEXT NOT NULL DEFAULT '人教A版',
                    student_analysis TEXT NOT NULL,
                    teaching_style TEXT NOT NULL,
                    auto_memory_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """)

            # 2. User pedagogical memory items table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'pedagogy',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 3,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_user ON user_memories(user_id)")
            conn.commit()

        # Seed demo memory for u-001 (teacher_demo)
        self._seed_demo_memories()

    def _seed_demo_memories(self):
        demo_user_id = "u-001"
        now = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM user_profiles WHERE user_id = ?", (demo_user_id,))
            if not cursor.fetchone():
                cursor.execute("""
                    INSERT INTO user_profiles (
                        user_id, subject, grade, textbook_version, student_analysis,
                        teaching_style, auto_memory_enabled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """, (
                    demo_user_id,
                    "高中数学",
                    "高二理科实验班",
                    "人教A版",
                    "班级学生代数变形与基本初等函数求导熟练，但对抽象函数极值点偏移、含参分类讨论的临界点判定较为薄弱，需要循序渐进铺设思维脚手架",
                    "启发式问题链教学法，强调数形结合、先割线后切线的几何直观，板书规范，注重易错点变式反思",
                    now
                ))

            # Seed 4 initial high-value memory items
            cursor.execute("SELECT COUNT(*) as cnt FROM user_memories WHERE user_id = ?", (demo_user_id,))
            cnt = cursor.fetchone()["cnt"]
            if cnt == 0:
                demo_items = [
                    (
                        "mem-01",
                        demo_user_id,
                        "pedagogy",
                        "导数教学强制采用割线逼近切线引入",
                        "在进行《导数的几何意义》及求导法则教学时，强制采用割线割向切线的极限逼近动画与数形结合直观引入，禁止一上课直接罗列死记硬背公式，必须引导学生体会以直代曲的思想。",
                        5,
                        1,
                        now,
                        now
                    ),
                    (
                        "mem-02",
                        demo_user_id,
                        "preference",
                        "教案与公开课板书三栏分区设计规范",
                        "公开课教案必须严格规划三分区板书：左侧为主板书（核心定义定理与求导公式）、中间为典型例题分步严密推导、右侧为副板书（学生草稿作图、易错陷阱与思维反思）。",
                        4,
                        1,
                        now,
                        now
                    ),
                    (
                        "mem-03",
                        demo_user_id,
                        "preference",
                        "命题组卷压轴题必须包含含参分类讨论与梯度踩分点",
                        "命制高二期中期末数学试卷时，解答题压轴题第二小问必须设置含参数 $a$ 的分类讨论（如 $a>0, a=0, a<0$ 分区间极值讨论），且必须附带按步骤给分的详尽评分量规细则。",
                        4,
                        1,
                        now,
                        now
                    ),
                    (
                        "mem-04",
                        demo_user_id,
                        "student_status",
                        "学生对自然对数复合函数单调性存在普遍盲区",
                        "高二3班学生在上周测试中反映出普遍问题：对含 $\\ln x$ 的复合函数在定义域 $(0, +\\infty)$ 边界处的极限趋势缺乏敏锐度，求导后容易漏掉定义域限制，生成教案或例题时应重点强化此易错点变式。",
                        5,
                        1,
                        now,
                        now
                    )
                ]
                cursor.executemany("""
                    INSERT INTO user_memories (
                        id, user_id, category, title, content, importance, is_active, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, demo_items)
            conn.commit()

    def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                data = dict(row)
                data["auto_memory_enabled"] = bool(data["auto_memory_enabled"])
                return data

            # Return default profile for new user
            now = datetime.now(timezone.utc).isoformat()
            default_profile = {
                "user_id": user_id,
                "subject": "高中数学",
                "grade": "高二",
                "textbook_version": "人教A版",
                "student_analysis": "班级学生运算基础较好，但抽象综合题逻辑转化能力有待提高",
                "teaching_style": "启发式教学，注重逻辑推导与板书条理清晰",
                "auto_memory_enabled": True,
                "updated_at": now
            }
            cursor.execute("""
                INSERT INTO user_profiles (
                    user_id, subject, grade, textbook_version, student_analysis,
                    teaching_style, auto_memory_enabled, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """, (
                user_id,
                default_profile["subject"],
                default_profile["grade"],
                default_profile["textbook_version"],
                default_profile["student_analysis"],
                default_profile["teaching_style"],
                now
            ))
            conn.commit()
            return default_profile

    def update_user_profile(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        profile = self.get_user_profile(user_id)
        now = datetime.now(timezone.utc).isoformat()

        subject = updates.get("subject", profile["subject"])
        grade = updates.get("grade", profile["grade"])
        textbook_version = updates.get("textbook_version", profile["textbook_version"])
        student_analysis = updates.get("student_analysis", profile["student_analysis"])
        teaching_style = updates.get("teaching_style", profile["teaching_style"])
        auto_memory_enabled = 1 if updates.get("auto_memory_enabled", profile["auto_memory_enabled"]) else 0

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE user_profiles
                SET subject = ?, grade = ?, textbook_version = ?, student_analysis = ?,
                    teaching_style = ?, auto_memory_enabled = ?, updated_at = ?
                WHERE user_id = ?
            """, (
                subject, grade, textbook_version, student_analysis,
                teaching_style, auto_memory_enabled, now, user_id
            ))
            conn.commit()

        return self.get_user_profile(user_id)

    def get_memory_items(
        self,
        user_id: str,
        active_only: bool = False,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM user_memories WHERE user_id = ?"
            params = [user_id]

            if active_only:
                query += " AND is_active = 1"
            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY importance DESC, updated_at DESC"
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                item["is_active"] = bool(item["is_active"])
                results.append(item)
            return results

    def add_memory_item(
        self,
        user_id: str,
        title: str,
        content: str,
        category: str = "pedagogy",
        importance: int = 3,
        is_active: bool = True
    ) -> Dict[str, Any]:
        item_id = f"mem-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO user_memories (
                    id, user_id, category, title, content, importance, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item_id,
                user_id,
                category,
                title,
                content,
                importance,
                1 if is_active else 0,
                now,
                now
            ))
            conn.commit()

        return {
            "id": item_id,
            "user_id": user_id,
            "category": category,
            "title": title,
            "content": content,
            "importance": importance,
            "is_active": is_active,
            "created_at": now,
            "updated_at": now
        }

    def update_memory_item(
        self,
        user_id: str,
        item_id: str,
        updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_memories WHERE id = ? AND user_id = ?", (item_id, user_id))
            row = cursor.fetchone()
            if not row:
                return None

            current = dict(row)
            category = updates.get("category", current["category"])
            title = updates.get("title", current["title"])
            content = updates.get("content", current["content"])
            importance = updates.get("importance", current["importance"])
            is_active = current["is_active"]
            if "is_active" in updates and updates["is_active"] is not None:
                is_active = 1 if updates["is_active"] else 0

            now = datetime.now(timezone.utc).isoformat()

            cursor.execute("""
                UPDATE user_memories
                SET category = ?, title = ?, content = ?, importance = ?, is_active = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
            """, (category, title, content, importance, is_active, now, item_id, user_id))
            conn.commit()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_memories WHERE id = ?", (item_id,))
            updated_row = dict(cursor.fetchone())
            updated_row["is_active"] = bool(updated_row["is_active"])
            return updated_row

    def delete_memory_item(self, user_id: str, item_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_memories WHERE id = ? AND user_id = ?", (item_id, user_id))
            deleted = cursor.rowcount > 0
            conn.commit()
            return deleted

    def clear_memory_items(self, user_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM user_memories WHERE user_id = ?", (user_id,))
            deleted = cursor.rowcount
            conn.commit()
            return deleted

    def build_memory_prompt(self, user_id: str) -> str:
        """
        Builds the personalization and memory injection prompt block for Multi-Agent LLMs.
        """
        profile = self.get_user_profile(user_id)
        active_memories = self.get_memory_items(user_id, active_only=True)

        memory_lines = []
        for mem in active_memories:
            stars = "★" * mem["importance"]
            memory_lines.append(f"- [{mem['title']}] ({stars}): {mem['content']}")

        memories_text = "\n".join(memory_lines) if memory_lines else "暂无已激活的偏好条目。"

        prompt = f"""
## 【当前授课教师专属教学记忆与画像约束】
- **授课教师画像**：学科【{profile['subject']}】 · 学段【{profile['grade']}】 · 教材版本【{profile['textbook_version']}】
- **班级学情诊断**：{profile['student_analysis']}
- **常驻教学风格**：{profile['teaching_style']}
- **教师专属生效教学记忆 ({len(active_memories)} 条)**：
{memories_text}

**执行硬性约束**：在生成教学设计、命制测试试题、组织课堂问答或进行数理公式推导时，请严格贴合上述教师的学科习惯、班级学情薄弱点与板书结构要求！
"""
        return prompt.strip()

    def reflect_memories_from_dialogue(self, user_id: str, dialogues: List[str]) -> Dict[str, Any]:
        """
        Analyzes dialogue messages to discover potential pedagogical preferences and habits.
        """
        reflected = []
        text_corpus = "\n".join(dialogues)

        # Rule-based and pattern discovery heuristic
        if "板书" in text_corpus or "布局" in text_corpus:
            reflected.append({
                "title": "板书与排版偏好",
                "category": "preference",
                "content": "教师高度关注课堂板书布局，偏好主副板书分离与图文穿插展示。",
                "importance": 4,
                "suggested_action": "add"
            })

        if "学生" in text_corpus and ("错" in text_corpus or "薄弱" in text_corpus or "难点" in text_corpus):
            reflected.append({
                "title": "关注易错点与分层台阶",
                "category": "student_status",
                "content": "教师注重针对学生易错点设计变式反思训练，授课时倾向于多搭设过渡台阶。",
                "importance": 4,
                "suggested_action": "add"
            })

        if "压轴题" in text_corpus or "导数" in text_corpus or "评分" in text_corpus:
            reflected.append({
                "title": "试卷命题规范化",
                "category": "pedagogy",
                "content": "命题要求有清晰的采分点细则，强调考查数学核心素养与数形结合思想。",
                "importance": 3,
                "suggested_action": "add"
            })

        if not reflected:
            reflected.append({
                "title": "启发式互动倾向",
                "category": "pedagogy",
                "content": "教学对话偏好通过问题链由浅入深启发，而非单纯给出直接结论。",
                "importance": 3,
                "suggested_action": "add"
            })

        return {
            "reflected_items": reflected,
            "summary": f"基于近期与智能体的 {len(dialogues)} 轮对话分析，成功提炼出 {len(reflected)} 项教师潜在教学习惯偏好。"
        }


memory_service = MemoryService()
