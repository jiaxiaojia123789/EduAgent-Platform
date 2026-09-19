import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from pydantic import BaseModel
from app.schemas.knowledge import SearchRequest, SearchResponse, SearchResultItem
from app.services.rag.document_parser import unified_document_parser
from app.services.rag.chunker import (
    semantic_chunker,
    chunker_factory,
    ChunkStrategy,
    MIN_CHUNK_SIZE,
    MAX_CHUNK_SIZE,
    auto_detect_strategy,
    AUTO_STRATEGY_MAP,
)
from app.services.rag.milvus_manager import milvus_manager
from app.services.rag.hybrid_search import hybrid_search_engine
from app.services.rag.reranker import bge_reranker
from app.services.llm.bailian_client import bailian_client

router = APIRouter(prefix="/knowledge", tags=["Knowledge Base & RAG"])

# In-memory Knowledge Bases Registry
KNOWLEDGE_BASES_STORE: Dict[str, Dict[str, Any]] = {
    "kb-math-01": {
        "id": "kb-math-01",
        "name": "普通高中数学人教A版教材与典型课例库",
        "subject": "高中数学",
        "grade": "高二",
        "description": "包含人教A版选择性必修全套微积分导数、解析几何及典型例题解析",
        "document_count": 2,
        "chunk_count": 18
    },
    "kb-edu-research": {
        "id": "kb-edu-research",
        "name": "基础教育教学改革与核心素养学术期刊库",
        "subject": "教育学",
        "grade": "通用",
        "description": "汇聚《课程·教材·教法》、《中国教育学刊》关于核心素养与AI数字化赋能文献",
        "document_count": 1,
        "chunk_count": 12
    }
}

# Documents Registry
DOCUMENTS_STORE: Dict[str, Dict[str, Any]] = {
    "doc-math-textbook": {
        "id": "doc-math-textbook",
        "kb_id": "kb-math-01",
        "filename": "人教A版数学选择性必修二_导数及其应用.pdf",
        "doc_type": "PDF",
        "file_size": 2048576,
        "chunk_count": 12,
        "formula_count": 26,
        "table_count": 3,
        "status": "SUCCESS",
        "created_at": "2026-09-16 10:00:00"
    },
    "doc-math-lesson": {
        "id": "doc-math-lesson",
        "kb_id": "kb-math-01",
        "filename": "特级教师《导数的几何意义》公开课教案.docx",
        "doc_type": "WORD",
        "file_size": 512000,
        "chunk_count": 6,
        "formula_count": 14,
        "table_count": 2,
        "status": "SUCCESS",
        "created_at": "2026-09-16 11:30:00"
    },
    "doc-edu-paper": {
        "id": "doc-edu-paper",
        "kb_id": "kb-edu-research",
        "filename": "基于大模型的多智能体赋能个性化探究教学实证研究.pdf",
        "doc_type": "PDF",
        "file_size": 3145728,
        "chunk_count": 12,
        "formula_count": 8,
        "table_count": 5,
        "status": "SUCCESS",
        "created_at": "2026-09-16 14:15:00"
    }
}

# Chunks Store
DOCUMENT_CHUNKS_STORE: Dict[str, List[Dict[str, Any]]] = {}

# Seed initial demonstration chunks
DEMO_INITIAL_CHUNKS = [
    {
        "chunk_id": "math_c1",
        "kb_id": "kb-math-01",
        "doc_id": "doc-math-textbook",
        "content": (
            "### §3.1 导数的几何意义\n"
            "设函数 $y = f(x)$ 在点 $x_0$ 附近有定义。当自变量 $x$ 在 $x_0$ 处有增量 $\\Delta x$ 时，"
            "函数值相应地有增量 $\\Delta y = f(x_0 + \\Delta x) - f(x_0)$。\n"
            "比值 $\\frac{\\Delta y}{\\Delta x}$ 称为函数在 $x_0$ 到 $x_0 + \\Delta x$ 之间的平均变化率，"
            "在几何上表示割线 $PQ$ 的斜率。\n"
            "当 $\\Delta x \\to 0$ 时，如果割线 $PQ$ 的极限位置存在，该极限位置 $PT$ 称为曲线在点 $P$ 处的切线。\n"
            "切线的斜率 $k = \\lim_{\\Delta x \\to 0} \\frac{\\Delta y}{\\Delta x} = f'(x_0)$。"
        ),
        "tokens": 280,
        "formula_count": 4,
        "table_count": 0,
        "page_number": 74,
        "metadata": {"subject": "高中数学", "section_path": "高中数学 > 导数及其应用 > 导数的几何意义"}
    },
    {
        "chunk_id": "math_c2",
        "kb_id": "kb-math-01",
        "doc_id": "doc-math-textbook",
        "content": (
            "### 切线方程的规范求解步骤\n"
            "求曲线 $y = f(x)$ 在点 $P(x_0, y_0)$ 处的切线方程的标准步骤：\n"
            "1. 求导函数 $f'(x)$；\n"
            "2. 计算切点处的导数值，得到切线斜率 $k = f'(x_0)$；\n"
            "3. 利用点斜式方程列出切线方程：$$y - y_0 = f'(x_0)(x - x_0)$$\n"
            "【注意区分】“在点 $P$ 处的切线”与“过点 $P$ 的切线”。若点 $P$ 不在曲线上，必须先设切点坐标 $(x_1, f(x_1))$。"
        ),
        "tokens": 240,
        "formula_count": 3,
        "table_count": 0,
        "page_number": 76,
        "metadata": {"subject": "高中数学", "section_path": "高中数学 > 导数及其应用 > 切线方程"}
    },
    {
        "chunk_id": "doc_word_c1",
        "kb_id": "kb-math-01",
        "doc_id": "doc-math-lesson",
        "content": (
            "### 公开课板书设计与学生探究导学案（Word提炼）\n\n"
            "| 教学环节 | 教师启发引导活动 | 学生活动 | 核心素养对标 |\n"
            "|---|---|---|---|\n"
            "| 环节一：情境导入 | 展示高铁刹车与瞬时速度微积分视频 | 观察并回忆平均速度极限 | 直观想象 |\n"
            "| 环节二：几何抽象 | 几何画板演示动点割线无限趋向切线 | 动手测量斜率变化规律 | 数学抽象 |\n"
            "| 环节三：例题精析 | 指导求解 $y = x^2$ 在点 $(1, 1)$ 处的切线 | 分组推导演算方程 | 数学运算 |\n\n"
            "核心公式板书：$$k = \\lim_{\\Delta x \\to 0} \\frac{f(x_0 + \\Delta x) - f(x_0)}{\\Delta x}$$"
        ),
        "tokens": 310,
        "formula_count": 2,
        "table_count": 1,
        "page_number": 1,
        "metadata": {"subject": "高中数学", "section_path": "教学设计 > 课堂环节与板书设计"}
    },
    {
        "chunk_id": "res_c1",
        "kb_id": "kb-edu-research",
        "doc_id": "doc-edu-paper",
        "content": (
            "### 大语言模型在个性化探究教学中的应用实证\n"
            "研究表明，基于检索增强生成（RAG）和多智能体协同的生成式教育系统，在支持学生自主探究方面具有显著正向效益 [1]。"
            "通过保留公式 LaTeX 原语和 Markdown 表格的版面分析，能将专业数学理科 QA 的准确率提升 25% 以上，"
            "同时大幅降低专业问答的幻觉发生率（下降超 30%）。"
        ),
        "tokens": 260,
        "formula_count": 1,
        "table_count": 0,
        "page_number": 12,
        "metadata": {"subject": "教育学", "section_path": "教育科学研究 > 人工智能教育赋能"}
    }
]

# Register initial chunks
for c in DEMO_INITIAL_CHUNKS:
    did = c["doc_id"]
    if did not in DOCUMENT_CHUNKS_STORE:
        DOCUMENT_CHUNKS_STORE[did] = []
    DOCUMENT_CHUNKS_STORE[did].append(c)

# Initialize BM25 with initial chunks
hybrid_search_engine.index_for_bm25(DEMO_INITIAL_CHUNKS)


class CreateKBRequest(BaseModel):
    name: str
    subject: str = "高中数学"
    grade: str = "高二"
    description: Optional[str] = None


@router.get("/list")
async def list_knowledge_bases():
    """Returns all registered knowledge bases."""
    return {"knowledge_bases": list(KNOWLEDGE_BASES_STORE.values())}


@router.post("/create")
async def create_knowledge_base(payload: CreateKBRequest):
    """Creates a new knowledge base."""
    kb_id = f"kb-{uuid.uuid4().hex[:8]}"
    new_kb = {
        "id": kb_id,
        "name": payload.name,
        "subject": payload.subject,
        "grade": payload.grade,
        "description": payload.description or f"{payload.subject}专属教学教研知识库",
        "document_count": 0,
        "chunk_count": 0
    }
    KNOWLEDGE_BASES_STORE[kb_id] = new_kb
    return new_kb


@router.get("/{kb_id}/documents")
async def list_kb_documents(kb_id: str):
    """Lists all documents within a knowledge base."""
    docs = [d for d in DOCUMENTS_STORE.values() if d["kb_id"] == kb_id]
    return {"documents": docs}


@router.get("/documents/{doc_id}/chunks")
async def get_document_chunks(doc_id: str):
    """Returns the semantic chunks for a document, allowing users to inspect chunking quality."""
    chunks = DOCUMENT_CHUNKS_STORE.get(doc_id, [])
    return {"chunks": chunks, "total": len(chunks)}


@router.get("/chunk-strategies")
async def list_chunk_strategies():
    """
    分块配置接口：列出所有可用分块策略、默认参数及取值范围
    """
    strategies = chunker_factory.list_strategies()
    return {
        "strategies": strategies,
        "constraints": {
            "chunk_size_range": [MIN_CHUNK_SIZE, MAX_CHUNK_SIZE],
            "chunk_overlap_max_ratio": "50% of chunk_size",
            "default_overlap_ratio": "12.5% of chunk_size",
        },
        # 结构化类型推荐映射：前端按文档类型联动展示推荐策略与默认参数
        "auto_mapping": AUTO_STRATEGY_MAP,
    }


@router.post("/chunk-preview")
async def preview_chunking(
    file: UploadFile = File(...),
    chunk_strategy: str = Form("auto"),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
):
    """
    Dry-run 分块预览：解析 + 试分块，不向量化、不入库。
    返回实际采用的策略/参数、切片总数、token 分布统计与前 5 个切片内容，
    供用户在正式上传前检查分块效果。
    """
    filename = file.filename or "uploaded_document"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 ({ext})。请上传 PDF, Word (.docx), Markdown (.md) 或 TXT 文档。"
        )

    if chunk_size != 0 and (chunk_size < MIN_CHUNK_SIZE or chunk_size > MAX_CHUNK_SIZE):
        raise HTTPException(
            status_code=400,
            detail=f"chunk_size 必须在 {MIN_CHUNK_SIZE}-{MAX_CHUNK_SIZE} 之间，当前值: {chunk_size}"
        )

    temp_dir = "./temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex[:6]}_{filename}")

    file_bytes = await file.read()
    with open(temp_path, "wb") as f:
        f.write(file_bytes)

    try:
        parsed = unified_document_parser.parse_file(temp_path, temp_dir)
        md_content = parsed["markdown_content"]
        doc_type = parsed["doc_type"]

        try:
            chunker, resolved_strategy = chunker_factory.get_strategy(
                strategy=chunk_strategy,
                chunk_size=chunk_size,
                overlap=chunk_overlap,
                doc_type=doc_type,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        chunks = chunker.chunk_document(
            markdown_text=md_content,
            doc_id="preview",
            kb_id="preview",
            default_metadata={"filename": filename, "doc_type": doc_type},
        )

        token_values = [c["tokens"] for c in chunks]
        preview_items = [
            {
                "chunk_index": c["chunk_index"],
                "content": c["content"],
                "tokens": c["tokens"],
                "formula_count": c["formula_count"],
                "table_count": c["table_count"],
                "section_path": c["metadata"].get("section_path", ""),
            }
            for c in chunks[:5]
        ]

        return {
            "status": "success",
            "doc_type": doc_type,
            "chunk_strategy": resolved_strategy,
            "chunk_size": chunker.chunk_size,
            "chunk_overlap": chunker.overlap,
            "total_chunks": len(chunks),
            "token_stats": {
                "min": min(token_values) if token_values else 0,
                "max": max(token_values) if token_values else 0,
                "avg": int(sum(token_values) / len(token_values)) if token_values else 0,
            },
            "preview_chunks": preview_items,
        }
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form("kb-math-01"),
    subject: str = Form("高中数学"),
    grade: str = Form("高二"),
    chunk_strategy: str = Form("auto"),
    chunk_size: int = Form(0),
    chunk_overlap: int = Form(0),
):
    """
    多格式教育文档入库：
    1. 支持 PDF/Word/Markdown/TXT
    2. MinerU 版面分析（公式/表格保留）
    3. 可配置分块策略（6 种 + auto 自动选择），chunk_size 限制 128-1024
    4. Text-Embedding-v3 向量化 + Milvus 2.4 入库
    5. BM25 稀疏索引更新

    分块参数：
    - chunk_strategy: auto/semantic/fixed/markdown_header/qa_pair/recursive/sentence
    - chunk_size: 128-1024（0 表示用策略默认值）
    - chunk_overlap: >=0 且 <= chunk_size/2（0 表示默认 12.5%）
    """
    filename = file.filename or "uploaded_document"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in [".pdf", ".docx", ".doc", ".md", ".markdown", ".txt"]:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 ({ext})。请上传 PDF, Word (.docx), Markdown (.md) 或 TXT 文档。"
        )

    # 校验 chunk_size 范围（0 除外，表示用默认值）
    if chunk_size != 0 and (chunk_size < MIN_CHUNK_SIZE or chunk_size > MAX_CHUNK_SIZE):
        raise HTTPException(
            status_code=400,
            detail=f"chunk_size 必须在 {MIN_CHUNK_SIZE}-{MAX_CHUNK_SIZE} 之间，当前值: {chunk_size}"
        )

    temp_dir = "./temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f"{uuid.uuid4().hex[:6]}_{filename}")

    # Save uploaded file
    file_bytes = await file.read()
    with open(temp_path, "wb") as f:
        f.write(file_bytes)

    # 1. Multi-format layout & document parsing
    parsed = unified_document_parser.parse_file(temp_path, temp_dir)
    md_content = parsed["markdown_content"]
    doc_type = parsed["doc_type"]

    # 2. 分块：通过工厂选择策略（auto 按 doc_type 自动选择）
    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    try:
        chunker, resolved_strategy = chunker_factory.get_strategy(
            strategy=chunk_strategy,
            chunk_size=chunk_size,
            overlap=chunk_overlap,
            doc_type=doc_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    chunks = chunker.chunk_document(
        markdown_text=md_content,
        doc_id=doc_id,
        kb_id=kb_id,
        default_metadata={
            "subject": subject,
            "grade": grade,
            "filename": filename,
            "doc_type": doc_type,
        }
    )

    # Format chunks with IDs
    formatted_chunks = []
    for idx, c in enumerate(chunks):
        c_obj = dict(c)
        c_obj["chunk_id"] = f"{doc_id}_c{idx + 1}"
        formatted_chunks.append(c_obj)

    # 3. Vector Embeddings with DashScope Text-Embedding-v3 & Milvus 2.4 insertion
    vectors = []
    for c in formatted_chunks:
        vec = await bailian_client.get_embedding(c["content"])
        vectors.append(vec)

    milvus_manager.insert_chunks(formatted_chunks, vectors)

    # 4. Save to document store & chunk store
    new_doc_record = {
        "id": doc_id,
        "kb_id": kb_id,
        "filename": filename,
        "doc_type": doc_type,
        "file_size": len(file_bytes),
        "chunk_count": len(formatted_chunks),
        "formula_count": parsed["formula_count"],
        "table_count": parsed["table_count"],
        "chunk_strategy": resolved_strategy,
        "chunk_size": chunker.chunk_size,
        "chunk_overlap": chunker.overlap,
        "status": "SUCCESS",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    }
    DOCUMENTS_STORE[doc_id] = new_doc_record
    DOCUMENT_CHUNKS_STORE[doc_id] = formatted_chunks

    # Update KB stats
    if kb_id in KNOWLEDGE_BASES_STORE:
        KNOWLEDGE_BASES_STORE[kb_id]["document_count"] += 1
        KNOWLEDGE_BASES_STORE[kb_id]["chunk_count"] += len(formatted_chunks)

    # 5. Update BM25 index with all chunks
    all_chunks = []
    for doc_chunks in DOCUMENT_CHUNKS_STORE.values():
        all_chunks.extend(doc_chunks)
    hybrid_search_engine.index_for_bm25(all_chunks)

    # Clean up temp file
    try:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception:
        pass

    return {
        "status": "success",
        "doc_id": doc_id,
        "filename": filename,
        "doc_type": doc_type,
        "chunks_created": len(formatted_chunks),
        "formula_count": parsed["formula_count"],
        "table_count": parsed["table_count"],
        "estimated_pages": parsed["page_count"],
        "chunk_strategy": resolved_strategy,
        "chunk_size": chunker.chunk_size,
        "chunk_overlap": chunker.overlap,
        "message": f"《{filename}》已完成版面解析与{resolved_strategy}分块，并成功入库 Milvus 2.4 与 BM25 双路向量索引！"
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Deletes a document and its chunks from knowledge base."""
    doc = DOCUMENTS_STORE.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    kb_id = doc["kb_id"]
    chunk_count = doc["chunk_count"]

    del DOCUMENTS_STORE[doc_id]
    if doc_id in DOCUMENT_CHUNKS_STORE:
        del DOCUMENT_CHUNKS_STORE[doc_id]

    if kb_id in KNOWLEDGE_BASES_STORE:
        KNOWLEDGE_BASES_STORE[kb_id]["document_count"] = max(0, KNOWLEDGE_BASES_STORE[kb_id]["document_count"] - 1)
        KNOWLEDGE_BASES_STORE[kb_id]["chunk_count"] = max(0, KNOWLEDGE_BASES_STORE[kb_id]["chunk_count"] - chunk_count)

    # Re-index BM25
    all_chunks = []
    for doc_chunks in DOCUMENT_CHUNKS_STORE.values():
        all_chunks.extend(doc_chunks)
    hybrid_search_engine.index_for_bm25(all_chunks)

    return {"status": "success", "message": f"文档《{doc['filename']}》已从知识库中彻底移除"}


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(payload: SearchRequest):
    """
    Two-stage Hybrid Search: BM25 + Milvus 2.4 Vector Search + BGE Reranker
    """
    import time
    start = time.time()

    candidates = await hybrid_search_engine.search(
        query=payload.query,
        kb_ids=payload.kb_ids,
        top_k=payload.top_k * 3
    )

    if payload.enable_rerank and candidates:
        ranked = bge_reranker.rerank(payload.query, candidates, top_n=payload.top_k)
    else:
        ranked = candidates[:payload.top_k]

    results = []
    for r in ranked:
        doc = DOCUMENTS_STORE.get(r.get("doc_id", ""), {})
        results.append(SearchResultItem(
            chunk_id=r.get("chunk_id", ""),
            doc_id=r.get("doc_id", ""),
            doc_name=doc.get("filename", "数学教材与学术文献精选"),
            content=r.get("content", ""),
            page_number=r.get("page_number", 1),
            sparse_score=r.get("sparse_score", 0.0),
            dense_score=r.get("dense_score", 0.0),
            final_score=r.get("rerank_score", r.get("final_score", 0.0)),
            metadata=r.get("metadata", {})
        ))

    elapsed = int((time.time() - start) * 1000)
    return SearchResponse(
        query=payload.query,
        results=results,
        total_retrieved=len(results),
        latency_ms=elapsed
    )
