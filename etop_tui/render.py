"""Curses drawing helpers and the four metric panels."""

from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from .collect import CpuSample, MemSample
from .gpu import GpuSample

GIB = 1024 ** 3
MIB = 1024 ** 2

def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def load_band(pct: float) -> str:
    """Map a utilisation percentage to one of four colour bands."""
    if pct >= 85:
        return "bad"
    if pct >= 60:
        return "orange"
    if pct >= 40:
        return "warn"
    return "good"


def format_bytes(value: Optional[int]) -> str:
    if value is None:
        return "n/a"
    value = max(0, int(value))
    if value >= GIB:
        return f"{value / GIB:.1f} GiB"
    if value >= MIB:
        return f"{value / MIB:.0f} MiB"
    if value >= 1024:
        return f"{value / 1024:.0f} KiB"
    return f"{value} B"


def compact_bytes(used: Optional[int], total: Optional[int]) -> str:
    """Short byte pair for text-first views, e.g. '9.4/15.6G'."""
    if used is None or not total:
        return ""
    if total >= 100 * GIB:
        # Decimals are noise for large cards; keep the line narrow.
        return f"{used / GIB:.0f}/{total / GIB:.0f}G"
    if total >= GIB:
        total_text = f"{total / GIB:.1f}".rstrip("0").rstrip(".")
        return f"{used / GIB:.1f}/{total_text}G"
    return f"{used / MIB:.0f}/{total / MIB:.0f}M"


def format_uptime(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


class Palette:
    """Colour pairs with a graceful monochrome fallback."""

    @staticmethod
    def _init_default_background() -> bool:
        """Make colour pair 0 use the terminal's default background."""
        try:
            curses.start_color()
        except curses.error:
            return False
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        assume = getattr(curses, "assume_default_colors", None)
        if assume is not None:
            try:
                assume(-1, -1)
            except curses.error:
                pass
        return True

    def __init__(self, enabled: bool = True) -> None:
        self.color = bool(enabled and curses.has_colors())
        self._pairs: Dict[str, int] = {}
        # Even in monochrome mode, make pair 0 use the terminal's default
        # background instead of ncurses' hard-coded black.
        if not self._init_default_background():
            self.color = False
            return
        if not self.color:
            return
        if curses.COLORS >= 256:
            # Desaturated xterm-256 shades: sage / straw / sand / dusty red.
            good, warn, orange, bad = 108, 186, 180, 174
            # Bright sky blue marks the host name without colliding with the
            # green/yellow/orange/red load bands.
            host = 117
        else:
            # 8-colour terminals collapse the middle two bands.
            good, warn, orange, bad = (
                curses.COLOR_GREEN,
                curses.COLOR_YELLOW,
                curses.COLOR_YELLOW,
                curses.COLOR_RED,
            )
            host = curses.COLOR_CYAN
        colours = {
            "normal": curses.COLOR_WHITE,
            "label": curses.COLOR_CYAN,
            "title": curses.COLOR_CYAN,
            "host": host,
            "good": good,
            "warn": warn,
            "orange": orange,
            "bad": bad,
            "accent": curses.COLOR_MAGENTA,
        }
        for index, (name, foreground) in enumerate(colours.items(), start=1):
            try:
                # -1 keeps the terminal's default background (transparent on
                # terminals/themes that support it). Never force black.
                curses.init_pair(index, foreground, -1)
            except curses.error:
                continue
            self._pairs[name] = index
        # If no pair could be initialised, fall back to monochrome attributes.
        self.color = bool(self._pairs)

    def attr(self, name: str = "normal", bold: bool = False) -> int:
        base = 0
        if self.color and name in self._pairs:
            base = curses.color_pair(self._pairs[name])
        elif name in ("label", "title", "accent"):
            base = curses.A_BOLD
        if bold:
            base |= curses.A_BOLD
        # Deliberately no A_DIM: some terminals render faint text with a
        # tinted background, which breaks a transparent terminal background.
        return base

    def level_attr(self, pct: float) -> int:
        band = load_band(pct)
        if not self.color or band not in self._pairs:
            # Monochrome fallback so the four bands remain distinguishable.
            if band == "bad":
                return curses.A_BOLD | curses.A_UNDERLINE
            if band == "orange":
                return curses.A_UNDERLINE
            if band == "warn":
                return curses.A_BOLD
            return curses.A_NORMAL
        return self.attr(band, bold=True)


def put(win, y: int, x: int, text: str, attr: int = 0) -> None:
    """Clip and draw, swallowing curses edge/encoding errors."""
    if not text:
        return
    try:
        height, width = win.getmaxyx()
    except curses.error:
        return
    if y < 0 or y >= height or x >= width:
        return
    if x < 0:
        text = text[-x:]
        x = 0
    text = text[: max(0, width - x)]
    if not text:
        return
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass


@dataclass(frozen=True)
class Rect:
    y: int
    x: int
    h: int
    w: int

    def inner(self) -> "Rect":
        return Rect(self.y + 1, self.x + 1, max(0, self.h - 2), max(0, self.w - 2))


def draw_panel(
    win,
    rect: Rect,
    title: str,
    right: str = "",
    pal: Optional[Palette] = None,
    ascii_mode: bool = False,
    right_attr: Optional[int] = None,
) -> None:
    if rect.h < 2 or rect.w < 4:
        return
    pal = pal or Palette(False)
    if ascii_mode:
        tl = tr = bl = br = "+"
        horizontal, vertical = "-", "|"
    else:
        tl, tr, bl, br = "┌", "┐", "└", "┘"
        horizontal, vertical = "─", "│"

    inner = rect.w - 2
    title_text = f" {title} "
    right_text = f" {right} " if right else ""
    if len(title_text) + len(right_text) > inner:
        right_text = ""
    if len(title_text) > inner:
        title_text = title_text[:inner]
    fill = max(0, inner - len(title_text) - len(right_text))
    top = tl + title_text + horizontal * fill + right_text + tr
    put(win, rect.y, rect.x, top, pal.attr("dim"))
    put(win, rect.y, rect.x + 1, title_text, pal.attr("title", bold=True))
    if right_text:
        put(
            win,
            rect.y,
            rect.x + 1 + len(title_text) + fill,
            right_text,
            right_attr if right_attr is not None else pal.attr("title", bold=True),
        )
    for offset in range(1, rect.h - 1):
        put(win, rect.y + offset, rect.x, vertical, pal.attr("dim"))
        put(win, rect.y + offset, rect.x + rect.w - 1, vertical, pal.attr("dim"))
    put(
        win,
        rect.y + rect.h - 1,
        rect.x,
        bl + horizontal * (rect.w - 2) + br,
        pal.attr("dim"),
    )


def pipe_parts(pct: float, width: int) -> Tuple[str, str]:
    """htop-style utilisation bar made of coloured '|' cells."""
    if width <= 0:
        return "", ""
    pct = _clamp(pct)
    filled_count = int(round(pct / 100.0 * width))
    if 0 < pct and filled_count == 0:
        filled_count = 1
    return "|" * filled_count, " " * (width - filled_count)


def draw_bar(
    win,
    y: int,
    x: int,
    width: int,
    pct: Optional[float],
    pal: Palette,
    ascii_mode: bool = False,
) -> None:
    """Draw a bracketed '|' bar: "[||||      ]"."""
    if width <= 0:
        return
    if width < 2:
        # Too narrow for brackets; keep the bare bar.
        if pct is None:
            put(win, y, x, " " * width, pal.attr("dim"))
            return
        pct = _clamp(pct)
        filled, empty = pipe_parts(pct, width)
        put(win, y, x, filled, pal.level_attr(pct))
        put(win, y, x + len(filled), empty, pal.attr("dim"))
        return

    inner = width - 2
    put(win, y, x, "[", pal.attr("dim"))
    put(win, y, x + width - 1, "]", pal.attr("dim"))
    if pct is None:
        put(win, y, x + 1, " " * inner, pal.attr("dim"))
        return
    pct = _clamp(pct)
    filled, empty = pipe_parts(pct, inner)
    attr = pal.level_attr(pct)
    put(win, y, x + 1, filled, attr)
    put(win, y, x + 1 + len(filled), empty, pal.attr("dim"))


def _temp_text(temp: Optional[float], ascii_mode: bool) -> str:
    if temp is None:
        return ""
    unit = "C" if ascii_mode else "°C"
    return f"{temp:.0f}{unit}"


def _gpu_suffixes(
    gpus: Sequence[GpuSample], ascii_mode: bool, align: bool = True
) -> List[str]:
    """Right-hand text for each GPU: '12.3/24G(51%) 54°C'.

    With several cards the columns are padded so temperatures line up. A
    single card keeps the natural one-space spacing instead.
    """
    mem_texts = [compact_bytes(gpu.mem_used, gpu.mem_total) for gpu in gpus]
    temp_texts = [_temp_text(gpu.temp, ascii_mode) for gpu in gpus]
    pct_texts = [
        "" if gpu.mem_pct is None else f"({gpu.mem_pct:.0f}%)" for gpu in gpus
    ]
    has_vram_pct = any(pct_texts)
    if not align:
        suffixes = []
        for index, gpu in enumerate(gpus):
            parts = []
            if mem_texts[index] or pct_texts[index]:
                parts.append(mem_texts[index] + pct_texts[index])
            if temp_texts[index]:
                parts.append(temp_texts[index])
            suffix = " ".join(parts)
            if gpu.stale:
                suffix = f"{suffix} *".strip()
            suffixes.append(suffix)
        return suffixes

    mem_width = max((len(text) for text in mem_texts), default=0)
    temp_width = max((len(text) for text in temp_texts), default=0)

    suffixes = []
    for index, gpu in enumerate(gpus):
        suffix = ""
        if mem_width:
            suffix += mem_texts[index].rjust(mem_width)
        if has_vram_pct:
            # 6 columns fits "(100%)"; shorter values get padding so 5%, 42%
            # and 81% keep their temperatures in the same column.
            pct_text = pct_texts[index]
            suffix += pct_text.ljust(6)
            if len(pct_text) >= 6:
                suffix += " "
        elif mem_width and temp_width:
            suffix += " "
        if temp_width:
            suffix += temp_texts[index].rjust(temp_width)
        if gpu.stale:
            suffix += " *"
        suffixes.append(suffix.rstrip())
    return suffixes


def draw_header(
    win,
    rect: Rect,
    hostname: str,
    uptime: float,
    loads: Sequence[float],
    tasks: Tuple[int, int],
    gpu_tasks: Optional[int],
    interval: float,
    paused: bool,
    pal: Palette,
) -> None:
    """Two-line header: identity/uptime/clock, then load and task counts."""
    left = f" {hostname}"
    uptime_text = f"up {format_uptime(uptime)}"
    clock = " " + time.strftime("%H:%M:%S") + " "

    put(win, rect.y, 0, left, pal.attr("host", bold=True))
    if rect.w >= len(clock):
        put(win, rect.y, rect.w - len(clock), clock, pal.attr("dim"))
    start = len(left) + 1
    limit = rect.w - len(clock) - 1 if rect.w >= len(clock) else rect.w
    if start < limit:
        put(win, rect.y, start, uptime_text[: limit - start], pal.attr("dim"))

    load_text = (
        f"load {loads[0]:.2f} {loads[1]:.2f} {loads[2]:.2f}"
        f"  cpu {tasks[0]}/{tasks[1]}"
    )
    if gpu_tasks is not None:
        load_text += f"  gpu {gpu_tasks}"
    if paused:
        load_text += "  PAUSED"
    put(win, rect.y + 1, 1, load_text[: max(0, rect.w - 1)], pal.attr("dim"))


def draw_footer(
    win, rect: Rect, interval: float, paused: bool, pal: Palette
) -> None:
    version = f" etop {__version__}"
    if rect.w < 62:
        text = f"{version}  q quit  p pause  +/-  h help"
    else:
        text = (
            f"{version}  q quit   p pause   +/- interval   h help"
            f"     {interval:.2f}s"
        )
    if paused:
        text += "  PAUSED"
    put(win, rect.y, 0, text, pal.attr("dim"))


@dataclass(frozen=True)
class CompactMetric:
    label: str
    pct: Optional[float]
    detail: str = ""


def compact_metrics(
    cpu: CpuSample,
    mem: MemSample,
    gpus: Sequence[GpuSample],
    ascii_mode: bool = False,
) -> List[CompactMetric]:
    """One row of data per metric, for terminals with very few rows."""
    mem_detail = compact_bytes(mem.used, mem.total)
    mem_suffix = (
        f"{mem_detail}({mem.used_pct:.1f}%)"
        if mem_detail
        else f"{mem.used_pct:.1f}%"
    )
    rows = [
        CompactMetric("CPU", cpu.total, f"{cpu.total:5.1f}%"),
        CompactMetric("MEM", mem.used_pct, mem_suffix),
    ]
    if gpus:
        # Every GPU, including a single card, gets one row that combines
        # utilisation, VRAM and temperature: "12.3/24G(51%) 54°C".
        suffixes = _gpu_suffixes(gpus, ascii_mode, align=len(gpus) > 1)
        for index, gpu in enumerate(gpus):
            label = "GPU" if len(gpus) == 1 else f"GPU{gpu.index}"
            rows.append(CompactMetric(label, gpu.util, suffixes[index]))
    return rows


def compact_metrics_for_height(
    rows: int,
    cpu: CpuSample,
    mem: MemSample,
    gpus: Sequence[GpuSample],
    ascii_mode: bool = False,
) -> List[CompactMetric]:
    """Show GPU rows only when the complete set fits below the two header rows."""
    full = compact_metrics(cpu, mem, gpus, ascii_mode)
    if len(full) <= rows - 2:
        return full
    return compact_metrics(cpu, mem, [], ascii_mode)


def compact_layout(
    rows: int, cols: int, count: int
) -> Optional[Tuple[int, Rect, Optional[Rect]]]:
    """Return (first_metric_row, header, footer).

    The footer is dropped when that is the only way to keep every metric row.
    """
    if rows < 5 or cols < 44 or count <= 0:
        return None
    header = Rect(0, 0, 2, cols)
    if count <= rows - 3:
        return 2, header, Rect(rows - 1, 0, 1, cols)
    if count <= rows - 2:
        return 2, header, None
    return None


def draw_compact(
    win,
    rows: int,
    cols: int,
    cpu: CpuSample,
    mem: MemSample,
    gpus: Sequence[GpuSample],
    pal: Palette,
    ascii_mode: bool,
    hostname: str,
    uptime: float,
    loads: Sequence[float],
    tasks: Tuple[int, int],
    gpu_tasks: Optional[int],
    interval: float,
    paused: bool,
) -> bool:
    """Draw the metrics without boxes, one row per metric."""
    metrics = compact_metrics_for_height(
        rows, cpu, mem, gpus, ascii_mode
    )
    layout = compact_layout(rows, cols, len(metrics))
    if layout is None:
        return False
    start, header, footer = layout
    draw_header(
        win,
        header,
        hostname,
        uptime,
        loads,
        tasks,
        gpu_tasks,
        interval,
        paused,
        pal,
    )

    label_width = max(5, max(len(metric.label) for metric in metrics) + 1)
    meter_x = 1 + label_width
    right_edge = cols - 1

    # All bars share the same width; every suffix is right-aligned so the
    # row endings line up regardless of how much text it carries.
    max_suffix = max((len(metric.detail) for metric in metrics), default=0)
    bar_end = right_edge - max_suffix - 1 if max_suffix else right_edge
    bar_width = max(1, bar_end - meter_x)

    for offset, metric in enumerate(metrics):
        y = start + offset
        put(win, y, 1, f"{metric.label:<{label_width}}", pal.attr("dim"))
        draw_bar(
            win,
            y,
            meter_x,
            bar_width,
            metric.pct,
            pal,
            ascii_mode,
        )
        if metric.detail:
            put(
                win,
                y,
                right_edge - len(metric.detail),
                metric.detail,
                pal.attr("dim"),
            )

    if footer is not None:
        draw_footer(win, footer, interval, paused, pal)
    return True


def draw_help(win, interval: float, pal: Palette, ascii_mode: bool) -> None:
    height, width = win.getmaxyx()
    box_width = min(58, max(30, width - 6))
    box_height = 10
    y = max(0, (height - box_height) // 2)
    x = max(0, (width - box_width) // 2)
    blank = " " * box_width
    for offset in range(box_height):
        put(win, y + offset, x, blank, pal.attr("normal"))
    draw_panel(
        win,
        Rect(y, x, box_height, box_width),
        "HELP",
        "any key",
        pal,
        ascii_mode,
    )
    lines = [
        " q, Esc      quit",
        " p, Space    pause / resume",
        " + / -       faster / slower refresh",
        " h, ?        close this help",
        "",
        f" refresh interval: {interval:.2f}s",
    ]
    for offset, line in enumerate(lines[: box_height - 2]):
        put(win, y + 1 + offset, x + 2, line, pal.attr("normal"))


def draw_too_small(
    win, rows: int, cols: int, pal: Palette, min_rows: int = 5, min_cols: int = 44
) -> None:
    put(win, 0, 1, "etop", pal.attr("title", bold=True))
    message = f"{cols}x{rows} too small (min {min_cols}x{min_rows})"
    put(win, 1, 1, message, pal.attr("normal"))
