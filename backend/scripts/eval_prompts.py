"""
Prompt 评估脚本 (Prompt Iteration & Evaluation)
================================================
对 3 个结构化 Prompt 能力（教案 / 组卷 / 量规批阅）跑固定测试集，
输出 JSON 合法率、重试率、API JSON 模式命中率与耗时对比，
作为 Prompt 迭代优化的量化基线。每次调用的明细同时持久化到
backend/data/prompt_metrics.db（与线上生产调用同库，可纵向对比）。

用法（在 backend 目录下）：
    python scripts/eval_prompts.py                # 每个用例跑 1 轮
    python scripts/eval_prompts.py --runs 3       # 每个用例跑 3 轮取均值
    python scripts/eval_prompts.py --cases lesson_plan exam_quiz

说明：未配置 DASHSCOPE_API_KEY 时 bailian_client 进入 Simulation 模式，
返回非 JSON 模拟文本，可完整验证「校验失败 -> 错误反馈重试 -> 指标落库」链路，
但 parse_ok 预期为 0；真实评估请配置 API Key 后运行。
"""
import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Type

from pydantic import BaseModel

# 脚本位于 backend/scripts/，把 backend 根目录加入 sys.path
BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.lesson_plan import LessonPlanStructured  # noqa: E402
from app.schemas.exam_quiz import ExamPaperStructured  # noqa: E402
from app.schemas.rubric_grading import RubricReportStructured  # noqa: E402
from app.services.llm.router import ModelRouter  # noqa: E402
from app.services.llm.structured import structured_acomplete  # noqa: E402
from app.services.llm.prompt_router import prompt_router  # noqa: E402
from app.services.llm.prompt_metrics import prompt_metrics  # noqa: E402
from app.services.llm.bailian_client import bailian_client  # noqa: E402

EVAL_USER_ID = "u-001"  # 演示教师账号，触发画像适配注入

# 固定测试集：与三个智能体生产调用点同构（同一 prompt_id / 同一模型路由）
CASES: List[Dict[str, Any]] = [
    {
        "case": "lesson_plan",
        "prompt_id": "lesson_plan.system",
        "schema": LessonPlanStructured,
        "agent": "lesson_plan",
        "user_prompt": "设计一节《导数的几何意义》教案，面向高二实验班，要求用割线逼近切线的几何直观引入，包含含参分类讨论的易错点变式。",
    },
    {
        "case": "exam_quiz",
        "prompt_id": "exam_quiz.system",
        "schema": ExamPaperStructured,
        "agent": "exam_quiz",
        "user_prompt": "命制一份高二数学期中测试卷（100 分 / 90 分钟），覆盖导数及其应用，解答题压轴题需含参数分类讨论并附评分细则。",
    },
    {
        "case": "rubric_grading",
        "prompt_id": "rubric.system",
        "schema": RubricReportStructured,
        "agent": "rubric_grading",
        "user_prompt": (
            "请按量规批阅以下学生作文片段（议论文《说勤》，约 600 字）："
            "开头引用韩愈『业精于勤荒于嬉』，主体举苏秦刺股、匡衡凿壁两例，"
            "但论证停留在例证罗列，未分析『勤』与『精』的因果链条，结尾口号化收束。"
        ),
    },
]


async def run_case(case: Dict[str, Any], runs: int) -> Dict[str, Any]:
    """单用例跑 runs 轮，返回聚合指标。"""
    schema: Type[BaseModel] = case["schema"]
    latencies: List[float] = []
    attempts_list: List[int] = []
    parse_ok_count = 0
    api_json_hits = 0
    sample: Dict[str, Any] = {}

    for _ in range(runs):
        messages = [
            {"role": "system", "content": prompt_router.build_system(
                case["prompt_id"], case["user_prompt"], user_id=EVAL_USER_ID)},
            {"role": "user", "content": case["user_prompt"]},
        ]
        started = time.perf_counter()
        obj, meta = await structured_acomplete(
            messages, schema,
            model=ModelRouter.route_model(case["agent"], len(case["user_prompt"])),
            prompt_id=case["prompt_id"], agent=case["agent"],
            session_id="eval-script", user_id=EVAL_USER_ID,
        )
        latencies.append((time.perf_counter() - started) * 1000)
        attempts_list.append(meta["attempts"])
        parse_ok_count += 1 if meta["parse_ok"] else 0
        api_json_hits += 1 if meta["api_json_mode"] else 0

        # 成功时抽查关键字段非空率，作为内容质量粗校验
        if obj is not None and not sample:
            sample = _spot_check(case["case"], obj)

    retried_count = sum(1 for a in attempts_list if a > 1)
    return {
        "case": case["case"],
        "prompt_id": case["prompt_id"],
        "runs": runs,
        "parse_ok_rate": parse_ok_count / runs,
        "retry_rate": retried_count / runs,
        "api_json_mode_rate": api_json_hits / runs,
        "avg_attempts": round(statistics.mean(attempts_list), 2),
        "avg_latency_ms": round(statistics.mean(latencies), 1),
        "spot_check": sample,
    }


def _spot_check(case: str, obj: BaseModel) -> Dict[str, Any]:
    """成功样本的最小内容抽查：核心列表字段非空即视为内容有效。"""
    data = obj.model_dump()
    if case == "lesson_plan":
        return {
            "title": data.get("title", ""),
            "teaching_steps": len(data.get("teaching_steps") or []),
            "objectives": len(data.get("teaching_objectives") or []),
        }
    if case == "exam_quiz":
        return {
            "paper_title": data.get("paper_title", ""),
            "questions": len(data.get("questions") or []),
            "total_score": data.get("total_score"),
        }
    return {
        "overall_score": data.get("overall_score"),
        "dimensions": len(data.get("dimensions") or []),
        "suggestions": len(data.get("improvement_suggestions") or []),
    }


def print_report(results: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 96)
    print(f"{'case':<16}{'parse_ok率':<12}{'重试率':<10}{'API_JSON率':<12}"
          f"{'均尝试':<8}{'均耗时(ms)':<12}{'抽查'}")
    print("-" * 96)
    for r in results:
        spot = ", ".join(f"{k}={v}" for k, v in r["spot_check"].items()) if r["spot_check"] else "-"
        print(
            f"{r['case']:<16}{r['parse_ok_rate']:<12.0%}{r['retry_rate']:<10.0%}"
            f"{r['api_json_mode_rate']:<12.0%}{r['avg_attempts']:<8}"
            f"{r['avg_latency_ms']:<12}{spot}"
        )
    print("=" * 96)

    # 持久化指标库的近期视图（含生产真实流量，可与本轮评估纵向对比）
    stats = prompt_metrics.stats(limit=10)
    recent = stats.get("recent") or []
    if recent:
        print("\n[prompt_metrics.db] 最近调用记录（含本轮评估写入）：")
        for row in recent[:10]:
            print(
                f"  {row.get('prompt_id') or '-':<22} ok={row.get('parse_ok')} "
                f"attempts={row.get('attempts')} latency={row.get('latency_ms')}ms"
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Prompt 结构化输出评估脚本")
    parser.add_argument("--runs", type=int, default=1, help="每个用例运行轮数（默认 1）")
    parser.add_argument("--cases", nargs="*", default=None,
                        help="仅运行指定用例：lesson_plan exam_quiz rubric_grading")
    args = parser.parse_args()

    selected = CASES
    if args.cases:
        selected = [c for c in CASES if c["case"] in args.cases]
        if not selected:
            print(f"未找到用例：{args.cases}，可选：{[c['case'] for c in CASES]}")
            sys.exit(2)

    mode = "Simulation(未配置 API Key，parse_ok 预期为 0)" if bailian_client.is_mock else "Live(DashScope)"
    print(f"[eval_prompts] 模式: {mode} | 用例: {[c['case'] for c in selected]} | 轮数: {args.runs}")

    results = [await run_case(c, args.runs) for c in selected]
    print_report(results)

    all_ok = all(r["parse_ok_rate"] == 1.0 for r in results)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
