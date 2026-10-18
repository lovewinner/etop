"""etop command line entry point and curses event loop."""

from __future__ import annotations

import argparse
import curses
import locale
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import __version__
from .collect import CpuCollector, CpuSample, MemSample, read_meminfo, read_uptime
from .gpu import DemoBackend, GpuMonitor, GpuSample, detect_backends
from .render import (
    Palette,
    compact_metrics_for_height,
    draw_compact,
    draw_help,
    draw_too_small,
    format_bytes,
    format_uptime,
    load_band,
    pipe_parts,
)

_ANSI_COLORS = {
    "good": "\033[38;5;108m",
    "warn": "\033[38;5;186m",
    "orange": "\033[38;5;180m",
    "bad": "\033[38;5;174m",
}
_ANSI_RESET = "\033[0m"


@dataclass
class Options:
    delay: float = 1.0
    once: bool = False
    gpu: str = "auto"
    demo: bool = False
    color: bool = True
    ascii: bool = False


def _setup_locale() -> None:
    """Prefer a UTF-8 locale so braille and block characters render."""
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    encoding = (locale.getpreferredencoding(False) or "").lower()
    if "utf" in encoding:
        return
    for candidate in ("C.UTF-8", "C.utf8", "en_US.UTF-8"):
        try:
            locale.setlocale(locale.LC_ALL, candidate)
            return
        except locale.Error:
            continue


def _stdout_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in encoding


class App:
    def __init__(self, screen, options: Options) -> None:
        self.screen = screen
        self.options = options
        self.pal = Palette(options.color)
        self.cpu = CpuCollector()
        backends = [DemoBackend()] if options.demo else detect_backends(options.gpu)
        self.gpu_monitor = GpuMonitor(backends)
        self.hostname = socket.gethostname()
        self.cpu_sample: Optional[CpuSample] = None
        self.mem_sample: Optional[MemSample] = None
        self.gpus: List[GpuSample] = []
        self.loads = (0.0, 0.0, 0.0)
        self.uptime = 0.0
        self.paused = False
        self.next_tick = 0.0
        self._update()

    def _update(self) -> None:
        self.cpu_sample = self.cpu.sample()
        self.mem_sample = read_meminfo()
        self.gpus = self.gpu_monitor.sample()
        try:
            self.loads = os.getloadavg()
        except OSError:
            self.loads = (0.0, 0.0, 0.0)
        try:
            self.uptime = read_uptime()
        except OSError:
            pass

    def _draw(self) -> None:
        if self.cpu_sample is None or self.mem_sample is None:
            return
        try:
            rows, cols = self.screen.getmaxyx()
            self.screen.erase()
            if not draw_compact(
                self.screen,
                rows,
                cols,
                self.cpu_sample,
                self.mem_sample,
                self.gpus,
                self.pal,
                self.options.ascii,
                self.hostname,
                self.uptime,
                self.loads,
                self.options.delay,
                self.paused,
            ):
                needed = len(
                    compact_metrics_for_height(
                        rows,
                        self.cpu_sample,
                        self.mem_sample,
                        self.gpus,
                        self.options.ascii,
                    )
                )
                draw_too_small(
                    self.screen,
                    rows,
                    cols,
                    self.pal,
                    min_rows=max(4, needed + 2),
                )
            self.screen.refresh()
        except curses.error:
            pass

    def run(self) -> None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.screen.nodelay(True)
        self.screen.keypad(True)
        self.next_tick = 0.0
        while True:
            now = time.monotonic()
            if now >= self.next_tick:
                if not self.paused:
                    self._update()
                self._draw()
                self.next_tick = now + self.options.delay

            remaining = max(0.0, self.next_tick - time.monotonic())
            self.screen.timeout(int(max(20, min(250, remaining * 1000))))
            key = self.screen.getch()
            if key == -1:
                continue
            if key in (ord("q"), ord("Q"), 27):
                return
            if key in (ord("p"), ord("P"), ord(" ")):
                self.paused = not self.paused
                if not self.paused:
                    self._update()
                self._draw()
                self.next_tick = time.monotonic() + self.options.delay
            elif key in (ord("+"), ord("=")):
                self.options.delay = max(
                    0.25, round(self.options.delay - 0.25, 2)
                )
                self._draw()
                self.next_tick = time.monotonic() + self.options.delay
            elif key in (ord("-"), ord("_")):
                self.options.delay = min(
                    10.0, round(self.options.delay + 0.25, 2)
                )
                self._draw()
                self.next_tick = time.monotonic() + self.options.delay
            elif key in (ord("h"), ord("H"), ord("?")):
                draw_help(
                    self.screen, self.options.delay, self.pal, self.options.ascii
                )
                self.screen.refresh()
                self.screen.timeout(-1)
                self.screen.getch()
                self.screen.nodelay(True)
                self._draw()
            elif key == curses.KEY_RESIZE:
                self._draw()


def print_once(options: Options) -> None:
    """Print a single plain-text snapshot (handy for scripts and ssh)."""
    cpu = CpuCollector()
    cpu.sample()
    time.sleep(0.3)
    sample = cpu.sample()
    mem = read_meminfo()
    backends = [DemoBackend()] if options.demo else detect_backends(options.gpu)
    gpus = GpuMonitor(backends).sample()

    ascii_mode = options.ascii or not _stdout_unicode()
    try:
        loads = os.getloadavg()
    except OSError:
        loads = (0.0, 0.0, 0.0)
    try:
        uptime = read_uptime()
    except OSError:
        uptime = 0.0

    print(
        f"etop {__version__}  {socket.gethostname()}  up {format_uptime(uptime)}"
        f"  load {loads[0]:.2f} {loads[1]:.2f} {loads[2]:.2f}"
    )
    print()

    width = 28
    use_ansi = (
        options.color
        and not os.environ.get("NO_COLOR")
        and hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
    )

    def row(
        label: str,
        pct: Optional[float],
        extra: str = "",
    ) -> None:
        if pct is None:
            filled, empty = " " * width, ""
            percent = "  n/a"
        else:
            filled, empty = pipe_parts(pct, width)
            percent = f"{pct:5.1f}%"
            if use_ansi:
                colour = _ANSI_COLORS[load_band(pct)]
                filled = colour + filled + _ANSI_RESET
                percent = colour + percent + _ANSI_RESET
        line = f"{label:<6}{percent}  [{filled}{empty}]"
        if extra:
            line += "  " + extra
        print(line.rstrip())

    row("CPU", sample.total)
    row(
        "MEM",
        mem.used_pct,
        f"{format_bytes(mem.used)} / {format_bytes(mem.total)}",
    )
    for gpu in gpus:
        details = []
        if gpu.temp is not None:
            details.append(f"{gpu.temp:.0f}{'C' if ascii_mode else '°C'}")
        if gpu.stale:
            details.append("stale")
        row(f"GPU {gpu.index}", gpu.util, "  ".join(details))
        if gpu.mem_total:
            row(
                f"VRAM {gpu.index}",
                gpu.mem_pct,
                f"{format_bytes(gpu.mem_used)} / {format_bytes(gpu.mem_total)}",
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etop",
        description=(
            "A small htop-like terminal monitor for CPU, memory, GPU and VRAM."
        ),
    )
    parser.add_argument(
        "-d",
        "--delay",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="refresh interval, 0.25-10 (default: 1.0)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="print one snapshot in plain text and exit",
    )
    parser.add_argument(
        "--gpu",
        choices=("auto", "nvidia", "sysfs", "none"),
        default="auto",
        help="GPU backend (default: auto)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="use a synthetic GPU so the GPU panels can be previewed",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="disable colours"
    )
    parser.add_argument(
        "--ascii", action="store_true", help="use ASCII instead of Unicode"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    options = Options(
        delay=max(0.25, min(10.0, args.delay)),
        once=args.once,
        gpu=args.gpu,
        demo=args.demo,
        color=not args.no_color,
        ascii=args.ascii,
    )
    _setup_locale()
    if not _stdout_unicode():
        options.ascii = True
    if options.once:
        print_once(options)
        return 0
    try:
        curses.wrapper(lambda screen: App(screen, options).run())
    except KeyboardInterrupt:
        pass
    return 0
