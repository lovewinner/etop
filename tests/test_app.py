import contextlib
import io
import unittest

from etop_tui.app import main


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

    def test_once_without_gpu(self):
        code, output = self._run(["--once", "--gpu", "none", "--ascii"])
        self.assertEqual(code, 0)
        self.assertIn("CPU", output)
        self.assertIn("MEM", output)
        self.assertNotIn("GPU", output)
        self.assertNotIn("VRAM", output)


if __name__ == "__main__":
    unittest.main()
