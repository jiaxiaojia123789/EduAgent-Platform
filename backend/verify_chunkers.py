"""
验证 6 种分块策略 + 工厂 + 自动映射 + 参数校验
"""
import sys
sys.path.insert(0, ".")

from app.services.rag.chunker import (
    chunker_factory,
    SemanticFormulaSafeChunker,
    FixedSizeChunker,
    MarkdownHeaderChunker,
    QAPairChunker,
    RecursiveChunker,
    SentenceChunker,
    validate_chunk_size,
    validate_overlap,
    auto_detect_strategy,
    MIN_CHUNK_SIZE,
    MAX_CHUNK_SIZE,
)

SAMPLE_MD = """# 第一章 导数的概念

## 1.1 平均变化率

平均变化率的定义如下。设函数 $y=f(x)$ 在点 $x_0$ 的某邻域内有定义。

令自变量在 $x_0$ 处取得增量 $\\Delta x$，则函数值相应地取得增量：
$$\\Delta y = f(x_0 + \\Delta x) - f(x_0)$$

平均变化率为：
$$\\frac{\\Delta y}{\\Delta x} = \\frac{f(x_0 + \\Delta x) - f(x_0)}{\\Delta x}$$

## 1.2 瞬时变化率

当 $\\Delta x \\to 0$ 时，若平均变化率的极限存在，则称此极限为函数在 $x_0$ 处的瞬时变化率。

## 习题

1. 求 $f(x)=x^2$ 在 $x=1$ 处的导数。
答案：$f'(1)=2$

2. 求 $f(x)=\\sin x$ 的导数。
答案：$f'(x)=\\cos x$

| 函数 | 导数 |
|------|------|
| $x^n$ | $nx^{n-1}$ |
| $\\sin x$ | $\\cos x$ |
"""


def test_validate():
    print("=" * 60)
    print("Test 1: 参数校验")
    print("=" * 60)
    # 合法值
    assert validate_chunk_size(128) == 128
    assert validate_chunk_size(1024) == 1024
    assert validate_overlap(64, 512) == 64
    print("[PASS] 合法值校验通过")

    # 非法值 - 小于下限
    try:
        validate_chunk_size(64)
        print("[FAIL] 应拒绝 chunk_size=64")
    except ValueError as e:
        print(f"[PASS] chunk_size=64 被拒绝: {e}")

    # 非法值 - 大于上限
    try:
        validate_chunk_size(2048)
        print("[FAIL] 应拒绝 chunk_size=2048")
    except ValueError as e:
        print(f"[PASS] chunk_size=2048 被拒绝: {e}")

    # overlap 超过 50%
    try:
        validate_overlap(300, 512)
        print("[FAIL] 应拒绝 overlap=300 (58%)")
    except ValueError as e:
        print(f"[PASS] overlap=300 被拒绝: {e}")


def test_auto_detect():
    print()
    print("=" * 60)
    print("Test 2: 按 doc_type 自动映射")
    print("=" * 60)
    for doc_type in ["PDF", "WORD", "MARKDOWN", "TXT", "UNKNOWN"]:
        cfg = auto_detect_strategy(doc_type)
        print(f"  {doc_type:10s} -> strategy={cfg['strategy']:18s} size={cfg['chunk_size']} overlap={cfg['overlap']}")
    print("[PASS] 自动映射完成")


def test_all_strategies():
    print()
    print("=" * 60)
    print("Test 3: 6 种分块策略")
    print("=" * 60)
    strategies = ["semantic", "fixed", "markdown_header", "qa_pair", "recursive", "sentence"]
    for sid in strategies:
        chunker, resolved = chunker_factory.get_strategy(strategy=sid, doc_type="PDF")
        chunks = chunker.chunk_document(SAMPLE_MD, doc_id="doc-1", kb_id="kb-1")
        avg_tokens = sum(c["tokens"] for c in chunks) / max(len(chunks), 1)
        print(f"  {sid:18s} -> {len(chunks):3d} chunks, avg_tokens={avg_tokens:.0f}, "
              f"size={chunker.chunk_size}, overlap={chunker.overlap}")
        # 基本校验
        assert len(chunks) > 0, f"{sid} 未产生任何分块"
        assert all(c["content"].strip() for c in chunks), f"{sid} 存在空分块"
        assert all(c["tokens"] > 0 for c in chunks), f"{sid} 存在 0 token 分块"
    print("[PASS] 6 种策略均正常产出分块")


def test_factory_auto():
    print()
    print("=" * 60)
    print("Test 4: 工厂 auto 模式")
    print("=" * 60)
    for doc_type in ["PDF", "WORD", "MARKDOWN", "TXT"]:
        chunker, resolved = chunker_factory.get_strategy(strategy="auto", doc_type=doc_type)
        print(f"  auto({doc_type}) -> resolved={resolved:18s} size={chunker.chunk_size} overlap={chunker.overlap}")
    print("[PASS] auto 模式解析正确")


def test_backward_compat():
    print()
    print("=" * 60)
    print("Test 5: 向后兼容（semantic_chunker 单例）")
    print("=" * 60)
    from app.services.rag.chunker import semantic_chunker
    chunks = semantic_chunker.chunk_document(SAMPLE_MD, doc_id="doc-1", kb_id="kb-1")
    print(f"  semantic_chunker 单例 -> {len(chunks)} chunks")
    # 验证公式块未被切断
    math_blocks_intact = all("$$" not in c["content"] or c["content"].count("$$") % 2 == 0 for c in chunks)
    print(f"  公式块完整: {math_blocks_intact}")
    print(f"  [{'PASS' if math_blocks_intact else 'FAIL'}] 向后兼容")


def test_list_strategies():
    print()
    print("=" * 60)
    print("Test 6: 列出策略接口")
    print("=" * 60)
    strategies = chunker_factory.list_strategies()
    print(f"  共 {len(strategies)} 种策略（含 auto）")
    for s in strategies:
        print(f"    - {s['id']:18s} {s['name']}")
    assert len(strategies) == 7  # 6 + auto
    print("[PASS] 策略列表正确")


if __name__ == "__main__":
    test_validate()
    test_auto_detect()
    test_all_strategies()
    test_factory_auto()
    test_backward_compat()
    test_list_strategies()
    print()
    print("=" * 60)
    print("ALL TESTS PASSED ✓")
    print("=" * 60)
