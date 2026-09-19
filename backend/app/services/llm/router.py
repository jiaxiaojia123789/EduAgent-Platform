from typing import Optional
from app.core.config import settings


class ModelTier:
    HEAVY = "HEAVY"          # qwen-max: For complex reasoning, long lesson plans, academic literature
    STANDARD = "STANDARD"    # qwen-plus: For general classroom QA, quiz explanation, socratic tutor
    LIGHT = "LIGHT"          # qwen-turbo: For intent classification, HyDE query rewriting, fast JSON parsing


class ModelRouter:
    """
    Intelligent Dynamic Model Router for Alibaba Bailian
    Balances latency, output quality, and token cost.
    """

    AGENT_TIER_MAPPING = {
        "supervisor": ModelTier.HEAVY,
        "lesson_plan": ModelTier.HEAVY,
        "academic_rag": ModelTier.HEAVY,
        "math_solver": ModelTier.HEAVY,
        "exam_quiz": ModelTier.STANDARD,
        "socratic": ModelTier.STANDARD,
        "curriculum": ModelTier.STANDARD,
        "rubric": ModelTier.STANDARD,
        "slide_outline": ModelTier.STANDARD,
        "code_grader": ModelTier.HEAVY,
        "intent_classifier": ModelTier.LIGHT,
        "hyde_generator": ModelTier.LIGHT,
    }

    @classmethod
    def route_model(cls, agent_type: str, prompt_length: int = 0) -> str:
        """Determines the most cost-effective and capable model for the task."""
        tier = cls.AGENT_TIER_MAPPING.get(agent_type, ModelTier.STANDARD)

        # Upgrade to heavy if context is extremely long (>4000 chars)
        if prompt_length > 4000:
            return settings.ROUTER_HEAVY_MODEL

        if tier == ModelTier.HEAVY:
            return settings.ROUTER_HEAVY_MODEL
        elif tier == ModelTier.LIGHT:
            return settings.ROUTER_LIGHT_MODEL
        else:
            return settings.DEFAULT_LLM_MODEL
