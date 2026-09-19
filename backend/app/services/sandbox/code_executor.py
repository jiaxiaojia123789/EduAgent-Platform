import os
import sys
import ast
import time
import tempfile
import subprocess
import logging
from typing import Dict, Any, List, Optional

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

    def __init__(self, default_timeout: int = 3):
        self.default_timeout = default_timeout

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
        runner_script = f"""# -*- coding: utf-8 -*-
import json
import sys

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
            proc = subprocess.run(
                [sys.executable, "-I", "-B", temp_path],
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
