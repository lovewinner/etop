import math
import unittest

from etop_tui.render import (
    Rect,
    compute_layout,
    format_bytes,
    format_uptime,
    graph_lines,
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
        empty = graph_lines([0.0] * 20, 10, 2)
        self.assertTrue(all(ch == " " for line in empty for ch in line))
        self.assertTrue(all(0x2800 <= ord(ch) <= 0x28FF for ch in "".join(full) if ch != " "))


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

    def test_too_small(self):
        self.assertIsNone(compute_layout(9, 80, 0, False))
        self.assertIsNone(compute_layout(24, 40, 0, False))
        self.assertIsNone(compute_layout(12, 50, 1, True))

    def test_rect_inner(self):
        inner = Rect(1, 2, 5, 10).inner()
        self.assertEqual((inner.y, inner.x, inner.h, inner.w), (2, 3, 3, 8))


if __name__ == "__main__":
    unittest.main()
