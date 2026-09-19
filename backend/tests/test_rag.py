import pytest
from app.services.rag.chunker import semantic_chunker
from app.services.rag.hallucination import hallucination_checker


def test_formula_safe_chunking():
    # Document with LaTeX display math block
    doc = (
        "# 高中物理教案\n\n"
        "带电粒子在复合场中的运动分析。\n\n"
        "$$\n"
        "F = qE + qv \\times B\n"
        "$$\n\n"
        "上述洛伦兹力与电场力的合力决定了粒子的偏转半径。\n"
    )

    chunks = semantic_chunker.chunk_document(
        markdown_text=doc,
        doc_id="test_doc_01",
        kb_id="kb_physics",
        default_metadata={"subject": "高中物理"}
    )

    assert len(chunks) >= 1
    # Verify display formula is kept intact within one chunk
    found_formula = any("F = qE + qv \\times B" in c["content"] for c in chunks)
    assert found_formula
    assert chunks[0]["metadata"]["has_formula"] is True


def test_hallucination_and_citation_extraction():
    answer = "根据课标要求 [1]，导数的几何意义在高考中常作为压轴题出现 [2]。"
    mock_retrieved = [
        {"chunk_id": "c1", "content": "导数的几何意义在高考中属于重点考察内容。", "page_number": 74},
        {"chunk_id": "c2", "content": "综合压轴题通常考查导数与函数零点的关系。", "page_number": 82}
    ]

    citations = hallucination_checker.extract_citations(answer, mock_retrieved)
    assert len(citations) == 2
    assert citations[0]["citation_id"] == 1
    assert citations[0]["page_number"] == 74
    assert citations[1]["citation_id"] == 2
    assert citations[1]["page_number"] == 82
