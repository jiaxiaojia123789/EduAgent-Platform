"""
真实 API 端到端验证脚本
验证 DashScope LLM 和 MinerU 的真实调用是否生效
"""
import os
import sys
import asyncio
import json

# 手动加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # 没有 python-dotenv，手动读 .env
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k.strip(), v)

import requests

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
MINERU_API_TOKEN = os.getenv("MINERU_API_TOKEN", "")
DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MINERU_BASE = os.getenv("MINERU_API_BASE", "https://mineru.net/api/v4")
MODEL = os.getenv("DEFAULT_LLM_MODEL", "qwen-plus")


def check_env():
    print("=" * 70)
    print("环境变量检查")
    print("=" * 70)
    if DASHSCOPE_API_KEY:
        print(f"DASHSCOPE_API_KEY: {DASHSCOPE_API_KEY[:10]}...{DASHSCOPE_API_KEY[-4:]}")
    else:
        print("DASHSCOPE_API_KEY: 未配置")

    if MINERU_API_TOKEN:
        print(f"MINERU_API_TOKEN: {MINERU_API_TOKEN[:10]}...{MINERU_API_TOKEN[-4:]}")
    else:
        print("MINERU_API_TOKEN: 未配置")

    print(f"DEFAULT_LLM_MODEL: {MODEL}")
    print(f"DASHSCOPE_BASE_URL: {DASHSCOPE_BASE}")
    print(f"MINERU_API_BASE: {MINERU_BASE}")


def test_dashscope_nonstream():
    print()
    print("=" * 70)
    print("Test 1: DashScope 非流式调用")
    print("=" * 70)
    if not DASHSCOPE_API_KEY:
        print("[SKIP] DASHSCOPE_API_KEY 未配置")
        return

    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "用一句话介绍导数的几何意义"}],
        "max_tokens": 100,
        "extra_body": {"enable_thinking": False},
    }
    try:
        r = requests.post(
            f"{DASHSCOPE_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        print(f"HTTP Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            message = data["choices"][0]["message"]
            content = message.get("content", "")
            reasoning = message.get("reasoning_content", "")
            print(f"Content: {content[:300]}")
            if reasoning:
                print(f"Reasoning (前 200 字): {reasoning[:200]}...")
            print(f"Usage: {data.get('usage', {})}")
            print("[PASS] DashScope 非流式调用成功")
        else:
            print(f"[FAIL] Error: {r.text[:400]}")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")


def test_dashscope_stream():
    print()
    print("=" * 70)
    print("Test 2: DashScope 流式调用（验证 reasoning_content 是否分离）")
    print("=" * 70)
    if not DASHSCOPE_API_KEY:
        print("[SKIP] DASHSCOPE_API_KEY 未配置")
        return

    # enable_thinking 必须放在 payload 顶层（不是 extra_body）
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "5 个字回答"}],
        "max_tokens": 50,
        "stream": True,
        "enable_thinking": False,
    }
    try:
        r = requests.post(
            f"{DASHSCOPE_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            stream=True,
            timeout=30,
        )
        print(f"HTTP Status: {r.status_code}")
        if r.status_code != 200:
            print(f"[FAIL] {r.text[:300]}")
            return

        chunk_count = 0
        content_parts = []
        reasoning_chunks = 0
        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8") if isinstance(line, bytes) else line
            if not s.startswith("data: "):
                continue
            d = s[6:].strip()
            if d == "[DONE]":
                break
            try:
                j = json.loads(d)
                delta = j["choices"][0].get("delta", {})
                c = delta.get("content", "")
                rc = delta.get("reasoning_content", "")
                if c:
                    chunk_count += 1
                    content_parts.append(c)
                if rc:
                    reasoning_chunks += 1
            except Exception:
                pass

        print(f"Content chunks: {chunk_count}")
        print(f"Reasoning chunks: {reasoning_chunks}（应该是 0）")
        print(f"Assembled content: {''.join(content_parts)}")
        if chunk_count > 0:
            print("[PASS] DashScope 流式调用成功")
        else:
            print("[FAIL] 未收到任何 content chunk")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")


def test_mineru_auth():
    print()
    print("=" * 70)
    print("Test 3: MinerU API 鉴权（不实际解析 PDF，仅验证 token 有效）")
    print("=" * 70)
    if not MINERU_API_TOKEN:
        print("[SKIP] MINERU_API_TOKEN 未配置")
        return

    # 用一个不存在的 task_id 调用，预期返回 "task not found"，说明 token 通过鉴权
    try:
        r = requests.get(
            f"{MINERU_BASE}/extract/task/nonexistent_task_id_test",
            headers={"Authorization": f"Bearer {MINERU_API_TOKEN}"},
            timeout=10,
        )
        print(f"HTTP Status: {r.status_code}")
        print(f"Response: {r.text[:300]}")
        if r.status_code == 200 and "task not found" in r.text:
            print("[PASS] MinerU token 鉴权通过（task not found 是预期响应）")
        elif r.status_code == 401:
            print("[FAIL] MinerU token 无效（401 Unauthorized）")
        else:
            print(f"[WARN] 未知响应，请人工确认")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")


def test_embedding():
    print()
    print("=" * 70)
    print("Test 4: DashScope Embedding 调用（验证 text-embedding-v3）")
    print("=" * 70)
    if not DASHSCOPE_API_KEY:
        print("[SKIP] DASHSCOPE_API_KEY 未配置")
        return

    try:
        r = requests.post(
            f"{DASHSCOPE_BASE}/embeddings",
            headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}", "Content-Type": "application/json"},
            json={"model": "text-embedding-v3", "input": "高中数学导数"},
            timeout=30,
        )
        print(f"HTTP Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            vec = data["data"][0]["embedding"]
            print(f"Vector dim: {len(vec)}")
            print(f"First 5 values: {vec[:5]}")
            if len(vec) == 1024:
                print("[PASS] Embedding 维度 1024 正确")
            else:
                print(f"[WARN] 维度 {len(vec)} 与预期 1024 不符")
        else:
            print(f"[FAIL] {r.text[:300]}")
    except Exception as e:
        print(f"[FAIL] Exception: {e}")


if __name__ == "__main__":
    check_env()
    test_dashscope_nonstream()
    test_dashscope_stream()
    test_embedding()
    test_mineru_auth()
    print()
    print("=" * 70)
    print("验证完成。如全部 [PASS]，即可在 backend 启动后真实调用 LLM。")
    print("=" * 70)
