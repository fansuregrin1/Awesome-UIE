"""Lightweight, dependency-free progress reporting.

Writes to **stderr only** so machine-readable output on stdout (e.g.
``validate --format json``) stays clean. On a TTY it refreshes one line in place;
otherwise it prints a line every ~10% so CI logs stay readable.
"""

from __future__ import annotations

import shutil
import sys
import time
from typing import Optional


class Progress:
    def __init__(
        self,
        total: int,
        desc: str,
        stream=None,
        enabled: bool = True,
        verbose: bool = False,
    ) -> None:
        self.total = max(int(total), 0)
        self.desc = desc
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled
        self.verbose = verbose
        self.count = 0
        self.start = time.time()
        self._last_percent = -1
        try:
            self._isatty = bool(self.stream.isatty())
        except Exception:
            self._isatty = False

    def _percent(self) -> int:
        return int(self.count / self.total * 100) if self.total else 100

    def update(self, detail: str = "") -> None:
        self.count += 1
        if not self.enabled:
            return
        if self.verbose:
            print(f"[{self.count}/{self.total}] {detail}", file=self.stream, flush=True)
            return
        percent = self._percent()
        if self._isatty:
            prefix = f"{self.desc} [{self.count}/{self.total}] {percent:3d}%  "
            width = shutil.get_terminal_size((100, 20)).columns
            available = max(1, width - len(prefix) - 1)
            text = detail if len(detail) <= available else detail[: available - 1] + "…"
            self.stream.write("\r" + prefix + text + "\x1b[K")
            self.stream.flush()
        elif percent >= self._last_percent + 10 or self.count == self.total:
            self._last_percent = percent
            print(f"{self.desc} [{self.count}/{self.total}] {percent}%", file=self.stream, flush=True)

    def close(self) -> None:
        if not self.enabled:
            return
        if self._isatty and not self.verbose:
            self.stream.write("\n")
            self.stream.flush()
        print(f"{self.desc}: done in {time.time() - self.start:.1f}s", file=self.stream, flush=True)

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def null_progress() -> Progress:
    """A disabled progress (no output) for library defaults."""
    return Progress(0, "", enabled=False)


def track(progress: Optional[Progress], total: int, desc: str, enabled: bool = True, verbose: bool = False) -> Progress:
    return progress if progress is not None else Progress(total, desc, enabled=enabled, verbose=verbose)
