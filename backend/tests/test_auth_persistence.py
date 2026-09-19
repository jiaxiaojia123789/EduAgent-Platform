import pytest
from app.services.auth.user_storage import user_storage
from app.core.security import verify_password


def test_seed_default_users():
    teacher = user_storage.get_by_username("teacher_demo")
    assert teacher is not None
    assert teacher["role"] == "teacher"
    assert verify_password("password123", teacher["hashed_password"])

    admin = user_storage.get_by_username("admin")
    assert admin is not None
    assert admin["role"] == "admin"
    assert verify_password("admin123", admin["hashed_password"])


def test_user_creation_and_retrieval():
    test_username = "test_teacher_01"
    # Ensure clean state
    existing = user_storage.get_by_username(test_username)
    if not existing:
        user = user_storage.create_user(
            username=test_username,
            email="test_teacher@edu.ai",
            password="secret_password",
            full_name="李老师",
            role="teacher"
        )
        assert user["username"] == test_username
        assert user["role"] == "teacher"

    # Verify retrieval
    fetched = user_storage.get_by_username(test_username)
    assert fetched is not None
    assert fetched["email"] == "test_teacher@edu.ai"

    # Verify credentials
    verified = user_storage.verify_credentials(test_username, "secret_password")
    assert verified is not None
    assert verified["username"] == test_username

    wrong_pw = user_storage.verify_credentials(test_username, "wrong_password")
    assert wrong_pw is None
