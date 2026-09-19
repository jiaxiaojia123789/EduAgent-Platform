from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union
from jose import jwt
import bcrypt
from app.core.config import settings

# 直接使用 bcrypt 包（passlib 1.7.x 与 bcrypt 4+/5+、Python 3.13+ 不兼容，
# 在初始化阶段 detect_wrap_bug 会触发 "password cannot be longer than 72 bytes" 错误）
# bcrypt 原生 API 生成的哈希格式 $2b$... 与 passlib 生成的完全兼容，已有数据库无需迁移

# bcrypt 算法固有限制：密码最大 72 字节，超长会被静默截断
# 此处显式截断以避免 ValueError（与 passlib 历史行为一致）
_BCRYPT_MAX_BYTES = 72


def _truncate(password: str) -> bytes:
    """将密码编码为 UTF-8 字节，并截断到 72 字节以满足 bcrypt 限制"""
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate(plain_password), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str) -> str:
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode("utf-8")


def create_access_token(subject: Union[str, Any], role: str = "teacher", expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {
        "sub": str(subject),
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except Exception:
        return None
