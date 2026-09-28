"""
LLM 供应商抽象层
================
避免锁死 DashScope：单一接口，多供应商热切换。

设计：
- LLMProvider 抽象基类：acomplete / astream / get_embedding
- 具体适配器：
  - DashScopeProvider（已有 bailian_client 包装，默认）
  - OpenAICompatibleProvider（兼容 OpenAI/Anthropic/vLLM/Together/Azure 等）
  - MockLLMProvider（CI/无密钥环境兜底）
- ProviderRouter：按优先级路由 + 故障转移（与 P3 断路器联动）
  - 健康供应商优先；触发断路器 OPEN → 跳过；全部故障 → 抛 RuntimeError
- 配置：LLM_PROVIDERS 环境变量（YAML/JSON 风格），列表 of provider 配置

使用方式（业务代码无感知）：
    from app.services.llm.provider import llm_router
    result = await llm_router.acomplete(messages, model="qwen-max")

切换供应商只需改 .env：
    LLM_PROVIDERS=dashscope,openai_fallback
    OPENAI_FALLBACK_API_KEY=sk-...
    OPENAI_FALLBACK_BASE_URL=https://api.openai.com/v1
"""
import asyncio
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.harness.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)


# ============================================================
# Provider 抽象基类
# ============================================================

class LLMProvider(ABC):
    """所有 LLM 供应商必须实现的接口。"""

    name: str = "abstract"

    @abstractmethod
    async def acomplete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """非流式补全。返回 {content, usage, model, ...}。"""
        raise NotImplementedError

    @abstractmethod
    async def astream(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        on_token=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """流式补全，token 通过 on_token 回调推送，返回最终 result。"""
        raise NotImplementedError

    @abstractmethod
    async def get_embedding(self, text: str, model: Optional[str] = None) -> List[float]:
        """文本向量。"""
        raise NotImplementedError

    async def health_check(self) -> bool:
        """健康探测：是否可用（默认 True，子类可重写做 ping）。"""
        return True


# ============================================================
# DashScope 适配器（包装已有 BailianLLMClient）
# ============================================================

class DashScopeProvider(LLMProvider):
    """阿里云百炼 DashScope 适配器，包装已有 BailianLLMClient。"""

    name = "dashscope"

    def __init__(self):
        from app.services.llm.bailian_client import BailianLLMClient
        self._client = BailianLLMClient()

    async def acomplete(self, messages, model, temperature=0.3, max_tokens=4096,
                        response_format=None, **kwargs):
        return await self._client.acomplete(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            enable_thinking=kwargs.get("enable_thinking", False),
            no_cache=kwargs.get("no_cache", False),
        )

    async def astream(self, messages, model, temperature=0.3, max_tokens=4096,
                      on_token=None, **kwargs):
        return await self._client.astream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            on_token=on_token,
            enable_thinking=kwargs.get("enable_thinking", False),
        )

    async def get_embedding(self, text, model=None):
        return await self._client.get_embedding(text, model=model)

    async def health_check(self) -> bool:
        # 无 API key 视为不可用（CI/dev 环境）
        return bool(settings.DASHSCOPE_API_KEY)


# ============================================================
# OpenAI 兼容供应商（支持 OpenAI / Anthropic / vLLM / Together 等）
# ============================================================

class OpenAICompatibleProvider(LLMProvider):
    """
    兼容 OpenAI Chat Completions API 的通用供应商。
    支持：OpenAI / Azure OpenAI / Anthropic(via openai proxy) / vLLM / Together / Mistral
    """

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        default_model: str,
        embedding_model: Optional[str] = None,
        timeout: int = 90,
    ):
        self.name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._embedding_model = embedding_model
        self._timeout = timeout
        self._client = None  # 延迟初始化 httpx

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        return self._client

    async def acomplete(self, messages, model, temperature=0.3, max_tokens=4096,
                        response_format=None, **kwargs):
        client = self._get_client()
        payload = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        resp = await client.post("/chat/completions", json=payload)
        if resp.status_code != 200:
            raise RuntimeError(
                f"[{self.name}] acomplete failed: {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        choice = data["choices"][0]
        return {
            "content": choice["message"]["content"],
            "model": data.get("model", payload["model"]),
            "usage": data.get("usage", {}),
        }

    async def astream(self, messages, model, temperature=0.3, max_tokens=4096,
                      on_token=None, **kwargs):
        client = self._get_client()
        payload = {
            "model": model or self._default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        async with client.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(
                    f"[{self.name}] astream failed: {resp.status_code} {body[:300]}"
                )
            full_content = []
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                import json
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    token = delta.get("content", "")
                    if token and on_token:
                        ret = on_token(token)
                        if asyncio.iscoroutine(ret):
                            await ret
                        full_content.append(token)
                except Exception:
                    continue

        return {
            "content": "".join(full_content),
            "model": payload["model"],
            "usage": {},  # 流式不返回 usage
        }

    async def get_embedding(self, text, model=None):
        if not self._embedding_model:
            raise RuntimeError(f"[{self.name}] 未配置 embedding 模型")
        client = self._get_client()
        resp = await client.post(
            "/embeddings",
            json={"input": text, "model": model or self._embedding_model},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"[{self.name}] embedding failed: {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()
        return data["data"][0]["embedding"]

    async def health_check(self) -> bool:
        return bool(self._api_key and self._base_url)


# ============================================================
# Mock Provider（CI / 无密钥环境兜底）
# ============================================================

class MockLLMProvider(LLMProvider):
    """CI/无密钥环境兜底，返回固定文本。"""

    name = "mock"

    async def acomplete(self, messages, model, temperature=0.3, max_tokens=4096,
                        response_format=None, **kwargs):
        return {
            "content": "[MockLLM] this is a stub response for CI/dev environment.",
            "model": model or "mock-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }

    async def astream(self, messages, model, temperature=0.3, max_tokens=4096,
                      on_token=None, **kwargs):
        stub_text = "[MockLLM] streaming stub response."
        if on_token:
            for chunk in [stub_text[i:i+8] for i in range(0, len(stub_text), 8)]:
                ret = on_token(chunk)
                if asyncio.iscoroutine(ret):
                    await ret
                await asyncio.sleep(0.01)
        return {
            "content": stub_text,
            "model": model or "mock-model",
            "usage": {},
        }

    async def get_embedding(self, text, model=None):
        # 返回固定 1024 维向量（与项目默认对齐）
        return [0.01] * 1024

    async def health_check(self) -> bool:
        return True


# ============================================================
# Provider Router（优先级路由 + 故障转移）
# ============================================================

@dataclass
class ProviderRoute:
    """单条路由配置：供应商 + 优先级 + 断路器。"""
    provider: LLMProvider
    priority: int  # 数字越小优先级越高
    breaker: CircuitBreaker = field(init=False)

    def __post_init__(self):
        self.breaker = CircuitBreaker(
            name=f"llm_{self.provider.name}",
            failure_threshold=5,
            cooldown_seconds=30,
        )


class ProviderRouter:
    """
    按优先级路由 LLM 调用：
    1. 取所有健康 + 未熔断的供应商，按 priority 升序排
    2. 依次尝试调用，成功返回，失败记录并尝试下一个
    3. 全部失败 → 抛 RuntimeError
    4. 自动跳过 OPEN 状态的供应商（断路器保护）
    """

    def __init__(self, routes: List[ProviderRoute]):
        self._routes = sorted(routes, key=lambda r: r.priority)
        self._initialized = bool(routes)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def _candidates(self) -> List[ProviderRoute]:
        """过滤掉断路器 OPEN 的供应商。"""
        return [r for r in self._routes if not r.breaker.is_open]

    async def acomplete(self, messages, model, **kwargs) -> Dict[str, Any]:
        candidates = self._candidates()
        if not candidates:
            raise RuntimeError(
                "所有 LLM 供应商均熔断或不可用，请稍后重试或检查健康状态"
            )
        last_error = None
        for route in candidates:
            try:
                async with route.breaker.protect():
                    result = await route.provider.acomplete(messages, model, **kwargs)
                result.setdefault("provider", route.provider.name)
                return result
            except CircuitBreakerOpenError:
                continue
            except Exception as e:
                route.breaker.record_failure()
                last_error = e
                logger.warning(
                    f"[ProviderRouter] {route.provider.name} acomplete failed, try next: {e}"
                )
        raise RuntimeError(f"所有供应商均失败，最后错误: {last_error}")

    async def astream(self, messages, model, on_token=None, **kwargs) -> Dict[str, Any]:
        candidates = self._candidates()
        if not candidates:
            raise RuntimeError("所有 LLM 供应商均熔断，无法流式调用")
        last_error = None
        for route in candidates:
            try:
                async with route.breaker.protect():
                    result = await route.provider.astream(
                        messages, model, on_token=on_token, **kwargs
                    )
                result.setdefault("provider", route.provider.name)
                return result
            except CircuitBreakerOpenError:
                continue
            except Exception as e:
                route.breaker.record_failure()
                last_error = e
                logger.warning(
                    f"[ProviderRouter] {route.provider.name} astream failed, try next: {e}"
                )
        raise RuntimeError(f"所有供应商流式调用均失败: {last_error}")

    async def get_embedding(self, text, model=None) -> List[float]:
        candidates = self._candidates()
        if not candidates:
            raise RuntimeError("所有 LLM 供应商均熔断，无法生成 embedding")
        last_error = None
        for route in candidates:
            try:
                # embedding 不走断路器（出错的概率低，且 embedding 失败影响小）
                return await route.provider.get_embedding(text, model=model)
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[ProviderRouter] {route.provider.name} embedding failed, try next: {e}"
                )
        raise RuntimeError(f"所有供应商 embedding 均失败: {last_error}")

    def stats(self) -> List[Dict[str, Any]]:
        """暴露所有路由的断路器状态给 /metrics。"""
        return [
            {
                "provider": r.provider.name,
                "priority": r.priority,
                **r.breaker.stats,
            }
            for r in self._routes
        ]


# ============================================================
# 单例工厂：从环境变量构造路由
# ============================================================

_llm_router: Optional[ProviderRouter] = None


def _build_routes_from_env() -> List[ProviderRoute]:
    """
    从 LLM_PROVIDERS 环境变量解析供应商列表。

    格式：逗号分隔的 provider 名，按出现顺序作为优先级。
    例：LLM_PROVIDERS=dashscope,openai_fallback
    对应环境变量：OPENAI_FALLBACK_API_KEY / OPENAI_FALLBACK_BASE_URL / OPENAI_FALLBACK_DEFAULT_MODEL
    """
    provider_names_str = os.environ.get("LLM_PROVIDERS", "dashscope")
    names = [n.strip() for n in provider_names_str.split(",") if n.strip()]
    routes: List[ProviderRoute] = []

    for idx, name in enumerate(names):
        name_upper = name.upper()
        priority = idx + 1

        if name == "dashscope":
            provider = DashScopeProvider()
            if not settings.DASHSCOPE_API_KEY:
                logger.info("[ProviderRouter] DashScope 无 API key，标记为不可用")
                # 仍加入路由，但 health_check 会返回 False；断路器初次失败即 OPEN
            routes.append(ProviderRoute(provider=provider, priority=priority))

        elif name == "mock":
            routes.append(ProviderRoute(provider=MockLLMProvider(), priority=priority))

        else:
            # 通用 OpenAI 兼容供应商
            api_key = os.environ.get(f"{name_upper}_API_KEY", "")
            base_url = os.environ.get(f"{name_upper}_BASE_URL", "")
            default_model = os.environ.get(f"{name_upper}_DEFAULT_MODEL", "gpt-4o-mini")
            embedding_model = os.environ.get(f"{name_upper}_EMBEDDING_MODEL") or None
            timeout = int(os.environ.get(f"{name_upper}_TIMEOUT", "90"))

            if not api_key or not base_url:
                logger.warning(
                    f"[ProviderRouter] {name} 缺少 API_KEY 或 BASE_URL，跳过"
                )
                continue

            routes.append(ProviderRoute(
                provider=OpenAICompatibleProvider(
                    name=name,
                    api_key=api_key,
                    base_url=base_url,
                    default_model=default_model,
                    embedding_model=embedding_model,
                    timeout=timeout,
                ),
                priority=priority,
            ))

    # 兜底：所有供应商都未配置时加 Mock
    if not routes:
        logger.warning("[ProviderRouter] 无任何供应商配置，使用 MockLLM")
        routes.append(ProviderRoute(provider=MockLLMProvider(), priority=1))

    return routes


def get_llm_router() -> ProviderRouter:
    """幂等单例。"""
    global _llm_router
    if _llm_router is None:
        _llm_router = ProviderRouter(_build_routes_from_env())
    return _llm_router


# 模块级便捷别名（业务代码无感知切换）
llm_router = get_llm_router()
