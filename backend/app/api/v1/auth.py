from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.core.config import settings
from app.core.security import create_access_token, decode_access_token, verify_password, get_password_hash
from app.schemas.auth import UserLogin, UserRegister, Token, UserOut
from app.services.auth.user_storage import user_storage

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token", auto_error=False)


@router.post("/login", response_model=Token)
async def login(payload: UserLogin):
    user = user_storage.get_by_username(payload.username)
    if not user or not verify_password(payload.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已被禁用，请联系管理员"
        )

    access_token = create_access_token(
        subject=user["id"],
        role=user["role"],
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return Token(
        access_token=access_token,
        token_type="bearer",
        role=user["role"],
        user_id=user["id"],
        username=user["username"]
    )


@router.post("/register", response_model=Token)
async def register(payload: UserRegister):
    # Check if username exists
    if user_storage.get_by_username(payload.username):
        raise HTTPException(status_code=400, detail="用户名已被注册，请尝试其他名称")

    # Check if email exists
    if user_storage.get_by_email(payload.email):
        raise HTTPException(status_code=400, detail="该邮箱已绑定其他账户")

    new_user = user_storage.create_user(
        username=payload.username,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name or payload.username,
        role=payload.role or "teacher"
    )

    access_token = create_access_token(
        subject=new_user["id"],
        role=new_user["role"],
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        role=new_user["role"],
        user_id=new_user["id"],
        username=new_user["username"]
    )


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> dict:
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌，请先登录",
            headers={"WWW-Authenticate": "Bearer"}
        )
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或已过期的认证凭证，请重新登录",
            headers={"WWW-Authenticate": "Bearer"}
        )
    user_id = payload.get("sub")
    user = user_storage.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户档案不存在")
    return user


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user["email"],
        "full_name": user["full_name"],
        "role": user["role"],
        "is_active": bool(user["is_active"]),
        "created_at": user["created_at"]
    }


@router.get("/users")
async def list_all_users(user: dict = Depends(get_current_user)):
    """Admin or inspection endpoint to list all platform users."""
    return {"users": user_storage.list_users()}
