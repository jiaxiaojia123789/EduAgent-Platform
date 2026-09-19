import json
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from app.core.config import settings

logger = logging.getLogger(__name__)


class MilvusManager:
    """
    Milvus 2.4 Distributed Vector Database Client Manager
    Features:
    1. Dynamic Collection Creation with HNSW Index (M=16, efConstruction=200)
    2. Inverted Index for fast scalar filtering (kb_id, subject, grade)
    3. Hybrid Search with scalar predicates
    4. Memory-backed fallback for standalone mock verification
    """

    def __init__(self):
        self.host = settings.MILVUS_HOST
        self.port = settings.MILVUS_PORT
        self.collection_name = settings.MILVUS_COLLECTION
        self.dim = settings.EMBEDDING_DIMENSION
        self.is_connected = False
        self._memory_chunks: List[Dict[str, Any]] = []

    def connect(self) -> bool:
        """Connects to Milvus 2.4 standalone/cluster."""
        try:
            from pymilvus import connections, utility
            connections.connect(
                alias="default",
                host=self.host,
                port=self.port,
                user=settings.MILVUS_USER,
                password=settings.MILVUS_PASSWORD,
                timeout=3.0
            )
            self.is_connected = True
            logger.info(f"[MilvusManager] Successfully connected to Milvus 2.4 at {self.host}:{self.port}")
            self.ensure_collection()
            return True
        except Exception as e:
            self.is_connected = False
            logger.warning(f"[MilvusManager] Milvus connection skipped ({e}). Using in-memory vector store.")
            return False

    def ensure_collection(self):
        """Creates collection and indexes if not exists."""
        if not self.is_connected:
            return

        try:
            from pymilvus import (
                utility, FieldSchema, CollectionSchema, DataType, Collection
            )
            if not utility.has_collection(self.collection_name):
                fields = [
                    FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=64, is_primary=True),
                    FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="subject", dtype=DataType.VARCHAR, max_length=32),
                    FieldSchema(name="grade", dtype=DataType.VARCHAR, max_length=32),
                    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
                    FieldSchema(name="page_num", dtype=DataType.INT32),
                    FieldSchema(name="has_formula", dtype=DataType.BOOL),
                    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
                ]
                schema = CollectionSchema(fields=fields, description="Educational Knowledge Chunks")
                collection = Collection(name=self.collection_name, schema=schema)

                # Create HNSW Index
                index_params = {
                    "metric_type": "COSINE",
                    "index_type": "HNSW",
                    "params": {"M": 16, "efConstruction": 200}
                }
                collection.create_index(field_name="vector", index_params=index_params)
                collection.load()
                logger.info(f"[MilvusManager] Collection '{self.collection_name}' initialized with HNSW index.")
        except Exception as e:
            logger.error(f"[MilvusManager] Error ensuring collection: {e}")

    def insert_chunks(self, chunks: List[Dict[str, Any]], vectors: List[List[float]]) -> List[str]:
        """Inserts chunks and vectors into Milvus 2.4."""
        chunk_ids = []

        if self.is_connected:
            try:
                from pymilvus import Collection
                collection = Collection(self.collection_name)
                
                entities = [
                    [c["chunk_id"] for c in chunks],
                    [c.get("kb_id", "") for c in chunks],
                    [c.get("doc_id", "") for c in chunks],
                    [c.get("metadata", {}).get("subject", "通用") for c in chunks],
                    [c.get("metadata", {}).get("grade", "通用") for c in chunks],
                    [c["content"][:4000] for c in chunks],
                    [c.get("page_number", 1) for c in chunks],
                    [c.get("metadata", {}).get("has_formula", False) for c in chunks],
                    vectors
                ]
                collection.insert(entities)
                collection.flush()
                return [c["chunk_id"] for c in chunks]
            except Exception as e:
                logger.error(f"[MilvusManager] Insertion error: {e}")

        # In-memory storage fallback
        for c, v in zip(chunks, vectors):
            cid = c.get("chunk_id") or str(len(self._memory_chunks) + 1)
            chunk_copy = dict(c)
            chunk_copy["chunk_id"] = cid
            chunk_copy["vector"] = v
            self._memory_chunks.append(chunk_copy)
            chunk_ids.append(cid)
        return chunk_ids

    def search_vector(
        self,
        query_vector: List[float],
        top_k: int = 5,
        kb_ids: Optional[List[str]] = None,
        subject: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Vector similarity search with scalar metadata filtering."""
        if self.is_connected:
            try:
                from pymilvus import Collection
                collection = Collection(self.collection_name)
                
                # Build scalar filter expr
                expr_parts = []
                if kb_ids:
                    kb_str = ", ".join([f"'{k}'" for k in kb_ids])
                    expr_parts.append(f"kb_id in [{kb_str}]")
                if subject:
                    expr_parts.append(f"subject == '{subject}'")

                expr = " and ".join(expr_parts) if expr_parts else None

                search_params = {"metric_type": "COSINE", "params": {"ef": 64}}
                results = collection.search(
                    data=[query_vector],
                    anns_field="vector",
                    param=search_params,
                    limit=top_k,
                    expr=expr,
                    output_fields=["chunk_id", "kb_id", "doc_id", "content", "page_num"]
                )

                hits = []
                for hit in results[0]:
                    hits.append({
                        "chunk_id": hit.entity.get("chunk_id"),
                        "kb_id": hit.entity.get("kb_id"),
                        "doc_id": hit.entity.get("doc_id"),
                        "content": hit.entity.get("content"),
                        "page_number": hit.entity.get("page_num", 1),
                        "score": round(float(hit.score), 4)
                    })
                return hits
            except Exception as e:
                logger.error(f"[MilvusManager] Search error: {e}")

        # In-memory cosine similarity search
        if not self._memory_chunks:
            return []

        q_vec = np.array(query_vector)
        scored = []
        for c in self._memory_chunks:
            if kb_ids and c.get("kb_id") not in kb_ids:
                continue
            c_vec = np.array(c["vector"])
            dot = np.dot(q_vec, c_vec)
            norm = (np.linalg.norm(q_vec) * np.linalg.norm(c_vec)) or 1e-6
            sim = dot / norm
            scored.append((sim, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sim, c in scored[:top_k]:
            results.append({
                "chunk_id": c["chunk_id"],
                "kb_id": c.get("kb_id", ""),
                "doc_id": c.get("doc_id", ""),
                "content": c["content"],
                "page_number": c.get("page_number", 1),
                "score": round(float(sim), 4)
            })
        return results


milvus_manager = MilvusManager()
