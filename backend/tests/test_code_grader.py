import asyncio
from app.services.sandbox.code_executor import (
    StaticSecurityChecker,
    SandboxCodeExecutor,
    code_sandbox
)
from app.harness.authorizer import ToolAuthorizer, ToolRiskLevel
from app.services.agent.specialized.code_grader import code_grader_agent
from app.services.agent.graph import graph_engine


def test_static_security_checker_catches_dangerous_imports():
    # Dangerous import os
    bad_code_1 = "import os\nos.system('whoami')"
    err = StaticSecurityChecker.inspect_code(bad_code_1)
    assert err is not None
    assert "os" in err

    # Dangerous from socket import socket
    bad_code_2 = "from socket import socket\ns = socket()"
    err = StaticSecurityChecker.inspect_code(bad_code_2)
    assert err is not None
    assert "socket" in err

    # Dangerous forbidden call open
    bad_code_3 = "def write_log():\n    f = open('secret.txt', 'w')\n    f.write('data')"
    err = StaticSecurityChecker.inspect_code(bad_code_3)
    assert err is not None
    assert "open" in err

    # Dangerous forbidden call eval
    bad_code_4 = "x = eval('2 + 2')"
    err = StaticSecurityChecker.inspect_code(bad_code_4)
    assert err is not None
    assert "eval" in err

    # Safe math code
    safe_code = "import math\ndef calc_circle_area(r):\n    return math.pi * r * r\n"
    assert StaticSecurityChecker.inspect_code(safe_code) is None


def test_sandbox_security_rejection():
    res = code_sandbox.run_code_with_test_cases(
        code="import subprocess\nsubprocess.run(['ls'])",
        test_cases=[{"input": "{}", "expected": "0"}]
    )
    assert res["status"] == "SECURITY_VIOLATION"
    assert res["passed_tests"] == 0
    assert "subprocess" in res["error_message"]


def test_sandbox_timeout_rejection():
    infinite_loop_code = """
def loop_forever(n):
    while True:
        pass
    return n
"""
    res = code_sandbox.run_code_with_test_cases(
        code=infinite_loop_code,
        test_cases=[{"input": "{\"n\": 1}", "expected": "1"}],
        timeout_seconds=2
    )
    assert res["status"] == "FAILED"
    assert len(res["test_results"]) == 1
    assert res["test_results"][0]["passed"] is False
    assert "超时" in res["test_results"][0]["error"] or "TimeLimitExceeded" in res["test_results"][0]["error"]


def test_sandbox_successful_grading():
    correct_binary_search = """
def binary_search(nums, target):
    left, right = 0, len(nums) - 1
    while left <= right:
        mid = (left + right) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
"""
    test_cases = [
        {"input": "{\"nums\": [1, 3, 5, 7, 9], \"target\": 5}", "expected": "2"},
        {"input": "{\"nums\": [1, 3, 5, 7, 9], \"target\": 1}", "expected": "0"},
        {"input": "{\"nums\": [1, 3, 5, 7, 9], \"target\": 9}", "expected": "4"},
        {"input": "{\"nums\": [1, 3, 5, 7, 9], \"target\": 10}", "expected": "-1"},
        {"input": "{\"nums\": [], \"target\": 1}", "expected": "-1"}
    ]
    res = code_sandbox.run_code_with_test_cases(
        code=correct_binary_search,
        test_cases=test_cases,
        timeout_seconds=3
    )
    assert res["status"] == "SUCCESS"
    assert res["passed_tests"] == 5
    assert res["total_tests"] == 5
    assert res["pass_rate"] == 100.0


def test_sandbox_failed_test_case():
    buggy_code = """
def add(a, b):
    return a - b  # Bug: subtraction instead of addition
"""
    test_cases = [
        {"input": "{\"a\": 2, \"b\": 3}", "expected": "5"},
        {"input": "{\"a\": 0, \"b\": 0}", "expected": "0"}
    ]
    res = code_sandbox.run_code_with_test_cases(
        code=buggy_code,
        test_cases=test_cases
    )
    assert res["passed_tests"] == 1
    assert res["total_tests"] == 2
    assert res["pass_rate"] == 50.0
    assert res["status"] == "FAILED"
    assert res["test_results"][0]["passed"] is False
    assert res["test_results"][1]["passed"] is True


def test_tool_authorizer_policy_for_sandbox():
    allowed, requires_hitl, msg = ToolAuthorizer.authorize("execute_code_in_sandbox", "student")
    assert allowed is True
    assert requires_hitl is False

    allowed_teacher, _, _ = ToolAuthorizer.authorize("execute_code_in_sandbox", "teacher")
    assert allowed_teacher is True


def test_code_grader_agent_workflow():
    user_prompt = """
批改学生提交的二分查找算法代码：
```python
def binary_search(nums, target):
    left, right = 0, len(nums) - 1
    while left <= right:
        mid = (left + right) // 2
        if nums[mid] == target:
            return mid
        elif nums[mid] < target:
            left = mid + 1
        else:
            right = mid - 1
    return -1
```
"""
    result = asyncio.run(graph_engine.run_workflow(
        user_message=user_prompt,
        session_id="test-code-grader-session",
        thread_id="test-code-grader-thread",
        explicit_agent="code_grader"
    ))

    assert result["agent_type"] == "code_grader"
    assert result["artifact"] is not None
    assert result["artifact_type"] == "CODE_GRADING_REPORT"
    assert "批改" in result["artifact"]["title"] or "评测" in result["artifact"]["title"]
