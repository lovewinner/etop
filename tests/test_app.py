import contextlib
import io
import unittest

from etop_tui.app import main


class _TtyStringIO(io.StringIO):
    def isatty(self):
        return True


class CommandLineTests(unittest.TestCase):
    def _run(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_once_demo(self):
        code, output = self._run(["--once", "--demo", "--ascii"])
        self.assertEqual(code, 0)
        for label in ("CPU", "MEM", "GPU 0", "VRAM 0"):
            self.assertIn(label, output)

    def test_once_colours_on_a_tty(self):
        buffer = _TtyStringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--once", "--demo", "--ascii"])
        self.assertEqual(code, 0)
        output = buffer.getvalue()
        self.assertIn("\033[38;5;108m", output)  # low CPU load is sage green
        self.assertIn("\033[0m", output)

    def test_once_without_gpu(self):
        code, output = self._run(["--once", "--gpu", "none", "--ascii"])
        self.assertEqual(code, 0)
        self.assertIn("CPU", output)
        self.assertIn("MEM", output)
        self.assertNotIn("GPU", output)
        self.assertNotIn("VRAM", output)


if __name__ == "__main__":
    unittest.main()
