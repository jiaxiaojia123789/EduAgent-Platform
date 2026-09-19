from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.v1.auth import get_current_user
from app.schemas.memory import (
    UserProfileBase, UserProfileUpdate, UserProfileOut,
    MemoryItemCreate, MemoryItemUpdate, MemoryItemOut,
    MemoryReflectRequest, MemoryReflectResponse
)
from app.services.memory.memory_service import memory_service

router = APIRouter(prefix="/memory", tags=["User Pedagogical Memory"])


@router.get("/profile", response_model=UserProfileOut)
async def get_my_profile(current_user: dict = Depends(get_current_user)):
    """Fetches the pedagogical profile and teaching habits of the authenticated user."""
    profile = memory_service.get_user_profile(current_user["id"])
    return profile


@router.put("/profile", response_model=UserProfileOut)
async def update_my_profile(payload: UserProfileUpdate, current_user: dict = Depends(get_current_user)):
    """Updates teaching profile parameters (subject, grade, student status, teaching style)."""
    updates = payload.dict(exclude_unset=True)
    updated = memory_service.update_user_profile(current_user["id"], updates)
    return updated


@router.get("/items", response_model=List[MemoryItemOut])
async def list_my_memories(
    active_only: bool = Query(False, description="Filter only active memories"),
    category: Optional[str] = Query(None, description="Filter by category (pedagogy, preference, student_status, custom)"),
    current_user: dict = Depends(get_current_user)
):
    """Lists all stored pedagogical memories for the current user."""
    items = memory_service.get_memory_items(
        user_id=current_user["id"],
        active_only=active_only,
        category=category
    )
    return items


@router.post("/items", response_model=MemoryItemOut)
async def create_memory_item(payload: MemoryItemCreate, current_user: dict = Depends(get_current_user)):
    """Creates a new pedagogical memory entry for the current user."""
    item = memory_service.add_memory_item(
        user_id=current_user["id"],
        title=payload.title,
        content=payload.content,
        category=payload.category,
        importance=payload.importance,
        is_active=payload.is_active
    )
    return item


@router.put("/items/{item_id}", response_model=MemoryItemOut)
async def update_memory_item(
    item_id: str,
    payload: MemoryItemUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Updates an existing memory item, including active toggle, importance, or content."""
    updates = payload.dict(exclude_unset=True)
    updated = memory_service.update_memory_item(
        user_id=current_user["id"],
        item_id=item_id,
        updates=updates
    )
    if not updated:
        raise HTTPException(status_code=404, detail="记忆条目不存在或无权修改")
    return updated


@router.delete("/items/{item_id}")
async def delete_memory_item(item_id: str, current_user: dict = Depends(get_current_user)):
    """Deletes a specific memory item."""
    success = memory_service.delete_memory_item(current_user["id"], item_id)
    if not success:
        raise HTTPException(status_code=404, detail="记忆条目不存在")
    return {"message": "记忆条目已成功删除", "item_id": item_id}


@router.delete("/items")
async def clear_all_memories(current_user: dict = Depends(get_current_user)):
    """Clears all memory items for the current user."""
    count = memory_service.clear_memory_items(current_user["id"])
    return {"message": f"已清空该用户的 {count} 条专属记忆", "count": count}


@router.get("/prompt-preview")
async def preview_memory_prompt(current_user: dict = Depends(get_current_user)):
    """Previews the prompt string that is dynamically injected into the multi-agent LLM."""
    prompt = memory_service.build_memory_prompt(current_user["id"])
    return {"memory_prompt": prompt}


@router.post("/reflect", response_model=MemoryReflectResponse)
async def reflect_dialogue_memories(
    payload: MemoryReflectRequest,
    current_user: dict = Depends(get_current_user)
):
    """Analyzes recent dialogues to automatically reflect and extract candidate teaching preferences."""
    result = memory_service.reflect_memories_from_dialogue(current_user["id"], payload.recent_dialogues)
    return result
