import tempfile
import unittest
from pathlib import Path
from unittest import mock

from etop_tui.gpu import (
    GpuMonitor,
    GpuSample,
    SysfsGpuBackend,
    parse_nvidia_csv,
    parse_nvidia_task_pids,
    read_nvidia_gpu_cores,
)

NVIDIA_CSV = """0, NVIDIA GeForce RTX 4090, 42, 1234, 24564, 54
1, NVIDIA A100-SXM4-80GB, [N/A], [N/A], 81920, [N/A]
"""


class NvidiaParseTests(unittest.TestCase):
    def test_parse(self):
        gpus = parse_nvidia_csv(NVIDIA_CSV)
        self.assertEqual(len(gpus), 2)
        first, second = gpus
        self.assertEqual(first.index, 0)
        self.assertEqual(first.name, "NVIDIA GeForce RTX 4090")
        self.assertEqual(first.util, 42.0)
        self.assertEqual(first.mem_used, 1234 * 1024 * 1024)
        self.assertEqual(first.mem_total, 24564 * 1024 * 1024)
        self.assertEqual(first.temp, 54.0)
        self.assertIsNone(second.util)
        self.assertIsNone(second.mem_used)
        self.assertIsNone(second.temp)
        self.assertAlmostEqual(
            first.mem_pct, 1234 / 24564 * 100, places=4
        )

    def test_parse_garbage(self):
        self.assertEqual(parse_nvidia_csv(""), [])
        self.assertEqual(parse_nvidia_csv("not,a,gpu"), [])

    def test_cores_default_to_none(self):
        gpus = parse_nvidia_csv(NVIDIA_CSV)
        self.assertIsNone(gpus[0].cores)

    def test_task_pids(self):
        self.assertEqual(parse_nvidia_task_pids("123\n456\n123\n"), 2)
        self.assertEqual(parse_nvidia_task_pids(""), 0)
        self.assertEqual(parse_nvidia_task_pids("N/A\n"), 0)


class _FakeNvml:
    """Minimal stand-in for libnvidia-ml driven through ctypes byref."""

    def __init__(self, cores, init_result=0, cores_result=0):
        self._cores = cores
        self._init_result = init_result
        self._cores_result = cores_result

    def nvmlInit_v2(self):
        return self._init_result

    def nvmlDeviceGetHandleByIndex(self, index, out):
        if index not in self._cores:
            return 2  # NVML_ERROR_NOT_SUPPORTED-ish: stop enumerating
        out._obj.value = index + 100
        return 0

    def nvmlDeviceGetNumGpuCores(self, handle, out):
        if self._cores_result != 0:
            return self._cores_result
        # ctypes hands the c_void_p through unchanged to Python callables.
        index = getattr(handle, "value", handle) - 100
        out._obj.value = self._cores[index]
        return 0

    def nvmlShutdown(self):
        return 0


class NvmlCoreTests(unittest.TestCase):
    def test_reads_core_counts_per_index(self):
        fake = _FakeNvml({0: 16384, 1: 9728})
        self.assertEqual(
            read_nvidia_gpu_cores(library=fake), {0: 16384, 1: 9728}
        )

    def test_init_failure_returns_empty(self):
        fake = _FakeNvml({0: 16384}, init_result=1)
        self.assertEqual(read_nvidia_gpu_cores(library=fake), {})

    def test_core_query_failure_skips_gpu(self):
        fake = _FakeNvml({0: 16384}, cores_result=3)
        self.assertEqual(read_nvidia_gpu_cores(library=fake), {})

    def test_missing_library_returns_empty(self):
        with mock.patch("etop_tui.gpu._nvml_library", return_value=None):
            self.assertEqual(read_nvidia_gpu_cores(), {})

    def test_no_library_on_this_machine(self):
        # Whatever the host exposes, the call must never raise.
        self.assertIsInstance(read_nvidia_gpu_cores(), dict)


class SysfsTests(unittest.TestCase):
    def _make_card(self, root: Path, index: int, vendor: str = "0x1002") -> None:
        device = root / f"card{index}" / "device"
        device.mkdir(parents=True)
        (device / "gpu_busy_percent").write_text("37\n")
        (device / "mem_info_vram_used").write_text(str(2 * 1024 ** 3))
        (device / "mem_info_vram_total").write_text(str(8 * 1024 ** 3))
        (device / "product_name").write_text("Radeon RX 7800 XT\n")
        (device / "vendor").write_text(vendor + "\n")
        hwmon = device / "hwmon" / "hwmon0"
        hwmon.mkdir(parents=True)
        (hwmon / "temp1_input").write_text("42500\n")

    def test_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_card(root, 0)
            backend = SysfsGpuBackend(str(root))
            self.assertTrue(backend.available())
            gpus = backend.sample()
        self.assertEqual(len(gpus), 1)
        gpu = gpus[0]
        self.assertEqual(gpu.vendor, "amd")
        self.assertEqual(gpu.name, "Radeon RX 7800 XT")
        self.assertEqual(gpu.util, 37.0)
        self.assertEqual(gpu.mem_used, 2 * 1024 ** 3)
        self.assertEqual(gpu.mem_total, 8 * 1024 ** 3)
        self.assertAlmostEqual(gpu.temp, 42.5)
        self.assertAlmostEqual(gpu.mem_pct, 25.0)

    def test_missing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = SysfsGpuBackend(str(Path(directory) / "nope"))
            self.assertFalse(backend.available())
            self.assertEqual(backend.sample(), [])


class _EmptyBackend:
    def sample(self):
        return []


class MonitorTests(unittest.TestCase):
    def test_keeps_last_sample_when_driver_fails(self):
        class _Good:
            def __init__(self):
                self.calls = 0

            def sample(self):
                self.calls += 1
                if self.calls > 1:
                    return []
                return [GpuSample("test", 0, "Test GPU", 10.0, 1, 2, 30.0)]

        monitor = GpuMonitor([_Good()])
        first = monitor.sample()
        second = monitor.sample()
        self.assertFalse(first[0].stale)
        self.assertTrue(second[0].stale)
        self.assertEqual(second[0].util, 10.0)

    def test_empty(self):
        monitor = GpuMonitor([_EmptyBackend()])
        self.assertEqual(monitor.sample(), [])
        self.assertIsNone(monitor.task_count())

    def test_task_count_sums_backends(self):
        class _Tasks:
            def sample(self):
                return []

            def task_count(self):
                return 3

        monitor = GpuMonitor([_Tasks(), _Tasks()])
        self.assertEqual(monitor.task_count(), 6)


if __name__ == "__main__":
    unittest.main()
