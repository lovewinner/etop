"""Collect CPU and memory utilisation from /proc."""

from __future__ import annotations

import fcntl
import glob
import os
import socket
import struct
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


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
        # Everything except pure idle. iowait counts as busy here (htop's
        # detailed CPU mode does the same) because it gets its own coloured
        # segment in the bar; htop's default mode hides it inside idle.
        return self.total - self.idle


@dataclass(frozen=True)
class CpuSample:
    total: float
    cores: Tuple[float, ...]
    # Breakdown of busy time as percentages of the same interval. The
    # values sum exactly to total; idle and iowait are intentionally left
    # out because they are not drawn in the CPU bar.
    user: float = 0.0
    nice: float = 0.0
    system: float = 0.0
    irq: float = 0.0
    steal: float = 0.0
    iowait: float = 0.0
    # Hottest CPU sensor in °C, or None on machines without sensors (VMs).
    temp: Optional[float] = None


@dataclass(frozen=True)
class MemSample:
    total: int
    free: int
    buffers: int
    cached: int
    swap_total: int
    swap_free: int
    sreclaimable: int = 0
    shmem: int = 0

    @property
    def used(self) -> int:
        """Non-cache, non-shared memory, matching htop's used segment."""
        value = (
            self.total
            - self.free
            - self.cached
            - self.sreclaimable
            - self.buffers
        )
        return max(0, value)

    @property
    def shared(self) -> int:
        return max(0, self.shmem)

    @property
    def cache(self) -> int:
        """Reclaimable cache, excluding the shared memory already shown."""
        return max(0, self.cached + self.sreclaimable - self.shmem)

    @property
    def used_total(self) -> int:
        """Value shown in the text: used plus shared memory."""
        return self.used + self.shared

    @property
    def used_pct(self) -> float:
        return 100.0 * self.used_total / self.total if self.total else 0.0

    @property
    def swap_used(self) -> int:
        return max(0, self.swap_total - self.swap_free)

    @property
    def swap_pct(self) -> float:
        return 100.0 * self.swap_used / self.swap_total if self.swap_total else 0.0

    @property
    def cache_total(self) -> int:
        return self.cache + self.buffers


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


def _category_percentages(
    previous: Optional[CpuTimes], current: CpuTimes
) -> Tuple[float, float, float, float, float, float]:
    """Split non-idle CPU time into user/nice/system/irq/steal/iowait."""
    if previous is None:
        total = current.total
        user, nice = current.user, current.nice
        system = current.system
        irq = current.irq + current.softirq
        steal = current.steal
        iowait = current.iowait
    else:
        total = current.total - previous.total
        user = current.user - previous.user
        nice = current.nice - previous.nice
        system = current.system - previous.system
        irq = (current.irq + current.softirq) - (
            previous.irq + previous.softirq
        )
        steal = current.steal - previous.steal
        iowait = current.iowait - previous.iowait
    if total <= 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    scale = 100.0 / total
    return (
        max(0.0, user * scale),
        max(0.0, nice * scale),
        max(0.0, system * scale),
        max(0.0, irq * scale),
        max(0.0, steal * scale),
        max(0.0, iowait * scale),
    )


_CPU_HWMON_NAMES = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu"}


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_temp_input(path: str) -> Optional[float]:
    """Read a millidegree sysfs input, rejecting implausible values."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        milli = int(text)
    except ValueError:
        return None
    celsius = milli / 1000.0
    if 0.0 < celsius < 150.0:
        return celsius
    return None


def read_cpu_temp(
    hwmon_root: str = "/sys/class/hwmon",
    thermal_root: str = "/sys/class/thermal",
) -> Optional[float]:
    """Return the hottest CPU temperature in °C, or None when unavailable.

    Prefers hwmon sensors driven by coretemp/k10temp and friends; falls back
    to thermal zones whose type mentions the CPU package. Machines without
    any CPU sensor (most VMs) simply return None.
    """
    temps: List[float] = []
    for hwmon in sorted(glob.glob(os.path.join(hwmon_root, "hwmon*"))):
        name = _read_text(os.path.join(hwmon, "name"))
        if name not in _CPU_HWMON_NAMES:
            continue
        for input_path in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
            value = _read_temp_input(input_path)
            if value is not None:
                temps.append(value)
    if temps:
        return max(temps)
    for zone in sorted(glob.glob(os.path.join(thermal_root, "thermal_zone*"))):
        ztype = (_read_text(os.path.join(zone, "type")) or "").lower()
        if not any(key in ztype for key in ("cpu", "pkg", "package")):
            continue
        value = _read_temp_input(os.path.join(zone, "temp"))
        if value is not None:
            temps.append(value)
    return max(temps) if temps else None


class CpuCollector:
    """Stateful CPU sampler; percentages are deltas between calls."""

    def __init__(
        self,
        path: str = "/proc/stat",
        temp_reader: Optional[Callable[[], Optional[float]]] = None,
    ) -> None:
        self._path = path
        self._temp_reader = temp_reader or read_cpu_temp
        self._previous: Optional[Tuple[CpuTimes, List[CpuTimes]]] = None

    def sample(self) -> CpuSample:
        overall, cores = read_cpu_times(self._path)
        previous_overall = self._previous[0] if self._previous else None
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
        breakdown = _category_percentages(previous_overall, overall)
        self._previous = (overall, cores)
        return CpuSample(
            total, per_core, *breakdown, temp=self._temp_reader()
        )


_MEMINFO_FIELDS = {
    "MemTotal": "total",
    "MemFree": "free",
    "Buffers": "buffers",
    "Cached": "cached",
    "SReclaimable": "sreclaimable",
    "Shmem": "shmem",
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
    return MemSample(
        total=values["total"],
        free=values.get("free", 0),
        buffers=values.get("buffers", 0),
        cached=values.get("cached", 0),
        swap_total=values.get("swap_total", 0),
        swap_free=values.get("swap_free", 0),
        sreclaimable=values.get("sreclaimable", 0),
        shmem=values.get("shmem", 0),
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


def interface_ipv4(name: str) -> str:
    """Return the IPv4 address configured on *name*, or "" if unavailable."""
    request = struct.pack("256s", name.encode("utf-8")[:15])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        result = fcntl.ioctl(sock.fileno(), 0x8915, request)  # SIOCGIFADDR
    except OSError:
        return ""
    finally:
        sock.close()
    return socket.inet_ntoa(result[20:24])


def read_default_ipv4(
    path: str = "/proc/net/route",
    address_lookup: Optional[Callable[[str], str]] = None,
) -> str:
    """Return the IPv4 address of the interface carrying the default route."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            rows = handle.readlines()[1:]
    except OSError:
        return ""
    lookup = address_lookup or interface_ipv4
    for row in rows:
        fields = row.split()
        if len(fields) < 4 or fields[1] != "00000000":
            continue
        try:
            flags = int(fields[3], 16)
        except ValueError:
            continue
        if not flags & 0x1:  # RTF_UP
            continue
        return lookup(fields[0])
    return ""
