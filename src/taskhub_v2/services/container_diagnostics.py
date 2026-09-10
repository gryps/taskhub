def docker_log_text(raw: bytes) -> str:
    if not raw:
        return ""
    chunks = []
    offset = 0
    while offset + 8 <= len(raw) and raw[offset] in {0, 1, 2}:
        size = int.from_bytes(raw[offset + 4 : offset + 8], "big")
        offset += 8
        chunks.append(raw[offset : offset + size])
        offset += size
    payload = b"".join(chunks) if chunks else raw
    return payload.decode("utf-8", errors="replace")[-20_000:]


def container_resources(stats: dict) -> dict:
    memory = stats.get("memory_stats") or {}
    cpu = stats.get("cpu_stats") or {}
    previous = stats.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (
        (previous.get("cpu_usage") or {}).get("total_usage", 0)
    )
    system_delta = cpu.get("system_cpu_usage", 0) - previous.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(
        (cpu.get("cpu_usage") or {}).get("percpu_usage") or []
    ) or 1
    cpu_percent = 100.0 * cpu_delta / system_delta * online if system_delta > 0 else None
    return {
        "cpu_percent": round(cpu_percent, 1) if cpu_percent is not None else None,
        "memory_used_bytes": memory.get("usage"),
        "memory_limit_bytes": memory.get("limit"),
    }
