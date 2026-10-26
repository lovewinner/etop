"""GPU discovery and sampling.

Supported sources, in order:

* NVIDIA  -- `nvidia-smi --query-gpu=...` (works with the proprietary driver)
* sysfs   -- /sys/class/drm/card*/device/gpu_busy_percent (amdgpu, some others)

If no source is available the GPU panels are simply omitted.
"""

from __future__ import annotations

import ctypes
import glob
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional

GIB = 1024 ** 3

NVIDIA_FIELDS = (
    "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu"
)


@dataclass(frozen=True)
class GpuSample:
    vendor: str
    index: int
    name: str
    util: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    temp: Optional[float] = None
    # CUDA core count (NVIDIA only, via NVML); None when unavailable.
    cores: Optional[int] = None
    stale: bool = False

    @property
    def mem_pct(self) -> Optional[float]:
        if self.mem_used is None or not self.mem_total:
            return None
        return max(0.0, min(100.0, 100.0 * self.mem_used / self.mem_total))


def _number(text: str) -> Optional[float]:
    text = text.strip()
    if not text or text.startswith("["):  # nvidia-smi prints [N/A]
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_nvidia_task_pids(text: str) -> int:
    """Count unique PIDs from nvidia-smi compute-apps output."""
    pids = {
        int(line.strip())
        for line in text.splitlines()
        if line.strip().isdigit()
    }
    return len(pids)


def parse_nvidia_csv(text: str) -> List[GpuSample]:
    """Parse `nvidia-smi --format=csv,noheader,nounits` output."""
    gpus: List[GpuSample] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        util = _number(parts[2])
        used = _number(parts[3]) if len(parts) > 3 else None
        total = _number(parts[4]) if len(parts) > 4 else None
        temp = _number(parts[5]) if len(parts) > 5 else None
        gpus.append(
            GpuSample(
                vendor="nvidia",
                index=index,
                name=parts[1] or "NVIDIA GPU",
                util=util,
                mem_used=int(used * 1024 * 1024) if used is not None else None,
                mem_total=int(total * 1024 * 1024) if total is not None else None,
                temp=temp,
            )
        )
    return gpus


class NvidiaBackend:
    vendor = "nvidia"

    def __init__(self, executable: Optional[str] = None) -> None:
        self.executable = executable or shutil.which("nvidia-smi")
        self._tasks_at = -1.0
        self._tasks_value: Optional[int] = None
        self._cores: Optional[Dict[int, int]] = None

    def available(self) -> bool:
        return bool(self.executable)

    def _core_counts(self) -> Dict[int, int]:
        # The core count is a constant: read it once, never refresh.
        if self._cores is None:
            self._cores = read_nvidia_gpu_cores()
        return self._cores

    def sample(self) -> List[GpuSample]:
        if not self.executable:
            return []
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "--query-gpu=" + NVIDIA_FIELDS,
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if completed.returncode != 0:
            return []
        gpus = parse_nvidia_csv(completed.stdout)
        cores = self._core_counts()
        if cores:
            gpus = [replace(gpu, cores=cores.get(gpu.index)) for gpu in gpus]
        return gpus

    def task_count(self) -> Optional[int]:
        """Number of unique compute processes across all GPUs."""
        if not self.executable:
            return None
        now = time.monotonic()
        if now - self._tasks_at < 1.0:
            return self._tasks_value
        try:
            completed = subprocess.run(
                [
                    self.executable,
                    "--query-compute-apps=pid",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return self._tasks_value
        if completed.returncode != 0:
            return self._tasks_value
        self._tasks_at = now
        self._tasks_value = parse_nvidia_task_pids(completed.stdout)
        return self._tasks_value


_NVML_CORE_LIMIT = 64


def _nvml_library() -> Optional[object]:
    """Load the NVML shared library, or None when unavailable."""
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


def read_nvidia_gpu_cores(library: Optional[object] = None) -> Dict[int, int]:
    """Return {GPU index: CUDA core count} via NVML, or {} when unavailable.

    nvidia-smi has no core-count field, but NVML exposes
    nvmlDeviceGetNumGpuCores. Driving it through ctypes keeps etop free of
    third-party dependencies; any failure simply means no core count.
    """
    lib = library if library is not None else _nvml_library()
    if lib is None:
        return {}
    try:
        init = getattr(lib, "nvmlInit_v2", None) or getattr(
            lib, "nvmlInit", None
        )
        if init is None or init() != 0:
            return {}
        counts: Dict[int, int] = {}
        try:
            for index in range(_NVML_CORE_LIMIT):
                handle = ctypes.c_void_p()
                if (
                    lib.nvmlDeviceGetHandleByIndex(index, ctypes.byref(handle))
                    != 0
                ):
                    break
                cores = ctypes.c_uint(0)
                if (
                    lib.nvmlDeviceGetNumGpuCores(handle, ctypes.byref(cores))
                    == 0
                    and cores.value
                ):
                    counts[index] = cores.value
        finally:
            shutdown = getattr(lib, "nvmlShutdown", None)
            if shutdown is not None:
                shutdown()
        return counts
    except Exception:
        return {}


def _read(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> Optional[int]:
    value = _read(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


_VENDOR_NAMES = {"0x1002": "amd", "0x8086": "intel", "0x10de": "nvidia"}


class SysfsGpuBackend:
    """Reads the generic DRM sysfs counters exposed by amdgpu and friends."""

    vendor = "sysfs"

    def __init__(self, root: str = "/sys/class/drm") -> None:
        self.root = root
        self._tasks_at = -1.0
        self._tasks_value: Optional[int] = None

    def _devices(self) -> List[str]:
        devices: List[str] = []
        seen = set()
        for card in sorted(glob.glob(os.path.join(self.root, "card[0-9]*"))):
            device = os.path.join(card, "device")
            if not os.path.exists(os.path.join(device, "gpu_busy_percent")):
                continue
            real = os.path.realpath(device)
            if real in seen:
                continue
            seen.add(real)
            devices.append(device)
        return devices

    def available(self) -> bool:
        return bool(self._devices())

    def task_count(self) -> Optional[int]:
        """Best-effort count of processes holding a /dev/dri file descriptor."""
        now = time.monotonic()
        if now - self._tasks_at < 1.0:
            return self._tasks_value
        pids = set()
        for fd_dir in glob.glob("/proc/[0-9]*/fd"):
            pid = fd_dir.split("/")[2]
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        target = os.readlink(os.path.join(fd_dir, fd))
                    except OSError:
                        continue
                    if target.startswith("/dev/dri/"):
                        pids.add(pid)
                        break
            except OSError:
                continue
        self._tasks_at = now
        self._tasks_value = len(pids)
        return self._tasks_value

    def sample(self) -> List[GpuSample]:
        gpus: List[GpuSample] = []
        for index, device in enumerate(self._devices()):
            vendor_id = (_read(os.path.join(device, "vendor")) or "").lower()
            vendor = _VENDOR_NAMES.get(vendor_id, "drm")
            name = _read(os.path.join(device, "product_name")) or f"{vendor.upper()} GPU"
            util = _read_int(os.path.join(device, "gpu_busy_percent"))
            used = _read_int(os.path.join(device, "mem_info_vram_used"))
            total = _read_int(os.path.join(device, "mem_info_vram_total"))
            temp: Optional[float] = None
            for temp_path in sorted(
                glob.glob(os.path.join(device, "hwmon", "hwmon*", "temp1_input"))
            ):
                milli = _read_int(temp_path)
                if milli is not None:
                    temp = milli / 1000.0
                    break
            gpus.append(
                GpuSample(
                    vendor=vendor,
                    index=index,
                    name=name,
                    util=float(util) if util is not None else None,
                    mem_used=used,
                    mem_total=total,
                    temp=temp,
                )
            )
        return gpus


class DemoBackend:
    """Synthetic GPU used by `--demo` to preview the GPU/VRAM panels."""

    vendor = "demo"

    def __init__(self) -> None:
        self._tick = 0

    def available(self) -> bool:
        return True

    def task_count(self) -> Optional[int]:
        return 1

    def sample(self) -> List[GpuSample]:
        self._tick += 1
        util = 55.0 + 35.0 * math.sin(self._tick / 4.0)
        used = int((9.5 + 5.5 * math.sin(self._tick / 7.0 + 1.0)) * GIB)
        return [
            GpuSample(
                vendor="demo",
                index=0,
                name="Demo GPU (synthetic)",
                util=max(0.0, min(100.0, util)),
                mem_used=max(0, min(16 * GIB, used)),
                mem_total=16 * GIB,
                temp=51.0 + 4.0 * math.sin(self._tick / 9.0),
                cores=16384,
            )
        ]


class GpuMonitor:
    """Samples all backends and survives transient driver hiccups."""

    def __init__(self, backends: Iterable[object]) -> None:
        self.backends = list(backends)
        self._last: List[GpuSample] = []

    def sample(self) -> List[GpuSample]:
        samples: List[GpuSample] = []
        for backend in self.backends:
            try:
                samples.extend(backend.sample())
            except Exception:
                # A backend must never take the whole UI down.
                continue
        if not samples and self._last:
            return [replace(gpu, stale=True) for gpu in self._last]
        if samples:
            self._last = samples
        return samples

    def task_count(self) -> Optional[int]:
        counts = []
        for backend in self.backends:
            counter = getattr(backend, "task_count", None)
            if counter is None:
                continue
            try:
                value = counter()
            except Exception:
                continue
            if value is not None:
                counts.append(value)
        return sum(counts) if counts else None


def detect_backends(explicit: str = "auto") -> List[object]:
    """Return the GPU backends selected by --gpu."""
    if explicit == "none":
        return []
    if explicit == "nvidia":
        return [NvidiaBackend()]
    if explicit == "sysfs":
        return [SysfsGpuBackend()]
    backends: List[object] = []
    nvidia = NvidiaBackend()
    if nvidia.available():
        backends.append(nvidia)
    sysfs = SysfsGpuBackend()
    if sysfs.available():
        backends.append(sysfs)
    return backends
