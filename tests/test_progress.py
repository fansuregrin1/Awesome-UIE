"""Tests for the dependency-free progress reporter."""

import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".dev_scripts"))

from uie.progress import Progress  # noqa: E402


class ProgressTest(unittest.TestCase):
    def test_disabled_is_silent(self):
        buffer = io.StringIO()
        progress = Progress(5, "x", stream=buffer, enabled=False)
        for index in range(5):
            progress.update(str(index))
        progress.close()
        self.assertEqual(buffer.getvalue(), "")

    def test_non_tty_prints_milestones(self):
        buffer = io.StringIO()  # StringIO.isatty() is False
        progress = Progress(100, "work", stream=buffer)
        for index in range(100):
            progress.update(f"item{index}")
        progress.close()
        output = buffer.getvalue()
        self.assertIn("work [100/100] 100%", output)
        self.assertIn("done in", output)
        self.assertLess(output.count("\n"), 20)  # ~10% milestones, not one line per item

    def test_verbose_prints_each_item(self):
        buffer = io.StringIO()
        progress = Progress(3, "work", stream=buffer, verbose=True)
        progress.update("a")
        progress.update("b")
        progress.update("c")
        progress.close()
        output = buffer.getvalue()
        self.assertIn("[1/3] a", output)
        self.assertIn("[3/3] c", output)


if __name__ == "__main__":
    unittest.main()
