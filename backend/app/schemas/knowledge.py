from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class KBCreate(BaseModel):
    name: str = Field(description="知识库名称")
    description: Optional[str] = None
    embedding_model: Optional[str] = "text-embedding-v3"
    dimension: Optional[int] = 1024


class KBOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    owner_id: str
    embedding_model: str
    dimension: int
    milvus_collection: str
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentOut(BaseModel):
    id: str
    kb_id: str
    filename: str
    file_size: int
    parse_status: str
    parser_type: str
    chunk_count: int
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChunkOut(BaseModel):
    id: str
    doc_id: str
    chunk_index: int
    content: str
    tokens: int
    formula_count: int
    table_count: int
    page_number: int
    metadata_json: Dict[str, Any]

    class Config:
        from_attributes = True


class SearchRequest(BaseModel):
    query: str
    kb_ids: List[str]
    top_k: int = 5
    score_threshold: float = 0.3
    filter_subject: Optional[str] = None
    filter_grade: Optional[str] = None
    enable_rerank: bool = True


class SearchResultItem(BaseModel):
    chunk_id: str
    doc_id: str
    doc_name: str
    content: str
    page_number: int
    sparse_score: float = 0.0
    dense_score: float = 0.0
    final_score: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    query: str
    results: List[SearchResultItem]
    total_retrieved: int
    latency_ms: int
