"""
RAG 文档分块策略集

提供 6 种分块策略，支持按文档类型自动选择：
1. semantic        - 语义结构分块（保护公式/表格，heading 面包屑）
2. fixed           - 固定长度滑窗分块
3. markdown_header - 按 Markdown 标题层级分块
4. qa_pair         - 问答对分块（识别 Q:/A: 或 问题:/答案:）
5. recursive       - 递归字符分块（\n\n → \n → 。 → 空格）
6. sentence        - 句子级分块（2-5 句/块）

分块大小限制：128 - 1024 tokens，超出则拒绝并抛出 ValueError。
"""
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)

# 分块大小允许范围
MIN_CHUNK_SIZE = 128
MAX_CHUNK_SIZE = 1024
DEFAULT_OVERLAP_RATIO = 0.125  # 12.5%


class ChunkStrategy(str, Enum):
    SEMANTIC = "semantic"
    FIXED = "fixed"
    MARKDOWN_HEADER = "markdown_header"
    QA_PAIR = "qa_pair"
    RECURSIVE = "recursive"
    SENTENCE = "sentence"
    AUTO = "auto"


# 按文档类型自动映射策略与默认参数
AUTO_STRATEGY_MAP: Dict[str, Dict[str, Any]] = {
    "PDF": {"strategy": "semantic", "chunk_size": 512, "overlap": 64},
    "WORD": {"strategy": "semantic", "chunk_size": 384, "overlap": 48},
    "MARKDOWN": {"strategy": "markdown_header", "chunk_size": 512, "overlap": 64},
    "TXT": {"strategy": "recursive", "chunk_size": 384, "overlap": 48},
}


def validate_chunk_size(chunk_size: int) -> int:
    """校验分块大小在 128-1024 之间，超出则抛出 ValueError"""
    if not isinstance(chunk_size, int) or chunk_size < MIN_CHUNK_SIZE or chunk_size > MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size 必须在 {MIN_CHUNK_SIZE}-{MAX_CHUNK_SIZE} 之间，当前值: {chunk_size}"
        )
    return chunk_size


def validate_overlap(overlap: int, chunk_size: int) -> int:
    """校验重叠长度不超过分块大小的 50%"""
    if overlap < 0:
        raise ValueError(f"chunk_overlap 不能为负数，当前值: {overlap}")
    if overlap > chunk_size // 2:
        raise ValueError(
            f"chunk_overlap ({overlap}) 不能超过 chunk_size ({chunk_size}) 的 50%"
        )
    return overlap


def estimate_tokens(text: str) -> int:
    """启发式 token 计数：1 中文 ≈ 1 token，1 英文词 ≈ 1.3 token"""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z0-9_]+", text))
    return chinese_chars + int(english_words * 1.3) + 5


def auto_detect_strategy(doc_type: str) -> Dict[str, Any]:
    """根据文档类型自动选择分块策略与参数"""
    doc_type_upper = (doc_type or "").upper()
    for key in AUTO_STRATEGY_MAP:
        if key in doc_type_upper:
            return AUTO_STRATEGY_MAP[key]
    # 默认回退到 semantic
    return {"strategy": "semantic", "chunk_size": 512, "overlap": 64}


class BaseChunker:
    """分块器基类"""

    def __init__(self, chunk_size: int = 512, overlap: int = 64):
        self.chunk_size = validate_chunk_size(chunk_size)
        self.overlap = validate_overlap(overlap, chunk_size)

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _build_chunk(
        self,
        content: str,
        doc_id: str,
        kb_id: str,
        chunk_index: int,
        page_num: int = 1,
        section_path: Optional[List[str]] = None,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        default_metadata = default_metadata or {}
        section_path = section_path or []
        content = content.strip()
        formula_count = len(re.findall(r"\$[^$]+\$", content)) + content.count("$$") // 2
        # 兼容 |---| 和 | --- | 两种表格分隔线写法
        table_count = len(re.findall(r"\|\s*-{2,}\s*\|", content))
        tokens = estimate_tokens(content)

        metadata = dict(default_metadata)
        metadata.update({
            "section_path": " > ".join(section_path) if section_path else "引言与概述",
            "page_number": page_num,
            "has_formula": formula_count > 0,
            "has_table": table_count > 0,
            "chunk_strategy": self.__class__.__name__,
        })

        return {
            "doc_id": doc_id,
            "kb_id": kb_id,
            "chunk_index": chunk_index,
            "content": content,
            "tokens": tokens,
            "formula_count": formula_count,
            "table_count": table_count,
            "page_number": page_num,
            "metadata": metadata,
        }


class SemanticFormulaSafeChunker(BaseChunker):
    """
    语义结构分块（默认策略）
    - 保护 $$...$$ 数学块和 Markdown 表格不被切断
    - 跟踪 heading 面包屑，写入 metadata
    """

    def __init__(self, chunk_size: int = 512, overlap: int = 64):
        super().__init__(chunk_size, overlap)

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        default_metadata = default_metadata or {}
        atomic_blocks = self._split_into_atomic_blocks(markdown_text)

        chunks = []
        current_blocks = []
        current_tokens = 0
        section_path: List[str] = []
        current_page = 1

        for block in atomic_blocks:
            if block["type"] == "page_marker":
                current_page = block["page"]
                continue

            if block["type"] == "heading":
                level = block["level"]
                title = block["text"]
                section_path = section_path[: level - 1]
                section_path.append(title)

            block_tokens = estimate_tokens(block["text"])

            if current_tokens + block_tokens > self.chunk_size and current_blocks:
                chunks.append(self._build_chunk(
                    "\n\n".join(b["text"] for b in current_blocks if b["text"].strip()),
                    doc_id, kb_id, len(chunks), current_page, section_path, default_metadata,
                ))
                last_block = current_blocks[-1]
                last_tokens = estimate_tokens(last_block["text"])
                if last_tokens <= self.overlap:
                    current_blocks = [last_block, block]
                    current_tokens = last_tokens + block_tokens
                else:
                    current_blocks = [block]
                    current_tokens = block_tokens
            else:
                current_blocks.append(block)
                current_tokens += block_tokens

        if current_blocks:
            chunks.append(self._build_chunk(
                "\n\n".join(b["text"] for b in current_blocks if b["text"].strip()),
                doc_id, kb_id, len(chunks), current_page, section_path, default_metadata,
            ))

        return chunks

    def _split_into_atomic_blocks(self, text: str) -> List[Dict[str, Any]]:
        blocks = []
        lines = text.split("\n")
        i, n = 0, len(lines)

        while i < n:
            line = lines[i]

            page_match = re.match(r"<!-- Page (\d+) -->", line)
            if page_match:
                blocks.append({"type": "page_marker", "page": int(page_match.group(1)), "text": ""})
                i += 1
                continue

            heading_match = re.match(r"^(#+)\s+(.+)$", line)
            if heading_match:
                blocks.append({"type": "heading", "level": len(heading_match.group(1)), "text": line.strip()})
                i += 1
                continue

            if line.strip().startswith("$$"):
                math_lines = [line]
                i += 1
                while i < n and not lines[i].strip().endswith("$$"):
                    math_lines.append(lines[i])
                    i += 1
                if i < n:
                    math_lines.append(lines[i])
                    i += 1
                blocks.append({"type": "math_block", "text": "\n".join(math_lines)})
                continue

            if line.strip().startswith("|") and i + 1 < n and (
                "|---" in lines[i + 1] or "| ---" in lines[i + 1] or "|:---" in lines[i + 1]
            ):
                table_lines = [line]
                i += 1
                while i < n and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i])
                    i += 1
                blocks.append({"type": "table_block", "text": "\n".join(table_lines)})
                continue

            para_lines = []
            while (
                i < n
                and lines[i].strip()
                and not lines[i].startswith("#")
                and not lines[i].strip().startswith("$$")
                and not lines[i].strip().startswith("|")
            ):
                para_lines.append(lines[i])
                i += 1
            if para_lines:
                blocks.append({"type": "paragraph", "text": " ".join(para_lines)})
            else:
                i += 1

        return blocks


class FixedSizeChunker(BaseChunker):
    """
    固定长度滑窗分块
    - 按 token 数硬切，无结构感知
    - overlap 从末尾截取指定 token 数作为下一块开头
    """

    def __init__(self, chunk_size: int = 384, overlap: int = 48):
        super().__init__(chunk_size, overlap)

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        default_metadata = default_metadata or {}
        text = markdown_text.strip()
        if not text:
            return []

        # 按字符近似切分（中文 1 字 ≈ 1 token）
        chunks = []
        start = 0
        text_len = len(text)
        prev_end = 0

        while start < text_len:
            end = min(start + self.chunk_size, text_len)
            # 尽量在空格/换行处断开，避免切断单词
            if end < text_len:
                # 向前找最近的分隔符
                search_region = text[start + self.chunk_size // 2 : end]
                sep_positions = [
                    search_region.rfind("\n"),
                    search_region.rfind("。"),
                    search_region.rfind(". "),
                    search_region.rfind(" "),
                ]
                max_sep = max(sep_positions)
                if max_sep > 0:
                    end = start + self.chunk_size // 2 + max_sep + 1

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(self._build_chunk(
                    chunk_text, doc_id, kb_id, len(chunks),
                    default_metadata=default_metadata,
                ))

            # 下一块起点：end - overlap（滑窗）
            start = end - self.overlap
            # 确保推进：若新起点未超过上次终点，直接跳到上次终点避免死循环
            if start <= prev_end:
                start = prev_end
            prev_end = end
            if start >= text_len:
                break

        return chunks


class MarkdownHeaderChunker(BaseChunker):
    """
    按 Markdown 标题层级分块
    - 以 # / ## / ### 为边界切分
    - 同一 section 内内容合并，超 chunk_size 再用递归切分
    """

    HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    def __init__(self, chunk_size: int = 512, overlap: int = 64):
        super().__init__(chunk_size, overlap)

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        default_metadata = default_metadata or {}
        headings = list(self.HEADING_RE.finditer(markdown_text))

        if not headings:
            # 无标题，回退到递归分块
            return RecursiveChunker(self.chunk_size, self.overlap).chunk_document(
                markdown_text, doc_id, kb_id, default_metadata
            )

        chunks = []
        section_path: List[str] = []

        for idx, match in enumerate(headings):
            level = len(match.group(1))
            title = match.group(2).strip()
            section_path = section_path[: level - 1]
            section_path.append(title)

            content_start = match.end()
            content_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(markdown_text)
            section_content = markdown_text[content_start:content_end].strip()

            if not section_content:
                continue

            section_tokens = estimate_tokens(section_content)
            if section_tokens <= self.chunk_size:
                chunks.append(self._build_chunk(
                    section_content, doc_id, kb_id, len(chunks),
                    section_path=list(section_path),
                    default_metadata=default_metadata,
                ))
            else:
                # section 过大，递归切分
                sub_chunks = self._split_oversized_section(
                    section_content, section_path
                )
                for sub in sub_chunks:
                    chunks.append(self._build_chunk(
                        sub, doc_id, kb_id, len(chunks),
                        section_path=list(section_path),
                        default_metadata=default_metadata,
                    ))

        return chunks

    def _split_oversized_section(self, text: str, section_path: List[str]) -> List[str]:
        """对超大 section 做二次切分（按段落/句号）"""
        result = []
        parts = re.split(r"\n\n+", text)
        current = ""
        for part in parts:
            if estimate_tokens(current + "\n\n" + part) <= self.chunk_size:
                current = (current + "\n\n" + part).strip()
            else:
                if current:
                    result.append(current)
                # 单个段落仍超限，按句号切
                if estimate_tokens(part) > self.chunk_size:
                    sentences = re.split(r"(?<=[。！？!?])", part)
                    cur = ""
                    for s in sentences:
                        if estimate_tokens(cur + s) <= self.chunk_size:
                            cur += s
                        else:
                            if cur:
                                result.append(cur)
                            cur = s
                    if cur:
                        result.append(cur)
                else:
                    current = part
        if current:
            result.append(current)
        return result or [text]


class QAPairChunker(BaseChunker):
    """
    问答对分块
    - 识别 Q:/A: 或 问题:/答案: 或 1. 2. 编号题目
    - 每个问答独立成块
    """

    QA_PATTERNS = [
        re.compile(r"(?:^|\n)(Q[:：]|问题[:：]|题目[:：])\s*(.+?)(?=(?:^|\n)(A[:：]|答案[:：]|解答[:：])\s*)", re.DOTALL),
        re.compile(r"(?:^|\n)(A[:：]|答案[:：]|解答[:：])\s*(.+?)(?=(?:^|\n)(Q[:：]|问题[:：]|题目[:：])\s*|$)", re.DOTALL),
    ]
    NUMBERED_Q = re.compile(r"(?:^|\n)(\d+[.、）)])\s*(.+?)(?=(?:^|\n)\d+[.、）)]\s*|$)", re.DOTALL)

    def __init__(self, chunk_size: int = 128, overlap: int = 16):
        super().__init__(chunk_size, overlap)

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        default_metadata = default_metadata or {}
        pairs = self._extract_qa_pairs(markdown_text)

        if not pairs:
            # 未识别到问答对，回退到语义分块
            return SemanticFormulaSafeChunker(self.chunk_size, self.overlap).chunk_document(
                markdown_text, doc_id, kb_id, default_metadata
            )

        chunks = []
        for q, a in pairs:
            content = f"问题：{q}\n答案：{a}".strip()
            chunks.append(self._build_chunk(
                content, doc_id, kb_id, len(chunks),
                default_metadata=default_metadata,
            ))

        return chunks

    def _extract_qa_pairs(self, text: str) -> List[Tuple[str, str]]:
        pairs = []
        # 匹配 Q1:/Q:/问题:/题目: 各种变体（含数字编号）
        q_pattern = r"(?:^|\n)(?:Q\d*[:：]|问题[:：]|题目[:：])\s*"
        a_pattern = r"(?:^|\n)(?:A\d*[:：]|答案[:：]|解答[:：])\s*"

        q_matches = list(re.finditer(
            q_pattern + r"(.+?)(?=" + a_pattern + r")",
            text, re.DOTALL,
        ))
        a_matches = list(re.finditer(
            a_pattern + r"(.+?)(?=(?:" + q_pattern + r")|$)",
            text, re.DOTALL,
        ))

        if q_matches and a_matches:
            for i, qm in enumerate(q_matches):
                q = qm.group(1).strip()
                a = a_matches[i].group(1).strip() if i < len(a_matches) else ""
                pairs.append((q, a))
        else:
            # 编号题目模式
            num_matches = list(self.NUMBERED_Q.finditer(text))
            for m in num_matches:
                content = m.group(2).strip()
                # 尝试分离问题和答案（按"答案"关键词）
                ans_split = re.split(r"(?:答案[:：]|解答[:：])", content, maxsplit=1)
                if len(ans_split) == 2:
                    pairs.append((ans_split[0].strip(), ans_split[1].strip()))
                else:
                    pairs.append((content, ""))

        return pairs


class RecursiveChunker(BaseChunker):
    """
    递归字符分块
    - 按分隔符优先级递归切分：\n\n → \n → 。 → ； → ， → 空格
    - 超阈值时用更低优先级分隔符继续切
    """

    SEPARATORS = ["\n\n", "\n", "。", "；", ";", "，", ",", " ", ""]

    def __init__(self, chunk_size: int = 384, overlap: int = 48):
        super().__init__(chunk_size, overlap)

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        default_metadata = default_metadata or {}
        pieces = self._recursive_split(markdown_text.strip(), self.SEPARATORS)

        chunks = []
        current = ""
        for piece in pieces:
            candidate = (current + piece).strip()
            if estimate_tokens(candidate) <= self.chunk_size:
                current = candidate
            else:
                if current:
                    chunks.append(self._build_chunk(
                        current, doc_id, kb_id, len(chunks),
                        default_metadata=default_metadata,
                    ))
                # 单个 piece 仍超限，硬切
                if estimate_tokens(piece) > self.chunk_size:
                    for i in range(0, len(piece), self.chunk_size):
                        sub = piece[i : i + self.chunk_size]
                        if sub.strip():
                            chunks.append(self._build_chunk(
                                sub.strip(), doc_id, kb_id, len(chunks),
                                default_metadata=default_metadata,
                            ))
                    current = ""
                else:
                    current = piece

        if current:
            chunks.append(self._build_chunk(
                current, doc_id, kb_id, len(chunks),
                default_metadata=default_metadata,
            ))

        return chunks

    def _recursive_split(self, text: str, separators: List[str]) -> List[str]:
        if not separators:
            return [text] if text.strip() else []

        sep = separators[0]
        if not sep:
            return [text] if text.strip() else []

        if sep not in text:
            return self._recursive_split(text, separators[1:])

        parts = text.split(sep)
        result = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if estimate_tokens(part) <= self.chunk_size:
                result.append(part + (sep if sep != "\n\n" else "\n"))
            else:
                result.extend(self._recursive_split(part, separators[1:]))
        return result


class SentenceChunker(BaseChunker):
    """
    句子级分块
    - 按句号/问号/感叹号切分
    - 每块 2-5 句，适合精确检索
    """

    SENTENCE_END = re.compile(r"(?<=[。！？!?])")

    def __init__(self, chunk_size: int = 128, overlap: int = 16):
        super().__init__(chunk_size, overlap)
        self.min_sentences = 2
        self.max_sentences = 5

    def chunk_document(
        self,
        markdown_text: str,
        doc_id: str,
        kb_id: str,
        default_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        default_metadata = default_metadata or {}
        sentences = [s.strip() for s in self.SENTENCE_END.split(markdown_text) if s.strip()]

        if not sentences:
            return SemanticFormulaSafeChunker(self.chunk_size, self.overlap).chunk_document(
                markdown_text, doc_id, kb_id, default_metadata
            )

        chunks = []
        current_sentences: List[str] = []
        current_tokens = 0

        for sent in sentences:
            sent_tokens = estimate_tokens(sent)
            if (
                current_tokens + sent_tokens > self.chunk_size
                and len(current_sentences) >= self.min_sentences
            ):
                chunks.append(self._build_chunk(
                    "".join(current_sentences), doc_id, kb_id, len(chunks),
                    default_metadata=default_metadata,
                ))
                # overlap：保留最后一句
                if current_sentences and estimate_tokens(current_sentences[-1]) <= self.overlap:
                    current_sentences = [current_sentences[-1], sent]
                    current_tokens = estimate_tokens(current_sentences[-1]) + sent_tokens
                else:
                    current_sentences = [sent]
                    current_tokens = sent_tokens
            else:
                current_sentences.append(sent)
                current_tokens += sent_tokens
                # 达到最大句数强制切
                if len(current_sentences) >= self.max_sentences:
                    chunks.append(self._build_chunk(
                        "".join(current_sentences), doc_id, kb_id, len(chunks),
                        default_metadata=default_metadata,
                    ))
                    current_sentences = []
                    current_tokens = 0

        if current_sentences:
            chunks.append(self._build_chunk(
                "".join(current_sentences), doc_id, kb_id, len(chunks),
                default_metadata=default_metadata,
            ))

        return chunks


class ChunkerFactory:
    """分块器工厂：根据策略 ID 创建对应分块器"""

    _registry: Dict[str, type] = {
        ChunkStrategy.SEMANTIC.value: SemanticFormulaSafeChunker,
        ChunkStrategy.FIXED.value: FixedSizeChunker,
        ChunkStrategy.MARKDOWN_HEADER.value: MarkdownHeaderChunker,
        ChunkStrategy.QA_PAIR.value: QAPairChunker,
        ChunkStrategy.RECURSIVE.value: RecursiveChunker,
        ChunkStrategy.SENTENCE.value: SentenceChunker,
    }

    @classmethod
    def get_strategy(
        cls,
        strategy: str = "auto",
        chunk_size: int = 0,
        overlap: int = 0,
        doc_type: str = "",
    ) -> Tuple[BaseChunker, str]:
        """
        创建分块器实例
        Returns: (chunker_instance, resolved_strategy_id)
        """
        # auto 模式：按 doc_type 自动选择
        if strategy == "auto" or not strategy:
            auto_cfg = auto_detect_strategy(doc_type)
            strategy = auto_cfg["strategy"]
            if chunk_size == 0:
                chunk_size = auto_cfg["chunk_size"]
            if overlap == 0:
                overlap = auto_cfg["overlap"]

        if strategy not in cls._registry:
            raise ValueError(
                f"未知的分块策略: {strategy}，可选: {list(cls._registry.keys())} + auto"
            )

        # 参数为 0 时用策略默认值
        chunker_cls = cls._registry[strategy]
        if chunk_size == 0:
            chunk_size = 512
        if overlap == 0:
            overlap = max(16, int(chunk_size * DEFAULT_OVERLAP_RATIO))

        chunker = chunker_cls(chunk_size=chunk_size, overlap=overlap)
        return chunker, strategy

    @classmethod
    def list_strategies(cls) -> List[Dict[str, Any]]:
        """列出所有可用策略及其描述"""
        strategy_info = {
            "semantic": {
                "name": "语义结构分块",
                "description": "保护公式/表格不切断，跟踪 heading 面包屑，适合教材、论文、教案",
                "default_chunk_size": 512,
                "default_overlap": 64,
            },
            "fixed": {
                "name": "固定长度滑窗",
                "description": "按 token 数硬切 + overlap，无结构感知，适合纯文本、笔记",
                "default_chunk_size": 384,
                "default_overlap": 48,
            },
            "markdown_header": {
                "name": "标题层级分块",
                "description": "以 # / ## 为边界切分，同 section 合并，超阈值再切，适合 Markdown 文档",
                "default_chunk_size": 512,
                "default_overlap": 64,
            },
            "qa_pair": {
                "name": "问答对分块",
                "description": "识别 Q:/A: 或编号题目，每题独立成块，适合试题、FAQ",
                "default_chunk_size": 128,
                "default_overlap": 16,
            },
            "recursive": {
                "name": "递归字符分块",
                "description": "按 \\n\\n → \\n → 。 → 空格 优先级递归切分，适合混合结构长文",
                "default_chunk_size": 384,
                "default_overlap": 48,
            },
            "sentence": {
                "name": "句子级分块",
                "description": "按句号切分，每块 2-5 句，适合法规条文、短句密集型文档",
                "default_chunk_size": 128,
                "default_overlap": 16,
            },
        }
        result = []
        for sid, info in strategy_info.items():
            result.append({"id": sid, **info})
        result.append({
            "id": "auto",
            "name": "自动选择",
            "description": "根据文档类型（PDF/Word/Markdown/TXT）自动选择最佳策略与参数",
            "default_chunk_size": None,
            "default_overlap": None,
        })
        return result


# 向后兼容：保留原单例，默认参数不变
semantic_chunker = SemanticFormulaSafeChunker()

# 工厂单例
chunker_factory = ChunkerFactory()
