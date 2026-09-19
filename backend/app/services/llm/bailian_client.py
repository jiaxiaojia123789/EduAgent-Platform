import json
import asyncio
import logging
from typing import List, Dict, Any, AsyncGenerator, Optional, Callable, Awaitable
import requests
from app.core.config import settings
from app.harness.timeout import with_timeout, ExternalCallTimeoutError

logger = logging.getLogger(__name__)


class BailianLLMClient:
    """
    Alibaba Bailian (DashScope) Client Wrapper
    Provides seamless integration with:
    - Qwen-Max, Qwen3.5-Plus, Qwen-Plus, Qwen-Turbo
    - Text-Embedding-v3 (1024-dim dense vectors)
    - OpenAI compatible protocol
    - Robust asyncio.to_thread request execution for ultra-reliable concurrency on Windows
    - Graceful fallback with educational mockup for local development/testing without live keys
    - SSE 流式 token 推送（通过 queue 跨线程桥接）
    """

    def __init__(self):
        self.api_key = settings.DASHSCOPE_API_KEY
        self.base_url = settings.DASHSCOPE_BASE_URL
        self.is_mock = (
            not self.api_key
            or "demo-key" in self.api_key
            or "your-bailian-api-key" in self.api_key
        )
        if self.is_mock:
            logger.warning("[BailianLLMClient] Running in Smart Simulation Mode (DASHSCOPE_API_KEY is not set or is demo key).")
        else:
            logger.info(f"[BailianLLMClient] Active with real DashScope API key. Default model: {settings.DEFAULT_LLM_MODEL}")

    async def acomplete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        enable_thinking: bool = False,
    ) -> Dict[str, Any]:
        """
        Async non-streaming completion.
        enable_thinking: True 时返回 reasoning_content（思维链），qwen3 系列支持
        """
        model_name = model or settings.DEFAULT_LLM_MODEL

        if not self.is_mock:
            try:
                def _post_sync():
                    payload = {
                        "model": model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    }
                    # qwen3 系列默认开启思维链，关闭以避免污染输出
                    # enable_thinking 必须放在 payload 顶层（DashScope OpenAI 兼容协议）
                    if "qwen3" in model_name.lower():
                        payload["enable_thinking"] = enable_thinking
                    if response_format:
                        payload["response_format"] = response_format
                    # requests 自身超时：连接 5s，读取不超过 LLM_CALL_TIMEOUT，
                    # 保证 wait_for 强杀后挂起线程也能尽快收尾
                    return requests.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json"
                        },
                        json=payload,
                        timeout=(5, settings.LLM_CALL_TIMEOUT)
                    )

                # watchdog 硬超时：截止时间到达立即强杀 await
                resp = await with_timeout(
                    asyncio.to_thread(_post_sync),
                    timeout=settings.LLM_CALL_TIMEOUT,
                    name=f"LLM acomplete [{model_name}]",
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choice = data["choices"][0]
                    message = choice.get("message", {})
                    content = message.get("content") or ""
                    # 思维链字段（qwen3 系列）：默认丢弃，仅 enable_thinking=True 时返回
                    reasoning = message.get("reasoning_content", "") if enable_thinking else ""
                    return {
                        "content": content,
                        "reasoning": reasoning,
                        "model": model_name,
                        "usage": data.get("usage", {}),
                    }
                else:
                    # 真实模式调用失败：抛出明确错误，不再静默 fallback 到 mock
                    error_body = resp.text[:500]
                    logger.error(f"[BailianLLMClient] DashScope error {resp.status_code}: {error_body}")
                    raise RuntimeError(
                        f"DashScope API 调用失败 (HTTP {resp.status_code}): {error_body}"
                    )
            except RuntimeError:
                raise  # 重新抛出，不要被下面的 except 吞掉
            except Exception as e:
                logger.error(f"[BailianLLMClient] Live call failed: {e}")
                raise RuntimeError(f"DashScope API 网络异常: {e}")

        # Simulation 模式：仅当未配置 API key 时才走演示回退
        last_msg = messages[-1]["content"] if messages else ""
        simulated_response = self._generate_simulated_response(last_msg, model_name)
        return {
            "content": simulated_response,
            "reasoning": "",
            "model": f"{model_name}-simulated",
            "usage": {"prompt_tokens": 120, "completion_tokens": 380, "total_tokens": 500}
        }

    async def astream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        enable_thinking: bool = False,
    ) -> AsyncGenerator[str, None]:
        """
        Async streaming completion generator for SSE.
        修复说明：
        1. 原实现把 requests.iter_lines() 直接用 await 跑，迭代器不会 yield。
           改用 asyncio.Queue + 后台线程桥接，确保真实流式生效。
        2. qwen3 系列流式响应的 chunk 里 delta 可能包含 reasoning_content（思维链），
           默认丢弃，仅当 enable_thinking=True 时才产出。
        """
        model_name = model or settings.DEFAULT_LLM_MODEL

        if not self.is_mock:
            queue: asyncio.Queue[Optional[str]] = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def _stream_in_thread():
                try:
                    payload = {
                        "model": model_name,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "stream": True,
                    }
                    # qwen3 系列默认开启思维链，关闭以避免污染输出
                    # enable_thinking 必须放在 payload 顶层
                    if "qwen3" in model_name.lower():
                        payload["enable_thinking"] = enable_thinking
                    resp = requests.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json"
                        },
                        json=payload,
                        stream=True,
                        timeout=(5, settings.LLM_STREAM_IDLE_TIMEOUT)
                    )
                    if resp.status_code != 200:
                        error_body = resp.text[:500]
                        logger.error(f"[BailianLLMClient] Stream error {resp.status_code}: {error_body}")
                        # 把错误信息作为特殊事件传给消费者，让其知晓调用失败
                        err_msg = f"\n\n[DashScope API 调用失败 HTTP {resp.status_code}]: {error_body}"
                        asyncio.run_coroutine_threadsafe(queue.put(err_msg), loop)
                        asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                        return
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                        if not line_str.startswith("data: "):
                            continue
                        data_str = line_str[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_str)
                            delta = chunk_json["choices"][0].get("delta", {})
                            # 优先取 content，忽略 reasoning_content（除非显式启用）
                            content = delta.get("content", "")
                            if content:
                                asyncio.run_coroutine_threadsafe(queue.put(content), loop)
                        except Exception:
                            pass
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)
                except Exception as e:
                    logger.error(f"[BailianLLMClient] Streaming thread failed: {e}")
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)

            # 启动后台线程执行同步流式请求
            import threading
            threading.Thread(target=_stream_in_thread, daemon=True).start()

            # watchdog：空闲超时（两个 token 最大间隔）+ 总截止时间
            deadline = asyncio.get_running_loop().time() + settings.LLM_STREAM_TOTAL_TIMEOUT
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise ExternalCallTimeoutError(
                        f"LLM astream total [{model_name}]",
                        settings.LLM_STREAM_TOTAL_TIMEOUT,
                        settings.LLM_STREAM_TOTAL_TIMEOUT,
                    )
                try:
                    chunk = await asyncio.wait_for(
                        queue.get(),
                        timeout=min(settings.LLM_STREAM_IDLE_TIMEOUT, remaining),
                    )
                except asyncio.TimeoutError:
                    raise ExternalCallTimeoutError(
                        f"LLM astream idle [{model_name}]",
                        settings.LLM_STREAM_IDLE_TIMEOUT,
                        settings.LLM_STREAM_IDLE_TIMEOUT,
                    )
                if chunk is None:
                    break
                yield chunk
            return

        # Simulated streaming chunks
        last_msg = messages[-1]["content"] if messages else ""
        sim_text = self._generate_simulated_response(last_msg, model_name)
        chunk_size = 8
        for i in range(0, len(sim_text), chunk_size):
            yield sim_text[i:i + chunk_size]
            # 模拟网络延迟
            await asyncio.sleep(0.02)

    async def astream_with_callback(
        self,
        messages: List[Dict[str, str]],
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        enable_thinking: bool = False,
    ) -> str:
        """
        流式生成 + token 回调：边产出 token 边推送 SSE
        返回最终完整文本，同时通过 on_token 回调实时推送每个 chunk
        """
        full_text_parts: List[str] = []
        async for chunk in self.astream(
            messages, model=model, temperature=temperature, max_tokens=max_tokens,
            enable_thinking=enable_thinking,
        ):
            full_text_parts.append(chunk)
            if on_token:
                try:
                    await on_token(chunk)
                except Exception as e:
                    logger.warning(f"[BailianLLMClient] on_token 回调失败: {e}")
        return "".join(full_text_parts)

    async def get_embedding(self, text: str, model: Optional[str] = None) -> List[float]:
        """Generates dense vector embedding using text-embedding-v3."""
        model_name = model or settings.DEFAULT_EMBEDDING_MODEL

        if not self.is_mock:
            try:
                def _embed_sync():
                    return requests.post(
                        f"{self.base_url}/embeddings",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json"
                        },
                        json={"model": model_name, "input": text},
                        timeout=(5, settings.EMBEDDING_CALL_TIMEOUT)
                    )

                # watchdog 硬超时，超时异常直接上抛不走假向量兜底
                resp = await with_timeout(
                    asyncio.to_thread(_embed_sync),
                    timeout=settings.EMBEDDING_CALL_TIMEOUT,
                    name=f"Embedding [{model_name}]",
                )
                if resp.status_code == 200:
                    return resp.json()["data"][0]["embedding"]
                else:
                    logger.error(f"[BailianLLMClient] Embedding error {resp.status_code}: {resp.text}")
            except ExternalCallTimeoutError:
                raise  # 超时强杀必须上抛，不能降级为假向量
            except Exception as e:
                logger.error(f"[BailianLLMClient] Embedding call failed: {e}.")

        # Deterministic simulated 1024-dim embedding based on text hash
        import hashlib
        import numpy as np
        hash_digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(hash_digest[:4], "little")
        rng = np.random.default_rng(seed)
        vec = rng.standard_normal(settings.EMBEDDING_DIMENSION).astype(float)
        norm = np.linalg.norm(vec)
        return (vec / norm).tolist() if norm > 0 else vec.tolist()

    def _generate_simulated_response(self, user_prompt: str, model: str) -> str:
        """High-fidelity educational domain mockup when in demo mode."""
        return (
            f"### 【教学设计与分析】\n\n"
            f"根据您的备课与教研需求，针对「{user_prompt[:30]}」：\n\n"
            f"1. **核心素养导向**：遵循新课标要求，重点培养数学抽象与逻辑推理核心素养；\n"
            f"2. **探究环节设计**：从真实生活情境引入，通过数形结合引导学生自主推导；\n"
            f"3. **分层评价反馈**：设置基础巩固与思维拓展梯度题，精准反馈课堂达标率。\n\n"
            f"> *（当前处于演示仿真模式，接入真实阿里云百炼 API-KEY 后将由 {model} 提供实时大模型推理）*"
        )


bailian_client = BailianLLMClient()
