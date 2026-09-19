"""
FileResourceManager — 多 Agent 文件并行访问的资源控制

职责：
1. 写锁 X（可按 section_range 细粒度）：同一文件区域同一时刻只允许
   一个 writer；区域不重叠的写操作可并行
2. 读锁 S：在 MVCC 快照模式下读永不阻塞——reader 注册后 pin 住自己的
   文件版本（见 file_version_store），writer 提交不影响在途 reader
3. 租约 ttl + renew 心跳续期：任务异常死亡时锁自动过期，不会永久卡死；
   release 用 token 校验，只释放自己的锁

后端：
- Redis：acquire/release 用 Lua 脚本原子判定区域重叠并写入
- 无 Redis：降级为进程内 async 实现（轮询等待），语义一致
"""
import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.core.redis_client import redis_manager

logger = logging.getLogger(__name__)

# 区域：[start, end] 整数偏移；WHOLE = (-1, -1) 表示整个文件
WHOLE_REGION: Tuple[int, int] = (-1, -1)
DEFAULT_TTL = 30          # 锁租约（秒）
RENEW_BEFORE_EXPIRY = 10  # 剩余租约不足该值时心跳续期


def regions_overlap(a: Tuple[int, int], b: Tuple[int, int]) -> bool:
    """两个区域是否重叠；任一为 WHOLE 即与一切重叠。"""
    if a == WHOLE_REGION or b == WHOLE_REGION:
        return True
    return not (a[1] <= b[0] or b[1] <= a[0])


@dataclass
class FileLockHandle:
    token: str
    file_id: str
    mode: str                       # "S" / "X"
    region: Tuple[int, int]
    owner_id: str
    acquired_at: float

    def to_log(self) -> str:
        rng = "WHOLE" if self.region == WHOLE_REGION else f"{self.region[0]}-{self.region[1]}"
        return f"{self.mode}({rng})[{self.file_id}]"


class _InMemoryLockBackend:
    """无 Redis 时的进程内后端：writers/readers 注册表 + 条件变量等待。"""

    def __init__(self):
        self._writers: Dict[str, Dict[str, Any]] = {}  # token -> {file_id, region, owner_id, at}
        self._readers: Dict[str, Dict[str, Any]] = {}
        self._cond = asyncio.Condition()

    async def try_acquire_write(self, file_id, region, token, owner_id) -> bool:
        async with self._cond:
            for w in self._writers.values():
                if w["file_id"] == file_id and regions_overlap(region, w["region"]):
                    return False
            self._writers[token] = {
                "file_id": file_id, "region": region,
                "owner_id": owner_id, "at": time.time(),
            }
            self._cond.notify_all()
            return True

    async def register_read(self, file_id, region, token, owner_id):
        # MVCC：读始终允许，仅注册用于跟踪
        async with self._cond:
            self._readers[token] = {
                "file_id": file_id, "region": region,
                "owner_id": owner_id, "at": time.time(),
            }

    async def release(self, token: str):
        async with self._cond:
            existed = self._writers.pop(token, None) or self._readers.pop(token, None)
            self._cond.notify_all()
            return existed is not None

    async def active_writer_regions(self, file_id) -> List[Tuple[int, int]]:
        async with self._cond:
            return [w["region"] for w in self._writers.values() if w["file_id"] == file_id]


class FileResourceManager:
    """文件读写锁管理器（单例）。"""

    # ---- Lua：原子尝试加写锁（区域不重叠才写入） ----
    _LUA_TRY_WRITE = """
    local writers = redis.call('HGETALL', KEYS[1])
    local region = cjson.decode(ARGV[1])
    for i = 1, #writers, 2 do
        local r = cjson.decode(writers[i + 1])
        local function overlap(a, b)
            if a[1] == -1 or b[1] == -1 then return true end
            if a[2] <= b[1] or b[2] <= a[1] then return false end
            return true
        end
        if overlap(region, r.region) then return 0 end
    end
    redis.call('HSET', KEYS[1], ARGV[3], cjson.encode({region = region, owner = ARGV[2]}))
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
    return 1
    """

    _LUA_RELEASE = """
    local removed = redis.call('HDEL', KEYS[1], ARGV[1])
    local r2 = redis.call('HDEL', KEYS[2], ARGV[1])
    if removed + r2 > 0 then return 1 else return 0 end
    """

    def __init__(self):
        self._mem = _InMemoryLockBackend()

    def _wkey(self, file_id: str) -> str:
        return f"filelock:{file_id}:writers"

    def _rkey(self, file_id: str) -> str:
        return f"filelock:{file_id}:readers"

    # ------------------------------------------------------------------
    # 写锁
    # ------------------------------------------------------------------
    async def acquire_write(
        self,
        file_id: str,
        owner_id: str,
        region: Tuple[int, int] = WHOLE_REGION,
        ttl: int = DEFAULT_TTL,
        wait_timeout: float = 10.0,
        poll_interval: float = 0.15,
    ) -> Optional[FileLockHandle]:
        """
        获取区域写锁；区域被占用时等待，超过 wait_timeout 返回 None。
        """
        token = uuid.uuid4().hex
        deadline = time.monotonic() + wait_timeout

        if redis_manager.is_connected and redis_manager.client:
            while True:
                try:
                    ok = await redis_manager.client.eval(
                        self._LUA_TRY_WRITE, 1, self._wkey(file_id),
                        json.dumps(list(region)), owner_id, token, str(ttl),
                    )
                    if int(ok) == 1:
                        return self._handle(token, file_id, "X", region, owner_id)
                except Exception as e:
                    logger.error(f"[FileResourceManager] Redis 加写锁异常: {e}")
                    return None
                if time.monotonic() >= deadline:
                    return None
                await asyncio.sleep(poll_interval)

        # 内存降级
        while True:
            if await self._mem.try_acquire_write(file_id, region, token, owner_id):
                return self._handle(token, file_id, "X", region, owner_id)
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(poll_interval)

    # ------------------------------------------------------------------
    # 读锁（MVCC：始终授予，仅注册）
    # ------------------------------------------------------------------
    async def acquire_read(
        self,
        file_id: str,
        owner_id: str,
        region: Tuple[int, int] = WHOLE_REGION,
        ttl: int = DEFAULT_TTL,
    ) -> FileLockHandle:
        token = uuid.uuid4().hex
        if redis_manager.is_connected and redis_manager.client:
            try:
                await redis_manager.client.hset(
                    self._rkey(file_id), token,
                    json.dumps({"region": list(region), "owner": owner_id}),
                )
                await redis_manager.client.expire(self._rkey(file_id), ttl)
            except Exception as e:
                # 读锁仅为跟踪用途，注册失败不阻断 MVCC 读取
                logger.warning(f"[FileResourceManager] 读锁注册失败（不阻断读取）: {e}")
        else:
            await self._mem.register_read(file_id, region, token, owner_id)
        return self._handle(token, file_id, "S", region, owner_id)

    # ------------------------------------------------------------------
    # 释放 / 续期
    # ------------------------------------------------------------------
    async def release(self, handle: FileLockHandle) -> bool:
        if redis_manager.is_connected and redis_manager.client:
            try:
                key = self._wkey(handle.file_id) if handle.mode == "X" else self._rkey(handle.file_id)
                other = self._rkey(handle.file_id) if handle.mode == "X" else self._wkey(handle.file_id)
                ok = await redis_manager.client.eval(
                    self._LUA_RELEASE, 2, key, other, handle.token
                )
                return int(ok) == 1
            except Exception as e:
                logger.error(f"[FileResourceManager] Redis 释放锁异常: {e}")
                return False
        return await self._mem.release(handle.token)

    async def renew(self, handle: FileLockHandle, ttl: int = DEFAULT_TTL) -> bool:
        """心跳续期；内存模式下刷新活跃时间（始终成功）。"""
        if redis_manager.is_connected and redis_manager.client:
            try:
                key = self._wkey(handle.file_id) if handle.mode == "X" else self._rkey(handle.file_id)
                return bool(await redis_manager.client.expire(key, ttl))
            except Exception as e:
                logger.error(f"[FileResourceManager] Redis 续期异常: {e}")
                return False
        return True

    @staticmethod
    def _handle(token, file_id, mode, region, owner_id) -> FileLockHandle:
        return FileLockHandle(
            token=token, file_id=file_id, mode=mode,
            region=tuple(region), owner_id=owner_id, acquired_at=time.time(),
        )


file_resource_manager = FileResourceManager()
