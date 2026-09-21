"""
Grounding 真实评测脚本：用 LLM NLI 裁判跑一组测试用例，输出 grounding 通过率与 LaTeX 闭合率。
运行：cd backend && python eval_grounding.py
"""
import asyncio
import sys
sys.path.insert(0, ".")

from app.services.rag.hallucination import hallucination_checker
from app.harness.benchmark import BenchmarkHarness

# 测试集：(query, answer, retrieved_docs)，覆盖有依据/矛盾/无依据/含公式
TEST_CASES = [
    {
        "query": "导数的几何意义是什么",
        "answer": "导数的几何意义是曲线在该点处切线的斜率。当导数大于零时函数递增。",
        "retrieved_docs": [{"content": "导数 f'(x0) 表示曲线在点 x0 处切线的斜率。f'(x0)>0 时函数递增。"}],
    },
    {
        "query": "导数的几何意义",
        "answer": "导数的几何意义是曲线与 x 轴围成的面积。",  # contradiction
        "retrieved_docs": [{"content": "导数 f'(x0) 表示曲线在点 x0 处切线的斜率。"}],
    },
    {
        "query": "牛顿第二定律",
        "answer": "月球绕地球公转周期约 27 天。火星表面重力约为地球的 38%。",  # 全 neutral
        "retrieved_docs": [{"content": "牛顿第二定律：F=ma，物体加速度与合外力成正比。"}],
    },
    {
        "query": "二次函数顶点",
        "answer": "二次函数 $y=ax^2+bx+c$ 的顶点坐标为 $\\left(-\\frac{b}{2a}, \\frac{4ac-b^2}{4a}\\right)$。",  # LaTeX 闭合
        "retrieved_docs": [{"content": "二次函数顶点坐标为 (-b/2a, (4ac-b²)/4a)。"}],
    },
    {
        "query": "积分公式",
        "answer": "不定积分 $\\int x^n dx = \\frac{x^{n+1}}{n+1} + C$。",  # LaTeX 闭合
        "retrieved_docs": [{"content": "幂函数积分公式：∫xⁿdx = xⁿ⁺¹/(n+1) + C (n≠-1)。"}],
    },
    {
        "query": "三角函数",
        "answer": "正弦函数的导数是余弦函数，即 $(\\sin x)' = \\cos x$。",  # LaTeX 闭合
        "retrieved_docs": [{"content": "基本求导公式：(sin x)' = cos x。"}],
    },
    {
        "query": "勾股定理",
        "answer": "直角三角形两直角边的平方和等于斜边的平方，即 $a^2 + b^2 = c^2$。",  # 有依据
        "retrieved_docs": [{"content": "勾股定理：直角三角形中 a² + b² = c²。"}],
    },
    {
        "query": "椭圆方程",
        "answer": "椭圆的标准方程为 $\\frac{x^2}{a^2} + \\frac{y^2}{b^2} = 1$。",  # 有依据
        "retrieved_docs": [{"content": "椭圆标准方程：x²/a² + y²/b² = 1 (a>b>0)。"}],
    },
]


async def main():
    grounded_count = 0
    latex_valid_count = 0
    scores = []

    for i, case in enumerate(TEST_CASES, 1):
        is_grounded, score, status = await hallucination_checker.verify_grounding(
            case["answer"], case["retrieved_docs"]
        )
        latex_ok = BenchmarkHarness.validate_latex_syntax(case["answer"])["is_valid"]
        if is_grounded:
            grounded_count += 1
        if latex_ok:
            latex_valid_count += 1
        scores.append(score)
        print(f"[{i}] grounded={is_grounded} score={score:.4f} latex_ok={latex_ok} | {status}")

    n = len(TEST_CASES)
    grounding_pass_rate = grounded_count / n
    latex_valid_rate = latex_valid_count / n
    avg_grounding_score = sum(scores) / n

    print()
    print(f"=== 评测结果 (n={n}) ===")
    print(f"Grounding 通过率: {grounding_pass_rate*100:.1f}% ({grounded_count}/{n})")
    print(f"LaTeX 闭合率: {latex_valid_rate*100:.1f}% ({latex_valid_count}/{n})")
    print(f"平均 grounding_score: {avg_grounding_score:.4f}")


asyncio.run(main())
