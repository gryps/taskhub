import os
import shutil
import time
from pathlib import Path


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
