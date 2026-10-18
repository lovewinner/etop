"""Collect CPU and memory utilisation from /proc."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class CpuTimes:
    """Raw jiffy counters for one CPU (or the aggregate)."""

    user: int
    nice: int
    system: int
    idle: int
    iowait: int
    irq: int
    softirq: int
    steal: int

    @property
    def total(self) -> int:
        return (
            self.user
            + self.nice
            + self.system
            + self.idle
            + self.iowait
            + self.irq
            + self.softirq
            + self.steal
        )

    @property
    def busy(self) -> int:
        # iowait is time the CPU was idle waiting for I/O; htop counts it as
        # idle, and doing the same keeps our numbers comparable.
        return self.total - self.idle - self.iowait


@dataclass(frozen=True)
class CpuSample:
    total: float
    cores: Tuple[float, ...]


@dataclass(frozen=True)
class MemSample:
    total: int
    free: int
    available: int
    buffers: int
    cached: int
    swap_total: int
    swap_free: int

    @property
    def used(self) -> int:
        return max(0, self.total - self.available)

    @property
    def used_pct(self) -> float:
        return 100.0 * self.used / self.total if self.total else 0.0

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)

    @property
    def swap_pct(self) -> float:
        return 100.0 * self.swap_used / self.swap_total if self.swap_total else 0.0

    @property
    def cache_total(self) -> int:
        return self.cached + self.buffers


def read_cpu_times(path: str = "/proc/stat") -> Tuple[CpuTimes, List[CpuTimes]]:
    """Return (aggregate, per-core) counters from /proc/stat."""
    overall: Optional[CpuTimes] = None
    cores: List[Tuple[int, CpuTimes]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.startswith("cpu"):
                break
            fields = line.split()
            name = fields[0]
            if name == "cpu":
                index: Optional[int] = None
            elif name[3:].isdigit():
                index = int(name[3:])
            else:
                continue
            values = [int(value) for value in fields[1:9]]
            values += [0] * (8 - len(values))
            times = CpuTimes(*values[:8])
            if index is None:
                overall = times
            else:
                cores.append((index, times))
    if overall is None:
        raise OSError("no aggregate 'cpu' line in " + path)
    cores.sort(key=lambda item: item[0])
    return overall, [times for _, times in cores]


def _utilisation(previous: CpuTimes, current: CpuTimes) -> float:
    delta_total = current.total - previous.total
    if delta_total <= 0:
        return 0.0
    delta_busy = current.busy - previous.busy
    return max(0.0, min(100.0, 100.0 * delta_busy / delta_total))


class CpuCollector:
    """Stateful CPU sampler; percentages are deltas between calls."""

    def __init__(self, path: str = "/proc/stat") -> None:
        self._path = path
        self._previous: Optional[Tuple[CpuTimes, List[CpuTimes]]] = None

    def sample(self) -> CpuSample:
        overall, cores = read_cpu_times(self._path)
        if self._previous is None:
            # First frame: report the average since boot rather than 0%.
            total = 100.0 * overall.busy / overall.total if overall.total else 0.0
            per_core = tuple(
                100.0 * times.busy / times.total if times.total else 0.0
                for times in cores
            )
        else:
            previous_overall, previous_cores = self._previous
            total = _utilisation(previous_overall, overall)
            if len(previous_cores) == len(cores):
                per_core = tuple(
                    _utilisation(previous, current)
                    for previous, current in zip(previous_cores, cores)
                )
            else:
                # CPU hotplug changed the layout; fall back to since-boot values.
                per_core = tuple(
                    100.0 * times.busy / times.total if times.total else 0.0
                    for times in cores
                )
        self._previous = (overall, cores)
        return CpuSample(total, per_core)


_MEMINFO_FIELDS = {
    "MemTotal": "total",
    "MemFree": "free",
    "MemAvailable": "available",
    "Buffers": "buffers",
    "Cached": "cached",
    "SwapTotal": "swap_total",
    "SwapFree": "swap_free",
}


def read_meminfo(path: str = "/proc/meminfo") -> MemSample:
    """Read the handful of /proc/meminfo fields we display."""
    values = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            key, _, rest = line.partition(":")
            field = _MEMINFO_FIELDS.get(key)
            if field is None:
                continue
            parts = rest.split()
            if not parts:
                continue
            values[field] = int(parts[0]) * 1024
    if "total" not in values:
        raise OSError("no MemTotal in " + path)
    if "available" not in values:
        # Very old kernels: approximate with free + buffers + cached.
        values["available"] = (
            values.get("free", 0) + values.get("buffers", 0) + values.get("cached", 0)
        )
    return MemSample(
        total=values["total"],
        free=values.get("free", 0),
        available=values["available"],
        buffers=values.get("buffers", 0),
        cached=values.get("cached", 0),
        swap_total=values.get("swap_total", 0),
        swap_free=values.get("swap_free", 0),
    )


def read_uptime(path: str = "/proc/uptime") -> float:
    """Return system uptime in seconds."""
    with open(path, "r", encoding="utf-8") as handle:
        return float(handle.read().split()[0])


def read_task_counts(path: str = "/proc/loadavg") -> Tuple[int, int]:
    """Return (running, total) task counts from /proc/loadavg."""
    with open(path, "r", encoding="utf-8") as handle:
        parts = handle.read().split()
    running, total = parts[3].split("/", 1)
    return int(running), int(total)
