import unittest

from etop_tui.collect import CpuSample, MemSample
from etop_tui.gpu import GpuSample
from etop_tui.render import (
    GIB,
    Palette,
    Rect,
    _freq_text,
    compact_bytes,
    draw_bar,
    compact_layout,
    compact_metrics,
    compact_metrics_for_height,
    format_bytes,
    format_uptime,
    load_band,
    pipe_parts,
)


class MeterTests(unittest.TestCase):
    def test_pipe_sizes(self):
        for pct in (0, 0.4, 1, 50, 99.9, 100):
            filled, empty = pipe_parts(pct, 20)
            self.assertEqual(len(filled) + len(empty), 20)

    def test_pipe_bounds(self):
        self.assertEqual(pipe_parts(-5, 10), ("", " " * 10))
        self.assertEqual(pipe_parts(150, 10), ("|" * 10, ""))

    def test_pipe_parts(self):
        filled, empty = pipe_parts(50, 10)
        self.assertEqual(filled, "|||||")
        self.assertEqual(len(filled) + len(empty), 10)
        self.assertEqual(pipe_parts(0, 10), ("", " " * 10))
        self.assertEqual(pipe_parts(100, 10), ("|" * 10, ""))
        self.assertEqual(pipe_parts(1, 100), ("|", " " * 99))
        self.assertEqual(pipe_parts(0.1, 100), ("|", " " * 99))


class _Window:
    """Minimal curses window stub that records drawn characters."""

    def __init__(self, width):
        self.width = width
        self.cells = [" "] * width

    def getmaxyx(self):
        return 1, self.width

    def addstr(self, y, x, text, attr=0):
        for offset, char in enumerate(text):
            position = x + offset
            if 0 <= position < self.width:
                self.cells[position] = char

    def text(self):
        return "".join(self.cells).rstrip()


class DrawBarTests(unittest.TestCase):
    def test_bar_is_wrapped_in_brackets(self):
        win = _Window(20)
        draw_bar(win, 0, 0, 20, 50.0, Palette(False))
        self.assertEqual(win.text(), "[|||||||||         ]")

    def test_unknown_value_still_has_brackets(self):
        win = _Window(10)
        draw_bar(win, 0, 0, 10, None, Palette(False))
        self.assertEqual(win.text(), "[        ]")

    def test_segments_get_distinct_monochrome_glyphs(self):
        win = _Window(20)
        draw_bar(
            win,
            0,
            0,
            20,
            50.0,
            Palette(False),
            segments=((25.0, "cpu_user"), (25.0, "cpu_system")),
        )
        self.assertEqual(win.text(), "[" + "|" * 5 + "#" * 5 + " " * 8 + "]")


class ColourBandTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(load_band(0), "good")
        self.assertEqual(load_band(39.9), "good")
        self.assertEqual(load_band(40), "warn")
        self.assertEqual(load_band(59.9), "warn")
        self.assertEqual(load_band(60), "orange")
        self.assertEqual(load_band(84.9), "orange")
        self.assertEqual(load_band(85), "bad")
        self.assertEqual(load_band(100), "bad")

    def test_all_four_bands_are_reachable(self):
        self.assertEqual(
            {load_band(0), load_band(50), load_band(70), load_band(90)},
            {"good", "warn", "orange", "bad"},
        )


class FormatTests(unittest.TestCase):
    def test_bytes(self):
        self.assertEqual(format_bytes(None), "n/a")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(2048), "2 KiB")
        self.assertEqual(format_bytes(3 * 1024 ** 2), "3 MiB")
        self.assertEqual(format_bytes(3 * 1024 ** 3), "3.0 GiB")

    def test_uptime(self):
        self.assertEqual(format_uptime(59), "0m")
        self.assertEqual(format_uptime(3600), "1h 00m")
        self.assertEqual(format_uptime(90000), "1d 1h")


class RectTests(unittest.TestCase):
    def test_rect_inner(self):
        inner = Rect(1, 2, 5, 10).inner()
        self.assertEqual((inner.y, inner.x, inner.h, inner.w), (2, 3, 3, 8))


class CompactLayoutTests(unittest.TestCase):
    def test_44x8_fits_four_metrics(self):
        layout = compact_layout(8, 44, 4)
        self.assertIsNotNone(layout)
        start, header, footer = layout
        self.assertEqual((start, header.h, footer.y), (2, 2, 7))
        self.assertLessEqual(start + 4 - 1, footer.y - 1)

    def test_44x9_fits_four_gpus(self):
        layout = compact_layout(9, 44, 6)
        self.assertIsNotNone(layout)
        start, header, footer = layout
        self.assertEqual((start, header.h, footer.y), (2, 2, 8))
        self.assertLessEqual(start + 6 - 1, footer.y - 1)
        self.assertIsNone(compact_layout(9, 44, 7)[2])
        self.assertIsNone(compact_layout(9, 44, 8))

    def test_44x8_four_gpus_drops_footer(self):
        layout = compact_layout(8, 44, 6)
        self.assertIsNotNone(layout)
        start, header, footer = layout
        self.assertEqual((start, header.h, footer), (2, 2, None))
        self.assertEqual(start + 6 - 1, 7)

    def test_minimum_and_top_alignment(self):
        self.assertEqual(compact_layout(5, 44, 2)[0], 2)
        self.assertEqual(compact_layout(7, 44, 4)[0], 2)
        self.assertEqual(compact_layout(8, 44, 2)[0], 2)
        self.assertIsNone(compact_layout(4, 44, 2))
        self.assertIsNone(compact_layout(5, 43, 2))
        self.assertIsNotNone(compact_layout(6, 44, 4))

    def test_44x6_shows_all_metrics_without_footer(self):
        self.assertIsNotNone(compact_layout(6, 44, 4))
        self.assertIsNone(compact_layout(6, 44, 4)[2])

    def test_44x5_fits_a_single_gpu_row(self):
        cpu = CpuSample(10.0, ())
        mem = MemSample(8 * GIB, 1, 4 * GIB, 0, 0, 0, 0)
        gpu = GpuSample("nvidia", 0, "RTX", 10.0, 2 * GIB, 24 * GIB, 54.0)
        for rows in (5, 6, 7):
            self.assertEqual(
                [
                    metric.label
                    for metric in compact_metrics_for_height(
                        rows, cpu, mem, [gpu]
                    )
                ],
                ["CPU", "MEM", "GPU"],
            )

    def test_rows_do_not_overlap_footer(self):
        for rows in range(5, 20):
            for count in (2, 3, 4, 6):
                layout = compact_layout(rows, 44, count)
                if layout is None:
                    continue
                start, _, footer = layout
                if footer is None:
                    self.assertEqual(start + count - 1, rows - 1)
                else:
                    self.assertLessEqual(start + count - 1, footer.y - 1)

    def test_metrics(self):
        cpu = CpuSample(42.0, (10.0, 20.0))
        mem = MemSample(
            total=16 * GIB,
            free=4 * GIB,
            buffers=1 * GIB,
            cached=2 * GIB,
            swap_total=0,
            swap_free=0,
            sreclaimable=1 * GIB,
            shmem=0,
        )
        gpu = GpuSample("nvidia", 0, "RTX", 21.0, 2 * GIB, 24 * GIB, 54.0)
        metrics = compact_metrics(cpu, mem, [gpu], ascii_mode=False)
        self.assertEqual(
            [metric.label for metric in metrics], ["CPU", "MEM", "GPU"]
        )
        self.assertAlmostEqual(metrics[0].pct, 42.0)
        self.assertEqual(metrics[0].detail, "2(42.0%)")
        self.assertEqual(metrics[1].detail, "8.0/16G(50.0%)")
        self.assertAlmostEqual(metrics[2].pct, 21.0)
        self.assertEqual(metrics[2].detail, "2.0/24G(8%) 54°C")

    def test_cpu_detail_includes_temperature(self):
        cpu = CpuSample(42.0, (10.0, 20.0), temp=52.0)
        mem = MemSample(8 * GIB, 1, 0, 0, 0, 0)
        metrics = compact_metrics(cpu, mem, [])
        self.assertEqual(metrics[0].detail, "2(42.0%) 52°C")

    def test_cpu_detail_without_temperature(self):
        cpu = CpuSample(42.0, (10.0, 20.0))
        mem = MemSample(8 * GIB, 1, 0, 0, 0, 0)
        metrics = compact_metrics(cpu, mem, [])
        self.assertEqual(metrics[0].detail, "2(42.0%)")

    def test_cpu_detail_includes_frequency(self):
        cpu = CpuSample(42.0, (10.0, 20.0), temp=52.0, freq=3200.0)
        mem = MemSample(8 * GIB, 1, 0, 0, 0, 0)
        metrics = compact_metrics(cpu, mem, [])
        self.assertEqual(metrics[0].detail, "2(42.0%) 3.2GHz 52°C")

    def test_cpu_detail_frequency_without_temperature(self):
        cpu = CpuSample(42.0, (10.0, 20.0), freq=3200.0)
        mem = MemSample(8 * GIB, 1, 0, 0, 0, 0)
        metrics = compact_metrics(cpu, mem, [])
        self.assertEqual(metrics[0].detail, "2(42.0%) 3.2GHz")

    def test_freq_text_formatting(self):
        self.assertEqual(_freq_text(3200.0), "3.2GHz")
        self.assertEqual(_freq_text(4000.0), "4GHz")
        self.assertEqual(_freq_text(4500.0), "4.5GHz")
        self.assertEqual(_freq_text(800.0), "800MHz")
        self.assertEqual(_freq_text(None), "")

    def test_cpu_and_memory_segments(self):
        cpu = CpuSample(
            65.0,
            (),
            user=30.0,
            nice=5.0,
            system=20.0,
            irq=3.0,
            steal=2.0,
            iowait=5.0,
        )
        mem = MemSample(
            total=100 * GIB,
            free=10 * GIB,
            buffers=10 * GIB,
            cached=20 * GIB,
            swap_total=0,
            swap_free=0,
            sreclaimable=5 * GIB,
            shmem=5 * GIB,
        )
        metrics = compact_metrics(cpu, mem, [])
        self.assertEqual(
            metrics[0].segments,
            (
                (30.0, "cpu_user"),
                (5.0, "cpu_nice"),
                (20.0, "cpu_system"),
                (3.0, "cpu_irq"),
                (2.0, "cpu_steal"),
                (5.0, "cpu_iowait"),
            ),
        )
        self.assertEqual(
            metrics[1].segments,
            (
                (55.0, "mem_used"),
                (5.0, "mem_shared"),
                (10.0, "mem_buffers"),
                (20.0, "mem_cache"),
            ),
        )
        self.assertAlmostEqual(metrics[1].pct, 60.0)
        self.assertEqual(metrics[1].detail, "60/100G(60.0%)")

    def test_multi_gpu_metrics_have_bars_and_aligned_text(self):
        cpu = CpuSample(10.0, ())
        mem = MemSample(8 * GIB, 1, 4 * GIB, 0, 0, 0, 0)
        gpus = [
            GpuSample("nvidia", 0, "RTX 4090", 42.0, 2 * GIB, 24 * GIB, 54.0),
            GpuSample("nvidia", 1, "RTX 4090", 87.0, 19 * GIB, 24 * GIB, 61.0),
        ]
        metrics = compact_metrics(cpu, mem, gpus, ascii_mode=False)
        self.assertEqual(
            [metric.label for metric in metrics],
            ["CPU", "MEM", "GPU0", "GPU1"],
        )
        self.assertIn("2.0/24G(8%)", metrics[2].detail)
        self.assertIn("54°C", metrics[2].detail)
        self.assertNotIn("RTX 4090", metrics[2].detail)
        self.assertNotIn("42.0%", metrics[2].detail)
        self.assertNotIn("87.0%", metrics[3].detail)
        self.assertEqual(len(metrics[2].detail), len(metrics[3].detail))

    def test_metrics_without_gpu(self):
        cpu = CpuSample(10.0, ())
        mem = MemSample(8 * GIB, 1, 4 * GIB, 0, 0, 0, 0)
        self.assertEqual(
            [metric.label for metric in compact_metrics(cpu, mem, [])],
            ["CPU", "MEM"],
        )

    def test_gpu_without_vram(self):
        cpu = CpuSample(10.0, ())
        mem = MemSample(8 * GIB, 1, 4 * GIB, 0, 0, 0, 0)
        gpu = GpuSample("nvidia", 0, "RTX", 10.0, None, None, None)
        self.assertEqual(
            [metric.label for metric in compact_metrics(cpu, mem, [gpu])],
            ["CPU", "MEM", "GPU"],
        )

    def test_compact_bytes(self):
        self.assertEqual(compact_bytes(None, 2 * GIB), "")
        self.assertEqual(compact_bytes(512 * 1024 ** 2, 2 * GIB), "0.5/2G")
        self.assertEqual(compact_bytes(256 * 1024 ** 2, 512 * 1024 ** 2), "256/512M")
        self.assertEqual(
            compact_bytes(int(76.6 * GIB), int(503.5 * GIB)), "77/504G"
        )


if __name__ == "__main__":
    unittest.main()
