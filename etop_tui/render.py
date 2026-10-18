"""Curses drawing helpers and the four metric panels."""

from __future__ import annotations

import curses
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import __version__
from .collect import CpuSample, MemSample
from .gpu import GpuSample

GIB = 1024 ** 3
MIB = 1024 ** 2

_PARTIAL = " ▏▎▍▌▋▊▉█"
# Braille dot bits indexed by [column_in_cell][row_in_cell].
_DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


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

    def __init__(self, enabled: bool = True) -> None:
        self.color = bool(enabled and curses.has_colors())
        self._pairs: Dict[str, int] = {}
        if not self.color:
            return
        try:
            curses.start_color()
        except curses.error:
            self.color = False
            return
        backgrounds = []
        try:
            curses.use_default_colors()
            backgrounds.append(-1)
        except curses.error:
            pass
        backgrounds.append(curses.COLOR_BLACK)
        if curses.COLORS >= 256:
            # Muted xterm-256 shades; pure 46/226/196 are harsh on the eyes.
            good, warn, orange, bad = 71, 179, 173, 167
        else:
            # 8-colour terminals collapse the middle two bands.
            good, warn, orange, bad = (
                curses.COLOR_GREEN,
                curses.COLOR_YELLOW,
                curses.COLOR_YELLOW,
                curses.COLOR_RED,
            )
        colours = {
            "normal": curses.COLOR_WHITE,
            "label": curses.COLOR_CYAN,
            "title": curses.COLOR_CYAN,
            "good": good,
            "warn": warn,
            "orange": orange,
            "bad": bad,
            "accent": curses.COLOR_MAGENTA,
        }
        for index, (name, foreground) in enumerate(colours.items(), start=1):
            for background in backgrounds:
                try:
                    curses.init_pair(index, foreground, background)
                except curses.error:
                    continue
                self._pairs[name] = index
                break
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
        if name == "dim":
            base |= curses.A_DIM
        return base

    def level_attr(self, pct: float) -> int:
        band = load_band(pct)
        if not self.color or band not in self._pairs:
            # Monochrome fallback so the four bands remain distinguishable.
            if band == "bad":
                return curses.A_BOLD | curses.A_REVERSE
            if band == "orange":
                return curses.A_BOLD | curses.A_UNDERLINE
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


@dataclass
class Layout:
    header: Rect
    panels: Dict[str, Rect]
    footer: Rect


def compute_layout(
    rows: int, cols: int, gpu_count: int, has_vram: bool
) -> Optional[Layout]:
    """Divide the screen between the panels, or None if it is too small."""
    if rows < 10 or cols < 44:
        return None

    multi = gpu_count > 1
    gpu_height = gpu_count + 2 if multi else 4
    heights = {"cpu": 4, "mem": 5}
    if gpu_count:
        heights["gpu"] = gpu_height
    if has_vram and not multi:
        heights["vram"] = 4

    available = rows - 2  # header + footer
    minimum = {"cpu": 3, "mem": 4}
    if gpu_count:
        minimum["gpu"] = gpu_height if multi else 4
    if "vram" in heights:
        minimum["vram"] = 3
    while sum(heights.values()) > available:
        shrinkable = [key for key in heights if heights[key] > minimum[key]]
        if not shrinkable:
            return None
        key = max(shrinkable, key=lambda name: heights[name])
        heights[key] -= 1

    caps = {
        "cpu": 2 + 20 + 1,
        "mem": 2 + 20 + 3,
        "gpu": gpu_height if multi else 2 + 14 + 2,
        "vram": 2 + 12 + 2,
    }
    while sum(heights.values()) < available:
        grew = False
        for key in ("cpu", "mem", "gpu", "vram"):
            if key in heights and heights[key] < caps[key] and sum(heights.values()) < available:
                heights[key] += 1
                grew = True
        if not grew:
            break

    panels: Dict[str, Rect] = {}
    y = 1
    for key in ("cpu", "mem", "gpu", "vram"):
        if key in heights:
            panels[key] = Rect(y, 0, heights[key], cols)
            y += heights[key]
    return Layout(Rect(0, 0, 1, cols), panels, Rect(rows - 1, 0, 1, cols))


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


def meter_parts(pct: float, width: int, ascii_mode: bool = False) -> Tuple[str, str]:
    """Return (filled, empty) strings for a horizontal meter."""
    if width <= 0:
        return "", ""
    pct = _clamp(pct)
    if ascii_mode:
        filled_count = int(round(pct / 100.0 * width))
        return "#" * filled_count, " " * (width - filled_count)
    units = int(round(pct / 100.0 * width * 8))
    full, remainder = divmod(units, 8)
    full = min(full, width)
    if full >= width:
        return "█" * width, ""
    filled = "█" * full
    if remainder:
        filled += _PARTIAL[remainder]
    return filled, " " * (width - len(filled))


def draw_bar(
    win,
    y: int,
    x: int,
    width: int,
    pct: Optional[float],
    pal: Palette,
    ascii_mode: bool = False,
) -> None:
    """Draw a bar without the trailing percentage text."""
    if width <= 0:
        return
    if pct is None:
        put(win, y, x, " " * width, pal.attr("dim"))
        return
    pct = _clamp(pct)
    filled, empty = meter_parts(pct, width, ascii_mode)
    attr = pal.level_attr(pct)
    put(win, y, x, filled, attr)
    put(win, y, x + len(filled), empty, pal.attr("dim"))


def draw_meter(
    win,
    y: int,
    x: int,
    width: int,
    pct: Optional[float],
    pal: Palette,
    ascii_mode: bool = False,
    suffix: Optional[str] = None,
) -> None:
    if width <= 0:
        return
    if suffix is None:
        suffix = " n/a" if pct is None else f" {pct:5.1f}%"
    bar_width = max(1, width - len(suffix))
    if pct is None:
        put(win, y, x, " " * bar_width, pal.attr("dim"))
        put(win, y, x + bar_width, suffix, pal.attr("dim"))
        return
    pct = _clamp(pct)
    filled, empty = meter_parts(pct, bar_width, ascii_mode)
    attr = pal.level_attr(pct)
    put(win, y, x, filled, attr)
    put(win, y, x + len(filled), empty, pal.attr("dim"))
    put(win, y, x + bar_width, suffix, attr)


def graph_matrix(
    values: Iterable[float],
    width: int,
    height: int,
    vmax: float = 100.0,
    ascii_mode: bool = False,
) -> List[List[Tuple[str, Optional[float]]]]:
    """Render a history graph as rows of (character, column value) cells.

    The value attached to each cell is the average utilisation of that column,
    which lets the caller colour every segment according to its own load band.
    """
    if width <= 0 or height <= 0:
        return []
    data = list(values)[-width * 2 :]
    if not data:
        padded: List[Optional[float]] = [None] * (width * 2)
    elif len(data) < width * 2:
        # Stretch the short history across the whole graph instead of leaving
        # most of it blank; the time scale settles once the buffer is full.
        total = width * 2
        padded = [
            data[min(len(data) - 1, index * len(data) // total)]
            for index in range(total)
        ]
    else:
        padded = data

    columns: List[Optional[float]] = []
    for column in range(width):
        pair = [
            value
            for value in padded[column * 2 : column * 2 + 2]
            if value is not None
        ]
        columns.append(sum(pair) / len(pair) if pair else None)

    dot_height = height * 4
    matrix: List[List[Tuple[str, Optional[float]]]] = []
    for row in range(height):
        cells: List[Tuple[str, Optional[float]]] = []
        for column, column_value in enumerate(columns):
            if column_value is None:
                cells.append((" ", None))
                continue
            if ascii_mode:
                ratio = max(0.0, min(1.0, column_value / vmax))
                # Keep a one-row baseline so 0% still draws a continuous line.
                filled_rows = max(1, int(round(ratio * height)))
                character = "#" if row >= height - filled_rows else " "
            else:
                bits = 0
                for sub_column in (0, 1):
                    value = padded[column * 2 + sub_column]
                    if value is None:
                        continue
                    ratio = max(0.0, min(1.0, value / vmax))
                    level = max(1, int(round(ratio * dot_height)))
                    for sub_row in range(4):
                        from_bottom = (height - row - 1) * 4 + (3 - sub_row)
                        if from_bottom < level:
                            bits |= _DOTS[sub_column][sub_row]
                character = chr(0x2800 + bits) if bits else " "
            cells.append((character, column_value if character != " " else None))
        matrix.append(cells)
    return matrix


def graph_lines(
    values: Iterable[float],
    width: int,
    height: int,
    vmax: float = 100.0,
    ascii_mode: bool = False,
) -> List[str]:
    """Render a filled history graph, newest sample on the right."""
    return [
        "".join(character for character, _ in row)
        for row in graph_matrix(values, width, height, vmax, ascii_mode)
    ]


def draw_graph(
    win,
    rect: Rect,
    values: Iterable[float],
    pal: Palette,
    ascii_mode: bool = False,
    vmax: float = 100.0,
) -> None:
    if rect.h <= 0 or rect.w <= 0:
        return
    for offset, row in enumerate(
        graph_matrix(values, rect.w, rect.h, vmax, ascii_mode)
    ):
        for column, (character, value) in enumerate(row):
            if character == " " or value is None:
                continue
            put(
                win,
                rect.y + offset,
                rect.x + column,
                character,
                pal.level_attr(value),
            )


def draw_cpu(
    win,
    rect: Rect,
    sample: CpuSample,
    history: Sequence[float],
    pal: Palette,
    ascii_mode: bool,
) -> None:
    draw_panel(
        win,
        rect,
        "CPU",
        f"{sample.total:5.1f}%",
        pal,
        ascii_mode,
        right_attr=pal.level_attr(sample.total),
    )
    inner = rect.inner()
    if inner.h <= 0 or inner.w <= 0:
        return
    info_rows = 1 if inner.h >= 1 else 0
    graph_h = max(0, inner.h - info_rows)
    if graph_h:
        draw_graph(
            win,
            Rect(inner.y, inner.x, graph_h, inner.w),
            history,
            pal,
            ascii_mode,
        )
    if info_rows:
        draw_meter(
            win,
            inner.y + graph_h,
            inner.x,
            inner.w,
            sample.total,
            pal,
            ascii_mode,
        )


def draw_mem(
    win,
    rect: Rect,
    mem: MemSample,
    history: Sequence[float],
    pal: Palette,
    ascii_mode: bool,
) -> None:
    draw_panel(
        win,
        rect,
        "MEMORY",
        f"{mem.used_pct:5.1f}%",
        pal,
        ascii_mode,
        right_attr=pal.level_attr(mem.used_pct),
    )
    inner = rect.inner()
    if inner.h <= 0 or inner.w <= 0:
        return
    if inner.h >= 5:
        info_rows = 3
    elif inner.h >= 3:
        info_rows = 2
    elif inner.h >= 1:
        info_rows = 1
    else:
        info_rows = 0
    graph_h = max(0, inner.h - info_rows)
    if graph_h:
        draw_graph(
            win,
            Rect(inner.y, inner.x, graph_h, inner.w),
            history,
            pal,
            ascii_mode,
        )
    y = inner.y + graph_h
    if info_rows >= 1:
        draw_meter(win, y, inner.x, inner.w, mem.used_pct, pal, ascii_mode)
    if info_rows >= 2:
        detail = (
            f"Used {format_bytes(mem.used)} / {format_bytes(mem.total)}"
            f"   Cache {format_bytes(mem.cache_total)}"
            f"   Avail {format_bytes(mem.available)}"
        )
        put(win, y + 1, inner.x, detail, pal.attr("dim"))
    if info_rows >= 3:
        if mem.swap_total:
            swap = (
                f"Swap {format_bytes(mem.swap_used)} / "
                f"{format_bytes(mem.swap_total)} ({mem.swap_pct:.1f}%)"
            )
        else:
            swap = "Swap disabled"
        put(win, y + 2, inner.x, swap, pal.attr("dim"))


def _temp_text(temp: Optional[float], ascii_mode: bool) -> str:
    if temp is None:
        return ""
    unit = "C" if ascii_mode else "°C"
    return f"{temp:.0f}{unit}"


def _gpu_suffixes(
    gpus: Sequence[GpuSample], ascii_mode: bool
) -> List[str]:
    """Right-hand text for each GPU: '12.3/24G(51%) 54°C'."""
    mem_texts = [compact_bytes(gpu.mem_used, gpu.mem_total) for gpu in gpus]
    mem_width = max((len(text) for text in mem_texts), default=0)
    temp_texts = [_temp_text(gpu.temp, ascii_mode) for gpu in gpus]
    temp_width = max((len(text) for text in temp_texts), default=0)
    pct_texts = [
        "" if gpu.mem_pct is None else f"({gpu.mem_pct:.0f}%)" for gpu in gpus
    ]
    has_vram_pct = any(pct_texts)

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


def draw_gpu(
    win,
    rect: Rect,
    gpus: Sequence[GpuSample],
    histories: Mapping[str, Sequence[float]],
    pal: Palette,
    ascii_mode: bool,
) -> None:
    if not gpus:
        return
    primary = gpus[0]
    util = primary.util
    right = f"{util:5.1f}%" if util is not None else "  n/a"
    draw_panel(
        win,
        rect,
        "GPU",
        right,
        pal,
        ascii_mode,
        right_attr=pal.level_attr(util if util is not None else 0.0),
    )
    inner = rect.inner()
    if inner.h <= 0 or inner.w <= 0:
        return

    if len(gpus) > 1:
        for row, gpu in enumerate(gpus[: inner.h]):
            value = gpu.util
            suffix = f" {value:5.1f}%" if value is not None else "   n/a"
            draw_meter(
                win,
                inner.y + row,
                inner.x + 2,
                inner.w - 2,
                value,
                pal,
                ascii_mode,
                suffix=suffix,
            )
            put(win, inner.y + row, inner.x, f"{gpu.index}", pal.attr("dim"))
        return

    info_rows = 1 if inner.h >= 1 else 0
    graph_h = max(0, inner.h - info_rows)
    if graph_h:
        draw_graph(
            win,
            Rect(inner.y, inner.x, graph_h, inner.w),
            histories.get(f"gpu:{primary.index}", ()),
            pal,
            ascii_mode,
        )
    y = inner.y + graph_h
    if info_rows >= 1:
        suffix = ""
        temperature = _temp_text(primary.temp, ascii_mode)
        if temperature:
            suffix = "  " + temperature
        if primary.stale:
            suffix += "  [stale]"
        draw_meter(win, y, inner.x, inner.w, util, pal, ascii_mode, suffix=suffix)


def draw_vram(
    win,
    rect: Rect,
    gpus: Sequence[GpuSample],
    histories: Mapping[str, Sequence[float]],
    pal: Palette,
    ascii_mode: bool,
) -> None:
    with_memory = [gpu for gpu in gpus if gpu.mem_total]
    if not with_memory:
        return
    primary = with_memory[0]
    pct = primary.mem_pct
    right = f"{pct:5.1f}%" if pct is not None else "  n/a"
    draw_panel(
        win,
        rect,
        "VRAM",
        right,
        pal,
        ascii_mode,
        right_attr=pal.level_attr(pct if pct is not None else 0.0),
    )
    inner = rect.inner()
    if inner.h <= 0 or inner.w <= 0:
        return

    if len(with_memory) > 1:
        for row, gpu in enumerate(with_memory[: inner.h]):
            value = gpu.mem_pct
            used = format_bytes(gpu.mem_used)
            total = format_bytes(gpu.mem_total)
            suffix = f" {value:5.1f}%" if value is not None else "   n/a"
            suffix += f"  {used}/{total}"
            draw_meter(
                win,
                inner.y + row,
                inner.x + 2,
                inner.w - 2,
                value,
                pal,
                ascii_mode,
                suffix=suffix,
            )
            put(win, inner.y + row, inner.x, f"{gpu.index}", pal.attr("dim"))
        return

    info_rows = 2 if inner.h >= 4 else 1 if inner.h >= 1 else 0
    graph_h = max(0, inner.h - info_rows)
    if graph_h:
        draw_graph(
            win,
            Rect(inner.y, inner.x, graph_h, inner.w),
            histories.get(f"vram:{primary.index}", ()),
            pal,
            ascii_mode,
        )
    y = inner.y + graph_h
    if info_rows >= 1:
        draw_meter(win, y, inner.x, inner.w, pct, pal, ascii_mode)
    if info_rows >= 2:
        detail = (
            f"{format_bytes(primary.mem_used)} / "
            f"{format_bytes(primary.mem_total)} used"
        )
        put(win, y + 1, inner.x, detail, pal.attr("dim"))


def draw_gpu_list(
    win,
    rect: Rect,
    gpus: Sequence[GpuSample],
    pal: Palette,
    ascii_mode: bool,
) -> None:
    """Multi-GPU view: a utilisation bar plus right-aligned text per card."""
    if not gpus:
        return
    has_vram = any(gpu.mem_total for gpu in gpus)
    title = "GPU / VRAM" if has_vram else "GPU"
    draw_panel(
        win,
        rect,
        title,
        f"{len(gpus)} GPUs",
        pal,
        ascii_mode,
    )
    inner = rect.inner()
    if inner.h <= 0 or inner.w <= 0:
        return

    label_width = max(len(f"GPU{gpu.index}") for gpu in gpus) + 1
    suffixes = _gpu_suffixes(gpus, ascii_mode)
    suffix_width = max((len(text) for text in suffixes), default=0)
    suffix_end = inner.x + inner.w - 1
    suffix_start = suffix_end - suffix_width if suffix_width else inner.x + inner.w
    bar_x = inner.x + label_width
    bar_width = max(1, suffix_start - 1 - bar_x)

    visible = min(len(gpus), inner.h)
    truncated = len(gpus) > visible
    if truncated and visible > 1:
        visible -= 1

    for row, gpu in enumerate(gpus[:visible]):
        y = inner.y + row
        put(win, y, inner.x, f"GPU{gpu.index}", pal.attr("dim"))
        draw_bar(win, y, bar_x, bar_width, gpu.util, pal, ascii_mode)
        if suffixes[row]:
            put(
                win,
                y,
                suffix_end - len(suffixes[row]),
                suffixes[row],
                pal.attr("dim"),
            )

    if truncated:
        hidden = len(gpus) - visible
        if hidden > 0 and inner.y + visible < inner.y + inner.h:
            put(
                win,
                inner.y + visible,
                inner.x,
                f"+{hidden} more GPUs",
                pal.attr("dim"),
            )


def draw_header(
    win,
    rect: Rect,
    hostname: str,
    uptime: float,
    loads: Sequence[float],
    interval: float,
    paused: bool,
    pal: Palette,
) -> None:
    left = f" etop {__version__}  {hostname}"
    right = " " + time.strftime("%H:%M:%S") + " "
    parts = [
        f"up {format_uptime(uptime)}",
        f"load {loads[0]:.2f} {loads[1]:.2f} {loads[2]:.2f}",
    ]
    if paused:
        parts.append("PAUSED")
    space = rect.w - len(left) - len(right) - 1
    middle_parts = []
    used = 0
    for part in parts:
        addition = len(part) + (2 if middle_parts else 0)
        if used + addition > space:
            break
        middle_parts.append(part)
        used += addition
    put(win, rect.y, 0, left, pal.attr("title", bold=True))
    if middle_parts:
        put(
            win,
            rect.y,
            len(left) + 1,
            "  ".join(middle_parts),
            pal.attr("dim"),
        )
    if rect.w >= len(right):
        put(win, rect.y, rect.w - len(right), right, pal.attr("dim"))


def draw_footer(
    win, rect: Rect, interval: float, paused: bool, pal: Palette
) -> None:
    if rect.w < 62:
        text = " q quit  p pause  +/-  r reset  h help"
    else:
        text = (
            f" q quit   p pause   +/- interval   r reset   h help"
            f"     {interval:.2f}s"
        )
    if paused:
        text += "   PAUSED"
    put(win, rect.y, 1, text, pal.attr("dim"))


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
    if len(gpus) > 1:
        suffixes = _gpu_suffixes(gpus, ascii_mode)
        for index, gpu in enumerate(gpus):
            rows.append(
                CompactMetric(f"GPU{gpu.index}", gpu.util, suffixes[index])
            )
        return rows
    if gpus:
        primary = gpus[0]
        detail = _temp_text(primary.temp, ascii_mode)
        if primary.stale:
            detail = f"{detail} *".strip()
        rows.append(CompactMetric("GPU", primary.util, detail))
    if gpus and gpus[0].mem_total:
        primary = gpus[0]
        detail = compact_bytes(primary.mem_used, primary.mem_total)
        if primary.mem_pct is not None and detail:
            detail = f"{detail}({primary.mem_pct:.1f}%)"
        rows.append(CompactMetric("VRAM", primary.mem_pct, detail))
    return rows


def compact_layout(
    rows: int, cols: int, count: int
) -> Optional[Tuple[int, Rect, Rect]]:
    """Return (first_metric_row, header, footer) for the compact view."""
    if rows < 6 or cols < 40 or count <= 0 or count > rows - 2:
        return None
    return 1, Rect(0, 0, 1, cols), Rect(rows - 1, 0, 1, cols)


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
    interval: float,
    paused: bool,
) -> bool:
    """Draw the four metrics without boxes, one row per metric."""
    metrics = compact_metrics(cpu, mem, gpus, ascii_mode)
    layout = compact_layout(rows, cols, len(metrics))
    if layout is None:
        return False
    start, header, footer = layout
    draw_header(win, header, hostname, uptime, loads, interval, paused, pal)

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
        draw_bar(win, y, meter_x, bar_width, metric.pct, pal, ascii_mode)
        if metric.detail:
            put(
                win,
                y,
                right_edge - len(metric.detail),
                metric.detail,
                pal.attr("dim"),
            )

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
        " r           reset history graphs",
        " h, ?        close this help",
        "",
        f" refresh interval: {interval:.2f}s",
    ]
    for offset, line in enumerate(lines[: box_height - 2]):
        put(win, y + 1 + offset, x + 2, line, pal.attr("normal"))


def draw_too_small(
    win, rows: int, cols: int, pal: Palette, min_rows: int = 6, min_cols: int = 40
) -> None:
    put(win, 0, 1, "etop", pal.attr("title", bold=True))
    message = f"{cols}x{rows} too small (min {min_cols}x{min_rows})"
    put(win, 1, 1, message, pal.attr("normal"))
