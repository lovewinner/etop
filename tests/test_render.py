import math
import unittest

from etop_tui.collect import CpuSample, MemSample
from etop_tui.gpu import GpuSample
from etop_tui.render import (
    GIB,
    Rect,
    compact_bytes,
    compact_layout,
    compact_metrics,
    compute_layout,
    format_bytes,
    format_uptime,
    graph_lines,
    graph_matrix,
    load_band,
    meter_parts,
)


class MeterTests(unittest.TestCase):
    def test_sizes(self):
        for pct in (0, 0.4, 1, 50, 99.9, 100):
            filled, empty = meter_parts(pct, 20)
            self.assertEqual(len(filled) + len(empty), 20)
            ascii_filled, ascii_empty = meter_parts(pct, 20, ascii_mode=True)
            self.assertEqual(len(ascii_filled) + len(ascii_empty), 20)

    def test_bounds(self):
        self.assertEqual(meter_parts(-5, 10), ("", " " * 10))
        self.assertEqual(meter_parts(150, 10), ("█" * 10, ""))


class ColourBandTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(load_band(0), "good")
        self.assertEqual(load_band(59.9), "good")
        self.assertEqual(load_band(60), "warn")
        self.assertEqual(load_band(84.9), "warn")
        self.assertEqual(load_band(85), "bad")
        self.assertEqual(load_band(100), "bad")


class GraphTests(unittest.TestCase):
    def test_dimensions(self):
        values = [50.0] * 100
        lines = graph_lines(values, 30, 4)
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(len(line) == 30 for line in lines))
        ascii_lines = graph_lines(values, 30, 4, ascii_mode=True)
        self.assertEqual(len(ascii_lines), 4)
        self.assertTrue(all(len(line) == 30 for line in ascii_lines))

    def test_full_and_empty(self):
        full = graph_lines([100.0] * 20, 10, 2)
        self.assertTrue(all(ch != " " for line in full for ch in line[:10]))
        zero = graph_lines([0.0] * 20, 10, 2)
        self.assertTrue(all(ch != " " for ch in zero[-1]))
        self.assertTrue(all(ch == " " for row in zero[:-1] for ch in row))
        self.assertTrue(all(0x2800 <= ord(ch) <= 0x28FF for ch in "".join(full) if ch != " "))

    def test_matrix_keeps_per_column_values(self):
        matrix = graph_matrix([10.0, 10.0, 70.0, 70.0, 95.0, 95.0], 3, 2)
        self.assertEqual(len(matrix), 2)
        self.assertEqual(len(matrix[0]), 3)
        values = {
            value
            for row in matrix
            for character, value in row
            if value is not None and character != " "
        }
        self.assertEqual(values, {10.0, 70.0, 95.0})

    def test_short_history_stretches_to_full_width(self):
        matrix = graph_matrix([50.0], 5, 2)
        values = {
            value
            for row in matrix
            for character, value in row
            if value is not None and character != " "
        }
        self.assertEqual(values, {50.0})
        self.assertEqual(sum(1 for character, _ in matrix[1] if character != " "), 5)

    def test_history_order_is_preserved(self):
        matrix = graph_matrix([10.0, 90.0], 4, 2)
        columns = [value for _, value in matrix[1]]
        self.assertEqual(columns, [10.0, 10.0, 90.0, 90.0])


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


class LayoutTests(unittest.TestCase):
    def _check(self, layout, rows, cols):
        rects = [layout.header] + list(layout.panels.values()) + [layout.footer]
        for rect in rects:
            self.assertGreaterEqual(rect.y, 0)
            self.assertGreaterEqual(rect.x, 0)
            self.assertLessEqual(rect.y + rect.h, rows)
            self.assertLessEqual(rect.x + rect.w, cols)
        filled = [set(range(rect.y, rect.y + rect.h)) for rect in rects]
        for i in range(len(filled)):
            for j in range(i + 1, len(filled)):
                self.assertFalse(filled[i] & filled[j])

    def test_fits_common_sizes(self):
        for rows, cols in ((24, 80), (30, 120), (40, 110), (16, 60)):
            layout = compute_layout(rows, cols, 1, True)
            if layout is None:
                continue
            self._check(layout, rows, cols)
            self.assertIn("cpu", layout.panels)
            self.assertIn("mem", layout.panels)
            self.assertIn("gpu", layout.panels)
            self.assertIn("vram", layout.panels)

    def test_no_gpu(self):
        layout = compute_layout(24, 80, 0, False)
        self.assertIsNotNone(layout)
        self._check(layout, 24, 80)
        self.assertNotIn("gpu", layout.panels)
        self.assertNotIn("vram", layout.panels)

    def test_multi_gpu_text_layout(self):
        layout = compute_layout(24, 80, 4, True)
        self.assertIsNotNone(layout)
        self._check(layout, 24, 80)
        self.assertIn("gpu", layout.panels)
        self.assertNotIn("vram", layout.panels)
        self.assertEqual(layout.panels["gpu"].h, 6)

        layout = compute_layout(16, 44, 4, True)
        self.assertIsNotNone(layout)
        self._check(layout, 16, 44)
        self.assertEqual(layout.panels["gpu"].h, 6)

        layout = compute_layout(24, 80, 12, True)
        self.assertIsNotNone(layout)
        self._check(layout, 24, 80)
        self.assertEqual(layout.panels["gpu"].h, 14)

        self.assertIsNone(compute_layout(24, 80, 16, True))

    def test_too_small(self):
        self.assertIsNone(compute_layout(9, 80, 0, False))
        self.assertIsNone(compute_layout(24, 40, 0, False))
        self.assertIsNone(compute_layout(12, 50, 1, True))

    def test_rect_inner(self):
        inner = Rect(1, 2, 5, 10).inner()
        self.assertEqual((inner.y, inner.x, inner.h, inner.w), (2, 3, 3, 8))


class CompactLayoutTests(unittest.TestCase):
    def test_44x8_fits_four_metrics(self):
        layout = compact_layout(8, 44, 4)
        self.assertIsNotNone(layout)
        start, header, footer = layout
        self.assertEqual((start, header.y, footer.y), (2, 0, 7))
        self.assertLessEqual(start + 4 - 1, footer.y - 1)

    def test_44x8_fits_four_gpus(self):
        layout = compact_layout(8, 44, 6)
        self.assertIsNotNone(layout)
        start, header, footer = layout
        self.assertEqual((start, header.y, footer.y), (1, 0, 7))
        self.assertLessEqual(start + 6 - 1, footer.y - 1)
        self.assertIsNone(compact_layout(8, 44, 7))

    def test_minimum_and_centering(self):
        self.assertEqual(compact_layout(6, 44, 4)[0], 1)
        self.assertEqual(compact_layout(8, 44, 2)[0], 3)
        self.assertIsNone(compact_layout(5, 44, 4))
        self.assertIsNone(compact_layout(8, 39, 4))

    def test_rows_do_not_overlap_footer(self):
        for rows in range(6, 20):
            for count in (2, 3, 4):
                layout = compact_layout(rows, 44, count)
                if layout is None:
                    continue
                start, _, footer = layout
                self.assertLessEqual(start + count - 1, footer.y - 1)

    def test_metrics(self):
        cpu = CpuSample(42.0, (10.0, 20.0))
        mem = MemSample(
            total=16 * GIB,
            free=1,
            available=8 * GIB,
            buffers=0,
            cached=0,
            swap_total=0,
            swap_free=0,
        )
        gpu = GpuSample("nvidia", 0, "RTX", 21.0, 2 * GIB, 24 * GIB, 54.0)
        metrics = compact_metrics(cpu, mem, [gpu], ascii_mode=False)
        self.assertEqual(
            [metric.label for metric in metrics], ["CPU", "MEM", "GPU", "VRAM"]
        )
        self.assertAlmostEqual(metrics[0].pct, 42.0)
        self.assertEqual(metrics[0].detail, " 42.0%")
        self.assertEqual(metrics[1].detail, " 50.0% 8.0/16G")
        self.assertEqual(metrics[2].detail, "54°C")
        self.assertAlmostEqual(metrics[3].pct, 2 / 24 * 100)
        self.assertEqual(metrics[3].detail, "  8.3% 2.0/24G")

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
        self.assertIn("2.0/24G", metrics[2].detail)
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
