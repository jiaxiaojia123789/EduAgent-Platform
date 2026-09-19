"""
结构化输出约束层 (Structured Output Constraint)
================================================
针对教育场景对输出「可控性与格式一致性」的要求，提供三层保障：

1. Prompt 层：将 Pydantic 模型的 JSON Schema 与字段语义说明动态注入系统提示，
   配合各智能体的 Role Prompting 约束模型输出格式；
2. API 层：通过 DashScope OpenAI 兼容协议的 response_format={"type": "json_object"}
   强制 JSON 模式（若目标模型不支持则自动降级为纯 Prompt 约束）；
3. 校验层：Pydantic 严格校验，解析/校验失败时将错误反馈回传给模型自动重试一次。

失败兜底约定：结构化解析最终失败时返回 (None, meta)，由调用方降级为自由文本路径，
保证「结构化增强失败不阻断业务主流程」。
"""
import json
import re
import time
import logging
from typing import Dict, Any, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.services.llm.bailian_client import bailian_client
from app.services.llm.prompt_metrics import prompt_metrics

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# 输出格式指令：只允许纯 JSON，禁止围栏/解释/前后缀
_JSON_ONLY_INSTRUCTION = (
    "\n\n【输出格式硬约束】\n"
    "你的最终回复必须是且仅是一个符合下方 JSON Schema 的 JSON 对象：\n"
    "1. 禁止输出 Markdown 代码围栏（```）、解释性文字、注释或任何 JSON 以外的字符；\n"
    "2. 所有字段必须出现且类型严格匹配；列表字段不得为空（除非 Schema 标注可缺省）；\n"
    "3. 中文内容使用简体中文；理科公式一律使用标准 LaTeX 语法（$...$ 或 $$...$$）；\n"
    "4. 字符串内如需换行请使用 \\n 转义，保证 JSON 可被 json.loads 直接解析。\n"
    "目标 JSON Schema：\n"
)

# 二次结构化抽取指令：把自由文本(markdown)转换为符合 Schema 的 JSON
_EXTRACT_INSTRUCTION = (
    "你是一名严格的信息抽取引擎。请把用户给出的文档内容无损转换为符合下方 JSON Schema 的 JSON 对象。\n"
    "要求：忠实原文，不编造缺失信息（无法确定时用空字符串或合理默认值）；"
    "只输出纯 JSON 对象，禁止代码围栏与解释。\n"
    "目标 JSON Schema：\n"
)

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json_payload(text: str) -> str:
    """从模型输出中提取纯 JSON 字符串：剥除代码围栏，截取首尾大括号之间内容。"""
    fence = _FENCE_RE.search(text)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        return text[start : end + 1]
    return text.strip()


def _schema_summary(schema: Type[BaseModel]) -> str:
    """JSON Schema（含 $defs 展开提示）压缩为紧凑字符串，控制注入 token 开销。"""
    return json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))


def _with_format_instruction(messages: List[Dict[str, str]], schema: Type[BaseModel], prefix: str) -> List[Dict[str, str]]:
    """把格式硬约束追加进系统消息（不修改调用方传入的列表）。"""
    instruction = f"{prefix}{_schema_summary(schema)}"
    convo = [dict(m) for m in messages]
    if convo and convo[0].get("role") == "system":
        convo[0]["content"] = convo[0]["content"] + instruction
    else:
        convo.insert(0, {"role": "system", "content": instruction})
    return convo


async def structured_acomplete(
    messages: List[Dict[str, str]],
    schema: Type[T],
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 8192,
    max_retries: int = 1,
    use_api_json_mode: bool = True,
    prompt_id: Optional[str] = None,
    agent: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Tuple[Optional[T], Dict[str, Any]]:
    """
    带结构化输出约束的补全：返回 (schema 实例, meta)。
    解析/校验失败自动带错误反馈重试 max_retries 次，仍失败返回 (None, meta)。
    每次调用的解析结果/尝试次数/耗时自动写入 prompt_metrics（评估闭环数据源）。
    """
    meta: Dict[str, Any] = {
        "schema": schema.__name__,
        "attempts": 0,
        "parse_ok": False,
        "retried": False,
        "api_json_mode": False,
        "model": model,
        "last_error": None,
    }
    convo = _with_format_instruction(messages, schema, _JSON_ONLY_INSTRUCTION)

    last_raw = ""
    started = time.perf_counter()
    try:
        for attempt in range(1 + max_retries):
            meta["attempts"] = attempt + 1
            if attempt > 0:
                meta["retried"] = True
            try:
                resp = await bailian_client.acomplete(
                    convo,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"} if use_api_json_mode else None,
                )
                meta["api_json_mode"] = use_api_json_mode
            except RuntimeError as e:
                # 仅当错误确因 response_format 不被支持时降级为纯 Prompt 约束重试；
                # 403 配额耗尽 / 401 鉴权失败等账户级错误不重试，直接抛出
                if use_api_json_mode and "response_format" in str(e):
                    logger.warning(f"[Structured] response_format 被拒绝，降级为 Prompt 约束: {str(e)[:120]}")
                    meta["api_json_mode"] = False
                    resp = await bailian_client.acomplete(
                        convo, model=model, temperature=temperature, max_tokens=max_tokens,
                    )
                else:
                    raise

            meta["model"] = resp.get("model")
            last_raw = resp.get("content") or ""
            payload = extract_json_payload(last_raw)

            try:
                data = json.loads(payload)
                obj = schema.model_validate(data)
                meta["parse_ok"] = True
                meta["last_error"] = None
                return obj, meta
            except json.JSONDecodeError as e:
                err = f"JSONDecodeError: {e}; raw[:200]={last_raw[:200]!r}"
            except ValidationError as e:
                err = f"ValidationError: {str(e)[:400]}"

            logger.warning(f"[Structured] {schema.__name__} 第 {attempt + 1} 次输出未通过校验 -> {err[:160]}")
            meta["last_error"] = err[:500]

            if attempt < max_retries:
                # 错误反馈回传：让模型针对具体校验错误自修复
                convo.append({"role": "assistant", "content": last_raw[:2000]})
                convo.append({
                    "role": "user",
                    "content": (
                        f"上一次输出未通过校验：{err[:400]}\n"
                        f"请严格按目标 JSON Schema 重新输出，只返回纯 JSON 对象，"
                        f"不要包含任何解释或代码围栏。"
                    ),
                })

        return None, meta
    finally:
        prompt_metrics.record_call(
            prompt_id=prompt_id,
            schema_name=schema.__name__,
            parse_ok=meta["parse_ok"],
            attempts=meta["attempts"],
            retried=meta["retried"],
            api_json_mode=meta["api_json_mode"],
            model=meta.get("model"),
            latency_ms=(time.perf_counter() - started) * 1000,
            agent=agent,
            session_id=session_id,
            user_id=user_id,
            error=meta.get("last_error"),
        )


async def aextract_structured(
    free_text: str,
    schema: Type[T],
    model: Optional[str] = None,
    temperature: float = 0.1,
    prompt_id: Optional[str] = None,
    agent: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Tuple[Optional[T], Dict[str, Any]]:
    """
    二次结构化抽取：把已生成的自由文本（如流式产出的 markdown）转换为 Schema 实例。
    典型场景：SSE 打字机流式路径先保体验输出 markdown，再由轻量模型抽取结构化 artifact。
    """
    messages = [
        {"role": "user", "content": free_text[:12000]},
    ]
    convo = _with_format_instruction(messages, schema, _EXTRACT_INSTRUCTION)
    return await structured_acomplete(
        convo, schema, model=model, temperature=temperature, max_tokens=4096, max_retries=1,
        prompt_id=prompt_id, agent=agent, session_id=session_id, user_id=user_id,
    )
