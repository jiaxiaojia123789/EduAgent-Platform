"""
LangGraph A/B 实验框架
========================
让同一意图可挂不同 prompt/agent 组合，按 conversation_id 分桶分配变体。

设计：
- ExperimentConfig：实验定义（实验名、变体列表、分桶比例、目标 agent/prompt）
- ExperimentRouter：按 conversation_id 哈希分桶，稳定命中（同一会话始终走同一变体）
- 实验结果记录：每个变体的成功/失败/质量分/延迟，便于后续分析

适用场景：
- 不同 prompt 版本对比（A：详细指令 vs B：精简指令）
- 不同 agent 组合对比（A：lesson_plan 单独 vs B：lesson_plan + rubric 双 agent）
- 不同 LLM 模型对比（A：qwen-max vs B：qwen-turbo）
- 不同温度参数对比（A：T=0.3 vs B：T=0.7）

使用方式：
    # 1. 注册实验（在应用启动时）
    from app.services.agent.ab_experiment import experiment_registry
    experiment_registry.register(ExperimentConfig(
        name="lesson_plan_prompt_v2",
        variants=[
            Variant(name="control", prompt_id="lesson_plan_v1", weight=50),
            Variant(name="treatment", prompt_id="lesson_plan_v2", weight=50),
        ],
        target_agent="lesson_plan",
    ))

    # 2. 节点入口处分流
    variant = experiment_registry.assign("lesson_plan_prompt_v2", conversation_id)
    if variant:
        prompt_id = variant.prompt_id  # 用实验变体的 prompt
    else:
        prompt_id = "lesson_plan_v1"  # 默认

    # 3. 实验结果记录
    experiment_registry.record_result(
        experiment="lesson_plan_prompt_v2",
        variant="treatment",
        conversation_id=conversation_id,
        success=True,
        latency_ms=1200,
        quality_score=0.85,
    )
"""
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Variant:
    """单个实验变体。"""
    name: str
    prompt_id: Optional[str] = None     # 使用的 prompt 模板 ID
    agent: Optional[str] = None         # 使用的 agent 类型
    model: Optional[str] = None         # 使用的 LLM 模型
    temperature: Optional[float] = None  # 温度参数
    weight: int = 100                   # 权重（百分比，所有变体 weight 之和应 = 100）
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExperimentConfig:
    """实验定义。"""
    name: str
    variants: List[Variant]
    target_agent: Optional[str] = None  # 实验作用的 agent 节点
    enabled: bool = True
    description: str = ""


@dataclass
class VariantAssignment:
    """会话到变体的稳定分配结果。"""
    experiment: str
    variant: Variant
    bucket: int  # 0-99


class ExperimentRegistry:
    """
    实验注册中心：
    - register：注册实验
    - assign：按 conversation_id 哈希分桶，稳定命中
    - record_result：记录变体执行结果
    - stats：按实验聚合统计
    """

    def __init__(self):
        self._experiments: Dict[str, ExperimentConfig] = {}
        # 结果记录：{experiment: {variant: [{...}, ...]}}
        self._results: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    def register(self, config: ExperimentConfig) -> None:
        """注册或更新实验。"""
        if not config.variants:
            raise ValueError(f"实验 [{config.name}] 必须至少有 1 个变体")
        total_weight = sum(v.weight for v in config.variants)
        if total_weight != 100:
            logger.warning(
                f"实验 [{config.name}] 变体权重之和 {total_weight} != 100，"
                "分桶将按相对权重归一化"
            )
        self._experiments[config.name] = config
        self._results.setdefault(config.name, {v.name: [] for v in config.variants})
        logger.info(
            f"experiment_registered name={config.name} variants={[v.name for v in config.variants]}"
        )

    def get(self, name: str) -> Optional[ExperimentConfig]:
        return self._experiments.get(name)

    def assign(
        self,
        experiment_name: str,
        conversation_id: str,
    ) -> Optional[VariantAssignment]:
        """
        按 conversation_id 哈希分桶，返回分配到的变体。
        同一 conversation_id 始终命中同一变体（稳定分配）。
        """
        config = self._experiments.get(experiment_name)
        if not config or not config.enabled:
            return None

        # SHA256 哈希 conversation_id + experiment_name，取模 100
        key = f"{experiment_name}:{conversation_id}"
        h = hashlib.sha256(key.encode("utf-8")).hexdigest()
        bucket = int(h[:8], 16) % 100  # 0-99

        # 按权重累积分布选变体
        cumulative = 0
        total_weight = sum(v.weight for v in config.variants)
        for variant in config.variants:
            cumulative += variant.weight * 100 // total_weight
            if bucket < cumulative:
                return VariantAssignment(
                    experiment=experiment_name,
                    variant=variant,
                    bucket=bucket,
                )
        # 兜底：返回最后一个变体（bucket 落在边界外）
        return VariantAssignment(
            experiment=experiment_name,
            variant=config.variants[-1],
            bucket=bucket,
        )

    def record_result(
        self,
        experiment: str,
        variant: str,
        conversation_id: str,
        success: bool,
        latency_ms: Optional[float] = None,
        quality_score: Optional[float] = None,
        tokens: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """记录单次变体执行结果（best-effort，失败不阻断主流程）。"""
        if experiment not in self._results:
            self._results[experiment] = {}
        if variant not in self._results[experiment]:
            self._results[experiment][variant] = []

        self._results[experiment][variant].append({
            "conversation_id": conversation_id,
            "success": success,
            "latency_ms": latency_ms,
            "quality_score": quality_score,
            "tokens": tokens,
            "error": (error or "")[:200] if error else None,
        })

    def stats(self, experiment: Optional[str] = None) -> Dict[str, Any]:
        """聚合统计：按实验+变体维度的成功率/平均延迟/质量分。"""
        experiments = [experiment] if experiment else list(self._results.keys())
        result: Dict[str, Any] = {}

        for exp_name in experiments:
            exp_data = self._results.get(exp_name, {})
            exp_stats: Dict[str, Any] = {}
            for variant_name, records in exp_data.items():
                if not records:
                    exp_stats[variant_name] = {"sample_size": 0}
                    continue
                total = len(records)
                successes = sum(1 for r in records if r["success"])
                latencies = [r["latency_ms"] for r in records if r["latency_ms"]]
                qualities = [r["quality_score"] for r in records if r["quality_score"]]
                tokens = [r["tokens"] for r in records if r["tokens"]]

                exp_stats[variant_name] = {
                    "sample_size": total,
                    "success_rate": round(successes / total, 4),
                    "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
                    "avg_quality_score": round(sum(qualities) / len(qualities), 4) if qualities else None,
                    "total_tokens": sum(tokens),
                }
            result[exp_name] = exp_stats

        return result

    def reset(self, experiment: Optional[str] = None) -> None:
        """清空实验结果（运维场景）。"""
        if experiment:
            self._results[experiment] = {
                v.name: [] for v in self._experiments[experiment].variants
            }
        else:
            for exp_name in self._results:
                self._results[exp_name] = {
                    v.name: []
                    for v in self._experiments.get(exp_name, ExperimentConfig("", [])).variants
                }


# 单例
experiment_registry = ExperimentRegistry()


def auto_register_default_experiments() -> None:
    """
    应用启动时调用，注册项目内置实验。

    生产环境可改为从 YAML/DB 加载，便于运维动态调整。
    """
    # 实验 1：教案 prompt 精简版 vs 详细版
    if "lesson_plan_prompt_concise" not in experiment_registry._experiments:
        experiment_registry.register(ExperimentConfig(
            name="lesson_plan_prompt_concise",
            description="对比精简 vs 详细指令对教案质量的影响",
            target_agent="lesson_plan",
            variants=[
                Variant(name="control", prompt_id="lesson_plan_v1", weight=50),
                Variant(name="treatment", prompt_id="lesson_plan_v1_concise", weight=50),
            ],
            enabled=False,  # 默认关闭，避免生产意外分流
        ))

    # 实验 2：双 agent 协作 vs 单 agent
    if "multi_agent_vs_single" not in experiment_registry._experiments:
        experiment_registry.register(ExperimentConfig(
            name="multi_agent_vs_single",
            description="双 agent（lesson_plan + rubric）vs 单 lesson_plan",
            target_agent="lesson_plan",
            variants=[
                Variant(name="single", agent="lesson_plan", weight=50),
                Variant(name="multi", agent="lesson_plan_with_rubric", weight=50),
            ],
            enabled=False,
        ))

    logger.info("default_experiments_registered count=2")
