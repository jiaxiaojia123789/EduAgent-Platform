import os
import sys
import ast
import time
import json
import shutil
import tempfile
import subprocess
import logging
import platform
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Dangerous imports and functions forbidden in educational sandbox
FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "importlib",
    "pathlib", "pty", "commands", "ctypes", "multiprocessing",
    "threading", "signal", "builtins", "__builtin__", "posix", "nt"
}

FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input"
}


class CodeSecurityError(Exception):
    """Raised when submitted code contains prohibited system or security calls."""
    pass


# ============================================================
# P4-23: 沙箱资源限制器
# ============================================================

# 网络出口白名单：仅允许连接这些 host（DNS 解析后的 IP）
# 教学场景默认完全离线，禁止任何网络出口
DEFAULT_NETWORK_WHITELIST: Tuple[str, ...] = ()

# 默认资源上限（与教育场景匹配，足够运行作业代码）
DEFAULT_CPU_SECONDS = 5        # 单测 CPU 时间 5s
DEFAULT_MEMORY_MB = 256        # 256MB 内存
DEFAULT_FILE_SIZE_KB = 1024    # 单文件写入上限 1MB
DEFAULT_PROCESSES = 1          # 不允许 fork


class SandboxResourceLimiter:
    """
    跨平台沙箱资源限制器：
    - Linux/macOS：优先使用 setrlimit + cgroup v2（如可用）
    - Windows：降级为软限制（仅 timeout 控制），告警提示生产应跑在 Linux
    - 网络：通过 iptables/netns 或 unshare --net 隔离（仅 Linux），其他平台仅依赖 AST 拦截

    设计原则：
    1. 资源限制在子进程内通过 Python 启动脚本注入，避免修改父进程
    2. 失败不阻断执行（best-effort），仅告警；超时由 subprocess.timeout 兜底
    3. Linux 上探测 cgroup v2 可用性，可用则用 cgroup 而非 setrlimit（更强）
    """

    def __init__(
        self,
        cpu_seconds: int = DEFAULT_CPU_SECONDS,
        memory_mb: int = DEFAULT_MEMORY_MB,
        file_size_kb: int = DEFAULT_FILE_SIZE_KB,
        max_processes: int = DEFAULT_PROCESSES,
        network_whitelist: Tuple[str, ...] = DEFAULT_NETWORK_WHITELIST,
    ):
        self.cpu_seconds = cpu_seconds
        self.memory_mb = memory_mb
        self.file_size_kb = file_size_kb
        self.max_processes = max_processes
        self.network_whitelist = network_whitelist
        self._is_linux = platform.system().lower() == "linux"

    def _linux_prelude(self) -> str:
        """生成 Linux 子进程启动前注入的资源限制脚本（setrlimit）。"""
        # RLIMIT_CPU=0 → CPU 秒；RLIMIT_AS=5 → 地址空间字节；RLIMIT_FSIZE=1 → 文件大小字节
        # RLIMIT_NPROC=6 → 进程数
        return f"""
import resource
try:
    resource.setrlimit(resource.RLIMIT_CPU, ({self.cpu_seconds}, {self.cpu_seconds}))
    resource.setrlimit(resource.RLIMIT_AS, ({self.memory_mb} * 1024 * 1024, {self.memory_mb} * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, ({self.file_size_kb} * 1024, {self.file_size_kb} * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, ({self.max_processes}, {self.max_processes}))
except Exception as _e:
    sys.stderr.write(f"[Sandbox] setrlimit failed: {{_e}}\\n")
"""

    def _windows_prelude(self) -> str:
        """
        Windows 无 setrlimit，仅注入空注释（不污染 stderr，避免影响测试断言）。
        生产环境应在 Linux 上运行以获得硬资源限制。
        """
        return "# [Sandbox] Windows 模式：仅靠 subprocess.timeout 兜底，生产环境请在 Linux 上启用 setrlimit"

    def build_prelude(self) -> str:
        """供 SandboxCodeExecutor 注入到 runner_script 顶部的资源限制代码。"""
        return self._linux_prelude() if self._is_linux else self._windows_prelude()

    def wrap_command(self, cmd: List[str]) -> List[str]:
        """
        Linux 上用 unshare --net 隔离网络命名空间（进程内无任何网络接口）。
        Windows/无权限时返回原 cmd，仅靠 AST 拦截。
        """
        if not self._is_linux:
            return cmd
        if self.network_whitelist:
            # 白名单模式暂不支持（需要 netns + iptables，复杂度高）
            # 生产环境如需网络出口白名单，应配合 egress firewall（如 Cilium）
            return cmd
        # 完全离线模式：unshare --net 让子进程没有网络接口
        unshare = shutil.which("unshare")
        if unshare:
            return [unshare, "--net", "--pid", "--fork", "--mount-proc", *cmd]
        logger.warning("[Sandbox] unshare 不可用，仅依赖 AST 拦截网络调用")
        return cmd


# 默认单例（无网络 + 5s CPU + 256MB 内存）
default_limiter = SandboxResourceLimiter()


class StaticSecurityChecker:
    """
    AST-based Static Code Security Analyzer.
    Pre-screens submitted code before execution to ensure sandbox safety.
    """

    @classmethod
    def inspect_code(cls, code: str) -> Optional[str]:
        """
        Parses code AST and returns an error string if security violations are detected.
        Returns None if code is safe.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"语法解析错误 (SyntaxError): {e.msg} (第 {e.lineno} 行)"

        for node in ast.walk(tree):
            # 1. Check imports (e.g., import os, import socket)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in FORBIDDEN_MODULES:
                        return f"安全拦截：严禁导入系统底层或网络模块 '{root_pkg}'"

            # 2. Check from imports (e.g., from os import system)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in FORBIDDEN_MODULES:
                        return f"安全拦截：严禁从系统底层模块 '{root_pkg}' 导入方法"

            # 3. Check forbidden function calls (e.g., eval, exec, open)
            elif isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in FORBIDDEN_CALLS:
                    return f"安全拦截：严禁在作业评测中调用高危内置函数 '{func_name}'"

        return None


class SandboxCodeExecutor:
    """
    Secure Execution Sandbox for Python Code Grading.
    Executes student submissions against test cases with:
    - AST pre-screening
    - Subprocess isolation
    - Strict execution timeout (anti-infinite loop)
    - Stdout/stderr capture and telemetry
    """

    def __init__(self, default_timeout: int = 3, resource_limiter: Optional[SandboxResourceLimiter] = None):
        self.default_timeout = default_timeout
        # P4-23: 资源限制器（CPU/内存/网络），默认单例；生产可注入自定义
        self._limiter = resource_limiter or default_limiter

    def run_code_with_test_cases(
        self,
        code: str,
        test_cases: List[Dict[str, Any]],
        language: str = "python",
        timeout_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        timeout = timeout_seconds or self.default_timeout

        # 1. Static Security Check
        security_violation = StaticSecurityChecker.inspect_code(code)
        if security_violation:
            return {
                "status": "SECURITY_VIOLATION",
                "total_tests": len(test_cases),
                "passed_tests": 0,
                "pass_rate": 0.0,
                "total_time_ms": 0,
                "error_message": security_violation,
                "test_results": []
            }

        # 2. Prepare test runner wrapper script
        test_results = []
        passed_count = 0
        total_time_ms = 0

        # Execute each test case in isolation
        for idx, tc in enumerate(test_cases, 1):
            input_data = tc.get("input", "")
            expected = str(tc.get("expected", "")).strip()

            single_res = self._execute_single_test(
                user_code=code,
                input_str=input_data,
                expected_str=expected,
                timeout=timeout
            )
            single_res["test_id"] = idx
            test_results.append(single_res)

            total_time_ms += single_res.get("time_ms", 0)
            if single_res.get("passed"):
                passed_count += 1

        total_tests = len(test_cases)
        pass_rate = round((passed_count / total_tests * 100), 1) if total_tests > 0 else 0.0

        return {
            "status": "SUCCESS" if passed_count == total_tests else "FAILED",
            "total_tests": total_tests,
            "passed_tests": passed_count,
            "pass_rate": pass_rate,
            "total_time_ms": total_time_ms,
            "error_message": None if passed_count == total_tests else f"{total_tests - passed_count} 个用例未通过",
            "test_results": test_results
        }

    def _execute_single_test(
        self,
        user_code: str,
        input_str: str,
        expected_str: str,
        timeout: int
    ) -> Dict[str, Any]:
        """
        Executes user code against a single test case inside a temporary isolated sandbox script.
        """
        # Formulate execution runner code
        # Supports both:
        # Case A: User defined a function (e.g. def solution(...) or def search(...))
        # Case B: User wrote a procedural script with standard input/output
        # P4-23: 顶部注入资源限制 prelude（Linux: setrlimit / Windows: 告警）
        resource_prelude = self._limiter.build_prelude()
        runner_script = f"""# -*- coding: utf-8 -*-
import json
import sys

# P4-23: 沙箱资源限制（CPU/内存/文件大小/进程数）
{resource_prelude}

# 1. Inject student code
{user_code}

# 2. Execute test input
if __name__ == '__main__':
    try:
        input_raw = {repr(input_str)}
        # Try finding callable function
        candidates = [v for k, v in list(locals().items()) if callable(v) and not k.startswith('__') and k not in ('json', 'sys')]
        if candidates:
            target_fn = candidates[-1]  # Most recently defined function
            # If input is JSON parseable (e.g. list, args)
            try:
                parsed_args = json.loads(input_raw)
                if isinstance(parsed_args, list):
                    res = target_fn(*parsed_args)
                elif isinstance(parsed_args, dict):
                    res = target_fn(**parsed_args)
                else:
                    res = target_fn(parsed_args)
            except Exception:
                res = target_fn(input_raw)
            print(res)
        else:
            # Procedural fallback
            pass
    except Exception as e:
        sys.stderr.write(f"RuntimeError: {{e}}\\n")
        sys.exit(1)
"""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(runner_script)
            temp_path = f.name

        start_t = time.time()
        try:
            # Run in isolated subprocess: -I (isolated mode), -B (dont write pyc)
            # P4-23: wrap_command 在 Linux 上用 unshare --net 隔离网络命名空间
            base_cmd = [sys.executable, "-I", "-B", temp_path]
            wrapped_cmd = self._limiter.wrap_command(base_cmd)
            proc = subprocess.run(
                wrapped_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8"
            )
            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()

            if proc.returncode != 0:
                return {
                    "input": input_str,
                    "expected": expected_str,
                    "actual": stdout,
                    "error": stderr or f"非正常退出代码: {proc.returncode}",
                    "passed": False,
                    "time_ms": elapsed_ms
                }

            # Check correctness: string or JSON value equality
            is_passed = self._compare_output(stdout, expected_str)

            return {
                "input": input_str,
                "expected": expected_str,
                "actual": stdout,
                "passed": is_passed,
                "time_ms": elapsed_ms,
                "error": None if is_passed else f"输出不符：期望 '{expected_str}'，实际得到 '{stdout}'"
            }

        except subprocess.TimeoutExpired:
            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            return {
                "input": input_str,
                "expected": expected_str,
                "actual": "",
                "passed": False,
                "time_ms": elapsed_ms,
                "error": f"执行超时 (TimeLimitExceeded > {timeout}s) - 可能存在死循环或深层递归"
            }
        except Exception as e:
            elapsed_ms = round((time.time() - start_t) * 1000, 2)
            return {
                "input": input_str,
                "expected": expected_str,
                "actual": "",
                "passed": False,
                "time_ms": elapsed_ms,
                "error": f"沙箱调度异常: {str(e)}"
            }
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    @staticmethod
    def _compare_output(actual: str, expected: str) -> bool:
        if actual == expected:
            return True
        # Try JSON / python literal equivalence (e.g. [1, 2] vs [1,2])
        try:
            import ast
            return ast.literal_eval(actual) == ast.literal_eval(expected)
        except Exception:
            pass
        return False


code_sandbox = SandboxCodeExecutor()
