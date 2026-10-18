"""etop command line entry point and curses event loop."""

from __future__ import annotations

import argparse
import curses
import locale
import os
import socket
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Sequence

from . import __version__
from .collect import CpuCollector, CpuSample, MemSample, read_meminfo, read_uptime
from .gpu import DemoBackend, GpuMonitor, GpuSample, detect_backends
from .render import (
    Palette,
    compute_layout,
    draw_cpu,
    draw_footer,
    draw_gpu,
    draw_header,
    draw_help,
    draw_mem,
    draw_too_small,
    draw_vram,
    format_bytes,
    format_uptime,
    meter_parts,
)


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
        self.history: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=4096)
        )
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

        self.history["cpu"].append(self.cpu_sample.total)
        self.history["mem"].append(self.mem_sample.used_pct)
        for gpu in self.gpus:
            if gpu.util is not None:
                self.history[f"gpu:{gpu.index}"].append(gpu.util)
            if gpu.mem_pct is not None:
                self.history[f"vram:{gpu.index}"].append(gpu.mem_pct)

    def _draw(self) -> None:
        if self.cpu_sample is None or self.mem_sample is None:
            return
        try:
            rows, cols = self.screen.getmaxyx()
            self.screen.erase()
            has_vram = any(gpu.mem_total for gpu in self.gpus)
            layout = compute_layout(rows, cols, len(self.gpus), has_vram)
            if layout is None and self.gpus:
                # Not enough room for four panels: keep CPU and memory.
                layout = compute_layout(rows, cols, 0, False)
            if layout is None:
                minimum = 2 + 3 + 4 + (4 if self.gpus else 0) + (3 if has_vram else 0)
                draw_too_small(self.screen, rows, cols, self.pal, minimum)
            else:
                draw_header(
                    self.screen,
                    layout.header,
                    self.hostname,
                    self.uptime,
                    self.loads,
                    self.options.delay,
                    self.paused,
                    self.pal,
                )
                draw_cpu(
                    self.screen,
                    layout.panels["cpu"],
                    self.cpu_sample,
                    self.history["cpu"],
                    self.pal,
                    self.options.ascii,
                )
                draw_mem(
                    self.screen,
                    layout.panels["mem"],
                    self.mem_sample,
                    self.history["mem"],
                    self.pal,
                    self.options.ascii,
                )
                if "gpu" in layout.panels:
                    draw_gpu(
                        self.screen,
                        layout.panels["gpu"],
                        self.gpus,
                        self.history,
                        self.pal,
                        self.options.ascii,
                    )
                if "vram" in layout.panels:
                    draw_vram(
                        self.screen,
                        layout.panels["vram"],
                        self.gpus,
                        self.history,
                        self.pal,
                        self.options.ascii,
                    )
                draw_footer(
                    self.screen,
                    layout.footer,
                    self.options.delay,
                    self.paused,
                    self.pal,
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
            elif key in (ord("r"), ord("R")):
                self.history.clear()
                self._draw()
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

    def row(label: str, pct: Optional[float], extra: str = "") -> None:
        if pct is None:
            filled, empty = " " * width, ""
            percent = "  n/a"
        else:
            filled, empty = meter_parts(pct, width, ascii_mode)
            percent = f"{pct:5.1f}%"
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
        details = [gpu.name]
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
