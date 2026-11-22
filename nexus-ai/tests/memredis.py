"""Small in-memory async Redis stand-in for unit tests (the commands NEXUS uses)."""

from __future__ import annotations

import time
from typing import Any


class MemRedis:
    def __init__(self, broken: bool = False) -> None:
        self.kv: dict[str, Any] = {}
        self.exp: dict[str, float] = {}
        self.broken = broken
        self.calls: list[str] = []

    def _live(self, k: str) -> bool:
        if k in self.exp and self.exp[k] <= time.monotonic():
            self.kv.pop(k, None)
            self.exp.pop(k, None)
        return k in self.kv

    def _check(self, op: str) -> None:
        self.calls.append(op)
        if self.broken:
            raise ConnectionError("redis down")

    async def get(self, k: str) -> Any:
        self._check("get")
        return self.kv[k] if self._live(k) else None

    async def set(self, k: str, v: Any, ex: int | None = None, nx: bool = False) -> bool:
        self._check("set")
        if nx and self._live(k):
            return False
        self.kv[k] = v
        if ex:
            self.exp[k] = time.monotonic() + ex
        else:
            self.exp.pop(k, None)
        return True

    async def delete(self, *keys: str) -> int:
        self._check("delete")
        n = 0
        for k in keys:
            if self._live(k):
                n += 1
            self.kv.pop(k, None)
            self.exp.pop(k, None)
        return n

    async def incrby(self, k: str, n: int) -> int:
        self._check("incrby")
        v = int(self.kv[k]) if self._live(k) else 0
        self.kv[k] = v + n
        return v + n

    async def incr(self, k: str) -> int:
        return await self.incrby(k, 1)

    async def decrby(self, k: str, n: int) -> int:
        return await self.incrby(k, -n)

    async def expire(self, k: str, seconds: int) -> bool:
        self._check("expire")
        if self._live(k):
            self.exp[k] = time.monotonic() + seconds
            return True
        return False

    async def hincrby(self, k: str, field: str, n: int) -> int:
        self._check("hincrby")
        h = self.kv.setdefault(k, {}) if self._live(k) or k not in self.kv else self.kv[k]
        self.kv[k] = h
        h[field] = int(h.get(field, 0)) + n
        return int(h[field])

    async def hgetall(self, k: str) -> dict[str, Any]:
        self._check("hgetall")
        return dict(self.kv[k]) if self._live(k) else {}

    async def lpush(self, k: str, *values: Any) -> int:
        self._check("lpush")
        lst = self.kv.setdefault(k, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    async def ltrim(self, k: str, start: int, end: int) -> None:
        if k in self.kv:
            self.kv[k] = self.kv[k][start:end + 1]

    async def lrange(self, k: str, start: int, end: int) -> list[Any]:
        return list(self.kv.get(k, [])[start:end + 1])

    async def xadd(self, stream: str, fields: dict[str, Any], **_: Any) -> str:
        self._check("xadd")
        self.kv.setdefault(stream, []).append(fields)
        return "0-1"
