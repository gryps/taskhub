import asyncio
import os
import shutil
import threading
import time
from collections import deque
from contextlib import suppress
from pathlib import Path

PEAK_FIELDS = ("cpu_percent", "load_average_1m", "memory_used_percent", "disk_used_percent")


class RollingLoadSampler:
    def __init__(self, window_seconds: int = 300):
        self.window_seconds = window_seconds
        self._samples: deque[tuple[float, dict[str, float | int | None]]] = deque()
        self._lock = threading.Lock()
        self._collector: asyncio.Task | None = None

    async def start(self, root: Path, interval_seconds: int = 5) -> None:
        await asyncio.to_thread(self.sample, root)
        self._collector = asyncio.create_task(self._collect(root, interval_seconds))

    async def stop(self) -> None:
        if self._collector is None:
            return
        self._collector.cancel()
        with suppress(asyncio.CancelledError):
            await self._collector
        self._collector = None

    async def _collect(self, root: Path, interval_seconds: int) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(self.sample, root)

    def sample(self, root: Path) -> dict[str, float | int | None]:
        current = system_load(root)
        self.record(current)
        return current

    def record(
        self,
        sample: dict[str, float | int | None],
        sampled_at: float | None = None,
    ) -> None:
        now = time.monotonic() if sampled_at is None else sampled_at
        with self._lock:
            self._samples.append((now, sample))
            self._discard_expired(now)

    def peak(self, sampled_at: float | None = None) -> dict[str, float | int | None]:
        now = time.monotonic() if sampled_at is None else sampled_at
        with self._lock:
            self._discard_expired(now)
            if not self._samples:
                return {"window_seconds": self.window_seconds, "sample_count": 0}
            latest = dict(self._samples[-1][1])
            for field in PEAK_FIELDS:
                values = [
                    sample[field]
                    for _, sample in self._samples
                    if sample.get(field) is not None
                ]
                latest[field] = max(values) if values else None
            latest["window_seconds"] = self.window_seconds
            latest["sample_count"] = len(self._samples)
            return latest

    def _discard_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


def system_load(root: Path) -> dict[str, float | int | None]:
    disk = shutil.disk_usage(root)
    cpu_percent = _cpu_percent()
    total_memory, available_memory = _memory_bytes()
    return {
        "cpu_percent": cpu_percent,
        "load_average_1m": _load_average(),
        "memory_used_percent": _used_percent(total_memory, available_memory),
        "memory_total_bytes": total_memory,
        "memory_available_bytes": available_memory,
        "disk_used_percent": _used_percent(disk.total, disk.free),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
    }


def _cpu_percent() -> float | None:
    if os.name == "nt":
        return _windows_cpu_percent()
    first = _proc_cpu_times()
    if first is None:
        return None
    time.sleep(0.1)
    second = _proc_cpu_times()
    if second is None:
        return None
    idle = second[0] - first[0]
    total = second[1] - first[1]
    return round(max(0.0, min(100.0, 100 * (1 - idle / total))), 1) if total else 0.0


def _proc_cpu_times() -> tuple[int, int] | None:
    try:
        first_line = Path("/proc/stat").read_text().splitlines()[0]
        values = [int(value) for value in first_line.split()[1:]]
    except (OSError, ValueError, IndexError):
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _memory_bytes() -> tuple[int | None, int | None]:
    if os.name == "nt":
        return _windows_memory_bytes()
    try:
        values = {
            parts[0].rstrip(":"): int(parts[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if len(parts := line.split()) >= 2
        }
        return values["MemTotal"], values["MemAvailable"]
    except (OSError, ValueError, KeyError):
        return None, None


def _windows_memory_bytes() -> tuple[int | None, int | None]:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in ("total", "available", "page_total", "page_available",
                             "virtual_total", "virtual_available", "extended_available")
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None, None
        return int(status.total), int(status.available)
    except (AttributeError, OSError):
        return None, None


def _windows_cpu_percent() -> float | None:
    try:
        import ctypes

        def snapshot() -> tuple[int, int]:
            idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
            ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
            return idle.value, kernel.value + user.value

        first = snapshot()
        time.sleep(0.1)
        second = snapshot()
        total = second[1] - first[1]
        return round(100 * (1 - (second[0] - first[0]) / total), 1) if total else 0.0
    except (AttributeError, OSError):
        return None


def _load_average() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return None


def _used_percent(total: int | None, free: int | None) -> float | None:
    if not total or free is None:
        return None
    return round(100 * (total - free) / total, 1)
