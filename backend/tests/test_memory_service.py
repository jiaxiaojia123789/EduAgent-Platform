import pytest
from app.services.memory.memory_service import memory_service


def test_default_teacher_memory():
    # Teacher demo profile
    profile = memory_service.get_user_profile("u-001")
    assert profile is not None
    assert "数学" in profile["subject"]
    assert profile["auto_memory_enabled"] is True

    # Teacher demo memories
    memories = memory_service.get_memory_items("u-001", active_only=True)
    assert len(memories) >= 4
    titles = [m["title"] for m in memories]
    assert any("割线" in t or "导数" in t for t in titles)
    assert any("板书" in t for t in titles)


def test_memory_crud():
    user_id = "test-user-memory-crud"
    memory_service.clear_memory_items(user_id)
    # Create item
    item = memory_service.add_memory_item(
        user_id=user_id,
        title="测试教学记忆",
        content="授课时重点强调一题多解与数形结合思维",
        category="pedagogy",
        importance=4,
        is_active=True
    )
    assert item["id"] is not None
    assert item["title"] == "测试教学记忆"

    # Fetch items
    items = memory_service.get_memory_items(user_id)
    assert len(items) == 1
    assert items[0]["importance"] == 4

    # Update item (toggle active)
    updated = memory_service.update_memory_item(
        user_id=user_id,
        item_id=item["id"],
        updates={"is_active": False, "importance": 5}
    )
    assert updated["is_active"] is False
    assert updated["importance"] == 5

    # Build prompt test
    prompt = memory_service.build_memory_prompt(user_id)
    assert "当前授课教师专属教学记忆与画像约束" in prompt

    # Delete item
    deleted = memory_service.delete_memory_item(user_id, item["id"])
    assert deleted is True
    assert len(memory_service.get_memory_items(user_id)) == 0


def test_memory_reflection():
    dialogues = [
        "老师，这道导数题我算错了，能不能给我讲讲容易踩坑的地方？",
        "我们在板书设计上要给学生留出草稿演算区域，而且这道压轴题要附上严格的评分细则与采分点。"
    ]
    result = memory_service.reflect_memories_from_dialogue("u-001", dialogues)
    assert len(result["reflected_items"]) > 0
    assert "reflected_items" in result
