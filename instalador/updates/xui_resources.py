"""Hardware budgets for the native XUI 1.5.5 installation (MiB)."""
import math
import os
from pathlib import Path


def effective_memory():
    total = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:'))) // 1024
    for path in ['/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes']:
        try:
            limit = int(Path(path).read_text().strip()) // 1048576
            if limit > 0: total = min(total, limit)
        except (OSError, ValueError): pass
    return total


def effective_cpus():
    count = len(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else (os.cpu_count() or 1)
    try:
        quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
        if quota != 'max': count = min(count, max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError): pass
    return max(1, count)


def budgets(memory_mb, cpus, pools):
    if memory_mb < 4096: raise ValueError('São necessários pelo menos 4 GiB de RAM para este pacote nativo.')
    if cpus < 1 or pools < 1: raise ValueError('CPU/grupos PHP inválidos.')
    reserve = max(1024, memory_mb * 8 // 100)
    mysql = min(20480, max(512, memory_mb * 16 // 100))
    xui = min(98304, memory_mb * 76 // 100, memory_mb - reserve - mysql - 256)
    redis = min(8192, max(128, xui // 12))
    children = min(48, max(1, (xui * 40 // 100) // (128 * pools)))
    connections = min(1024, max(128, pools * children * 3 + 160))
    pending = min(160, max(8, pools * children * 2))
    return dict(memory_mb=memory_mb, cpus=cpus, pools=pools, os_reserve_mb=reserve,
                mysql_mb=mysql, xui_mb=xui, redis_mb=redis, proxy_mb=256,
                innodb_mb=min(10240, mysql // 2), php_children=children,
                mysql_connections=connections, open_pending=pending,
                channel_pending=min(24, max(2, pending // 4)),
                open_rate=min(200, max(20, pools * children * 2)),
                nginx_workers=min(8, cpus), aio_threads=min(8, max(2, cpus)),
                http_port=80, version=2)
