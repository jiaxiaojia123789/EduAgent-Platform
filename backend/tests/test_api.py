import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_healthz_endpoint():
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_auth_login_success():
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "teacher_demo", "password": "password123"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["role"] == "teacher"


def test_auth_login_failure():
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "teacher_demo", "password": "wrong_password"}
    )
    assert response.status_code == 401


def test_agent_matrix():
    response = client.get("/api/v1/agents/matrix")
    assert response.status_code == 200
    data = response.json()
    assert len(data["agents"]) >= 8
    # Check key agents exist
    agent_ids = [a["id"] for a in data["agents"]]
    assert "supervisor" in agent_ids
    assert "lesson_plan" in agent_ids
    assert "academic_rag" in agent_ids
    assert "exam_quiz" in agent_ids
    assert "socratic" in agent_ids
    assert "math_solver" in agent_ids


def test_agent_sync_run():
    response = client.post(
        "/api/v1/agents/sync-run",
        json={
            "message": "请为《导数的几何意义》设计一份简明微课教学方案大纲（300字以内）",
            "agent_type": "lesson_plan"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert "output" in data
    assert "artifact" in data
    assert "trace_summary" in data
    assert len(data["trace_summary"]["steps"]) >= 2
