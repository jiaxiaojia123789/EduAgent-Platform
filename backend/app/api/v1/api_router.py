from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.agents import router as agents_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.artifacts import router as artifacts_router
from app.api.v1.mcp_skills import router as mcp_skills_router
from app.api.v1.memory import router as memory_router

api_v1_router = APIRouter()
api_v1_router.include_router(auth_router)
api_v1_router.include_router(agents_router)
api_v1_router.include_router(conversations_router)
api_v1_router.include_router(knowledge_router)
api_v1_router.include_router(artifacts_router)
api_v1_router.include_router(mcp_skills_router)
api_v1_router.include_router(memory_router)
