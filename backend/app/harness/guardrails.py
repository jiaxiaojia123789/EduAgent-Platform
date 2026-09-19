import re
from typing import Tuple, List


class GuardrailViolationError(Exception):
    """Raised when an input or output violates educational safety guardrails."""
    pass


class SafetyGuardrails:
    """
    Agent Harness: Safety & Pedagogical Guardrails
    1. Input Prompt Injection Detection
    2. PII (Personally Identifiable Information) Sanitization
    3. Pedagogical Policy & Value Alignment Check
    """

    # Common injection keywords in academic / agent domains
    INJECTION_PATTERNS = [
        r"ignore\s+(all\s+)?(previous|above)\s+instructions",
        r"disregard\s+(all\s+)?(previous|above)\s+prompts",
        r"you\s+are\s+now\s+(DAN|unfiltered|jailbroken)",
        r"system\s+prompt\s+reveal",
        r"print\s+(your\s+)?initial\s+prompt",
        r"无视前面(所有)?指令",
        r"泄露(你的)?系统提示词",
        r"绕过(所有)?限制",
    ]

    # Sensitive PII patterns
    PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
    ID_CARD_PATTERN = re.compile(r"\b\d{17}[\dXx]\b")
    EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

    # Prohibited toxic / unpedagogical expressions
    UNPEDAGOGICAL_PATTERNS = [
        r"作弊(方法|手段|包过)",
        r"代考|替考|买卖论文",
        r"黑客攻击学校教务系统",
    ]

    @classmethod
    def check_input_safety(cls, text: str) -> Tuple[bool, str]:
        """Validates incoming user prompt for injection and educational safety."""
        for pattern in cls.INJECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return False, f"检测到疑似越狱或提示词注入风险行为 (匹配规则: {pattern})"

        for pattern in cls.UNPEDAGOGICAL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return False, f"请求内容违背教育诚信与国家法律法规 (匹配规则: {pattern})"

        return True, "Input check passed"

    @classmethod
    def sanitize_pii(cls, text: str) -> str:
        """Sanitizes sensitive information like phone numbers and ID cards."""
        text = cls.PHONE_PATTERN.sub("[已脱敏手机号]", text)
        text = cls.ID_CARD_PATTERN.sub("[已脱敏身份证]", text)
        return text

    @classmethod
    def validate_output_compliance(cls, text: str) -> Tuple[bool, str]:
        """Validates generated agent response against pedagogical standards."""
        for pattern in cls.UNPEDAGOGICAL_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return False, "模型生成内容触发教育价值观合规告警"
        return True, "Output check passed"
