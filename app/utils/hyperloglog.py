"""HyperLogLog cardinality estimator used to keep distinct-value statistics bounded.

The analysis engine never stores unbounded distinct-value sets, so memory stays
flat even for 130,000+ row CSV files.
"""
from __future__ import annotations

import hashlib
import math


class HyperLogLog:
    """A HyperLogLog estimator with 2^p registers (p = 4..16)."""

    def __init__(self, p: int = 12):
        if not 4 <= p <= 16:
            raise ValueError("p must be between 4 and 16")
        self.p = p
        self.m = 1 << p
        self.mask = self.m - 1
        self.registers = bytearray(self.m)

    def add(self, item: str) -> None:
        digest = hashlib.blake2b(item.encode("utf-8", errors="replace"), digest_size=8).digest()
        h = int.from_bytes(digest, "big")
        idx = h & self.mask
        w = h >> self.p
        rho = 1
        for i in range(64 - self.p):
            if (w >> i) & 1:
                break
            rho += 1
        if rho > self.registers[idx]:
            self.registers[idx] = rho

    def count(self) -> int:
        alpha = self._alpha(self.m)
        raw = alpha * self.m * self.m / sum(2.0 ** -r for r in self.registers)
        if raw <= 2.5 * self.m:
            zeros = self.registers.count(0)
            if zeros:
                raw = self.m * math.log(self.m / zeros)
        return int(round(raw))

    @staticmethod
    def _alpha(m: int) -> float:
        if m == 16:
            return 0.673
        if m == 32:
            return 0.708
        if m == 64:
            return 0.7925
        return 0.549 / (1.0 - m ** -0.9 * 0.579)


class BoundedSet:
    """Exact distinct values up to a limit, HLL estimate beyond it."""

    def __init__(self, limit: int = 2000, p: int = 12):
        self.limit = limit
        self.exact: set[str] = set()
        self.hll = HyperLogLog(p)
        self.overflow = 0

    def add(self, item: str) -> None:
        self.hll.add(item)
        if len(self.exact) < self.limit:
            self.exact.add(item)
        else:
            self.overflow += 1

    @property
    def exact_count(self) -> int:
        return len(self.exact)

    def estimate(self) -> int:
        return self.hll.count()

    def sample(self, n: int = 5) -> list[str]:
        return list(self.exact)[:n]
