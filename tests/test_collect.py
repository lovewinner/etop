import tempfile
import unittest
from pathlib import Path

from etop_tui.collect import (
    CpuCollector,
    MemSample,
    read_cpu_freq,
    read_cpu_temp,
    read_cpu_times,
    read_default_ipv4,
    read_meminfo,
    read_task_counts,
)

STAT_A = """cpu  100 0 100 700 100 0 0 0 0 0
cpu0 100 0 100 700 100 0 0 0 0 0
cpu1 50 0 50 800 100 0 0 0 0 0
intr 123 0 0
ctxt 456
btime 1600000000
processes 789
procs_running 2
procs_blocked 0
"""

STAT_B = """cpu  200 0 100 800 100 0 0 0 0 0
cpu0 200 0 100 800 100 0 0 0 0 0
cpu1 100 0 50 900 100 0 0 0 0 0
intr 124 0 0
ctxt 457
btime 1600000000
processes 790
procs_running 1
procs_blocked 0
"""

MEMINFO = """MemTotal:       16384000 kB
MemFree:         1000000 kB
MemAvailable:    8192000 kB
Buffers:          200000 kB
Cached:          4000000 kB
SReclaimable:    1000000 kB
Shmem:            400000 kB
SwapCached:            0 kB
SwapTotal:       2048000 kB
SwapFree:        1024000 kB
"""


class CpuCollectorTests(unittest.TestCase):
    def test_parse_and_first_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stat"
            path.write_text(STAT_A)
            collector = CpuCollector(str(path), temp_reader=lambda: None)
            sample = collector.sample()
        self.assertAlmostEqual(sample.total, 30.0)  # 300 busy / 1000 total
        self.assertEqual(len(sample.cores), 2)
        self.assertAlmostEqual(sample.cores[0], 30.0)
        self.assertAlmostEqual(sample.cores[1], 20.0)
        self.assertAlmostEqual(sample.user, 10.0)
        self.assertAlmostEqual(sample.nice, 0.0)
        self.assertAlmostEqual(sample.system, 10.0)
        self.assertAlmostEqual(sample.irq, 0.0)
        self.assertAlmostEqual(sample.steal, 0.0)
        self.assertAlmostEqual(sample.iowait, 10.0)

    def test_delta_between_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stat"
            path.write_text(STAT_A)
            collector = CpuCollector(str(path), temp_reader=lambda: None)
            collector.sample()
            path.write_text(STAT_B)
            sample = collector.sample()
        self.assertAlmostEqual(sample.total, 50.0)  # 100 busy / 200 total
        self.assertAlmostEqual(sample.cores[0], 50.0)
        self.assertAlmostEqual(sample.cores[1], 50 / 150 * 100)
        self.assertAlmostEqual(sample.user, 50.0)  # +100 user / 200 total
        self.assertAlmostEqual(sample.system, 0.0)
        self.assertAlmostEqual(sample.iowait, 0.0)  # iowait did not move

    def test_temp_flows_into_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stat"
            path.write_text(STAT_A)
            collector = CpuCollector(str(path), temp_reader=lambda: 52.0)
            sample = collector.sample()
        self.assertEqual(sample.temp, 52.0)

    def test_freq_flows_into_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stat"
            path.write_text(STAT_A)
            collector = CpuCollector(str(path), freq_reader=lambda: 3200.0)
            sample = collector.sample()
        self.assertEqual(sample.freq, 3200.0)

    def test_read_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stat"
            path.write_text(STAT_A)
            overall, cores = read_cpu_times(str(path))
        self.assertEqual(overall.total, 1000)
        self.assertEqual(len(cores), 2)


class TaskCountTests(unittest.TestCase):
    def test_read_task_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "loadavg"
            path.write_text("0.23 0.40 0.35 2/2028 1060386\n")
            self.assertEqual(read_task_counts(str(path)), (2, 2028))


class DefaultRouteTests(unittest.TestCase):
    HEADER = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\t"
        "Mask\tMTU\tWindow\tIRTT\n"
    )

    def _read(self, body, lookup=lambda name: f"ip-{name}"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "route"
            path.write_text(self.HEADER + body)
            return read_default_ipv4(str(path), address_lookup=lookup)

    def test_uses_default_route_interface(self):
        body = (
            "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t"
            "0\t0\t0\n"
            "eth1\t0000FEA9\t00000000\t0001\t0\t0\t0\t0000FFFF\t"
            "0\t0\t0\n"
        )
        self.assertEqual(self._read(body), "ip-eth0")

    def test_skips_down_interfaces(self):
        body = (
            "eth0\t00000000\t0101A8C0\t0000\t0\t0\t100\t00000000\t"
            "0\t0\t0\n"
        )
        self.assertEqual(self._read(body), "")

    def test_missing_route_file(self):
        self.assertEqual(read_default_ipv4("/nonexistent/route"), "")


class CpuTempTests(unittest.TestCase):
    def _hwmon(self, root: Path, name: str, millis) -> None:
        hwmon = root / "hwmon0"
        hwmon.mkdir()
        (hwmon / "name").write_text(name + "\n")
        for index, milli in enumerate(millis, start=1):
            (hwmon / f"temp{index}_input").write_text(f"{milli}\n")

    def test_reads_hottest_coretemp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._hwmon(root, "coretemp", [52000, 49000, 0])
            self.assertEqual(
                read_cpu_temp(hwmon_root=str(root), thermal_root=str(root)),
                52.0,
            )

    def test_ignores_non_cpu_sensors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._hwmon(root, "nvme", [41000])
            self.assertIsNone(
                read_cpu_temp(hwmon_root=str(root), thermal_root=str(root))
            )

    def test_thermal_zone_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            zone = root / "thermal_zone0"
            zone.mkdir()
            (zone / "type").write_text("x86_pkg_temp\n")
            (zone / "temp").write_text("61000\n")
            self.assertEqual(
                read_cpu_temp(hwmon_root=str(root), thermal_root=str(root)),
                61.0,
            )

    def test_missing_paths(self):
        self.assertIsNone(
            read_cpu_temp(
                hwmon_root="/nonexistent/hwmon",
                thermal_root="/nonexistent/thermal",
            )
        )


class CpuFreqTests(unittest.TestCase):
    def _cpufreq(self, root: Path, index: int, khz: int) -> None:
        cpufreq = root / f"cpu{index}" / "cpufreq"
        cpufreq.mkdir(parents=True)
        (cpufreq / "scaling_cur_freq").write_text(f"{khz}\n")

    def test_reads_highest_scaling_cur_freq(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._cpufreq(root, 0, 2800000)
            self._cpufreq(root, 1, 3200000)
            self.assertEqual(
                read_cpu_freq(
                    cpufreq_root=str(root), cpuinfo_path="/nonexistent"
                ),
                3200.0,
            )

    def test_cpuinfo_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cpuinfo = root / "cpuinfo"
            cpuinfo.write_text(
                "processor\t: 0\n"
                "cpu MHz\t\t: 2399.998\n"
                "processor\t: 1\n"
                "cpu MHz\t\t: 2399.998\n"
            )
            self.assertEqual(
                read_cpu_freq(
                    cpufreq_root=str(root), cpuinfo_path=str(cpuinfo)
                ),
                2399.998,
            )

    def test_missing_sources(self):
        self.assertIsNone(
            read_cpu_freq(
                cpufreq_root="/nonexistent/cpu",
                cpuinfo_path="/nonexistent/cpuinfo",
            )
        )


class MemInfoTests(unittest.TestCase):
    def test_read_meminfo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meminfo"
            path.write_text(MEMINFO)
            mem = read_meminfo(str(path))
        self.assertEqual(mem.total, 16384000 * 1024)
        # used = total - free - cached - sreclaimable - buffers
        self.assertEqual(mem.used, 10184000 * 1024)
        self.assertEqual(mem.shared, 400000 * 1024)
        self.assertEqual(mem.cache, 4600000 * 1024)
        self.assertEqual(mem.used_total, 10584000 * 1024)
        self.assertAlmostEqual(mem.used_pct, 64.6, places=1)
        self.assertEqual(mem.cache_total, 4800000 * 1024)
        self.assertAlmostEqual(mem.swap_pct, 50.0)

    def test_missing_optional_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meminfo"
            path.write_text(
                "MemTotal: 1000 kB\n"
                "MemFree: 200 kB\n"
                "Buffers: 100 kB\n"
                "Cached: 300 kB\n"
            )
            mem = read_meminfo(str(path))
        # used = total - free - cached - sreclaimable - buffers
        self.assertEqual(mem.used, 400 * 1024)
        self.assertEqual(mem.shared, 0)
        self.assertEqual(mem.cache, 300 * 1024)

    def test_no_swap(self):
        mem = MemSample(1000, 500, 0, 0, 0, 0)
        self.assertEqual(mem.swap_pct, 0.0)
        self.assertEqual(mem.swap_used, 0)


if __name__ == "__main__":
    unittest.main()
