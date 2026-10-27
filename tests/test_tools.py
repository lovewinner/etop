"""Tests for the screenshot helpers in tools/ (not part of the package)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "render_ansi", ROOT / "tools" / "render_ansi.py"
)
render_ansi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render_ansi)


class ParseLineTests(unittest.TestCase):
    def test_colour_codes_reach_the_cells(self):
        cells = render_ansi.parse_line("\x1b[38;5;108m|||\x1b[0m rest")
        self.assertEqual([cell[0] for cell in cells], list("||| rest"))
        self.assertEqual([cell[1] for cell in cells[:3]], [108, 108, 108])
        self.assertIsNone(cells[4][1])

    def test_bold_and_reset(self):
        cells = render_ansi.parse_line("\x1b[1m\x1b[38;5;117moc\x1b[0m")
        self.assertTrue(cells[0][2])
        self.assertEqual(cells[0][1], 117)
        self.assertEqual(cells[0][0], "o")

    def test_basic_and_bright_palette(self):
        cells = render_ansi.parse_line("\x1b[32ma\x1b[91mb")
        self.assertEqual(cells[0][1], 2)
        self.assertEqual(cells[1][1], 9)

    def test_truecolor(self):
        cells = render_ansi.parse_line("\x1b[38;2;10;20;30mx")
        self.assertEqual(cells[0][1], (10 << 16) | (20 << 8) | 30)

    def test_non_sgr_escapes_are_dropped(self):
        cells = render_ansi.parse_line("a\x1b[Kb\x1b(Bc")
        self.assertEqual([cell[0] for cell in cells], ["a", "b", "c"])

    def test_xterm_palette_extremes(self):
        self.assertEqual(render_ansi.xterm_color(0), (0, 0, 0))
        self.assertEqual(render_ansi.xterm_color(15), (255, 255, 255))
        self.assertEqual(render_ansi.xterm_color(196), (255, 0, 0))
        self.assertEqual(render_ansi.xterm_color(232), (8, 8, 8))


class RenderTests(unittest.TestCase):
    def setUp(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")

    def test_render_writes_a_png(self):
        capture = (
            "\x1b[1m\x1b[38;5;117mhost\x1b[0m 12:00\n"
            "CPU  [\x1b[38;5;108m||||\x1b[39m    ] 12%\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "shot.png"
            size = render_ansi.render(
                capture, str(output), "title", 16, True, (14, 14, 16), 1
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 500)
            self.assertGreater(size[0], 0)
            self.assertGreater(size[1], 0)

    def test_empty_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit):
                render_ansi.render(
                    "   \n\n", str(Path(directory) / "x.png"), "",
                    16, False, (0, 0, 0), 1,
                )


if __name__ == "__main__":
    unittest.main()
