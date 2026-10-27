#!/usr/bin/env python3
"""Render a terminal ANSI capture into a PNG for the README.

The capture is produced by ``tmux capture-pane -e`` (or any command that
emits SGR colour sequences). etop never writes a background colour, so the
image uses one fixed dark terminal background.

Usage:
    python3 tools/render_ansi.py shot.ansi -o docs/screenshot.png -t dev24G
"""
from __future__ import annotations

import argparse
import re
import sys

DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
DEFAULT_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

PAD_X = 20
PAD_Y = 14
TITLE_H = 32
FOREGROUND = (213, 213, 213)
BORDER = (52, 52, 58)
MUTED = (138, 138, 145)
TRAFFIC = [(255, 95, 86), (255, 189, 46), (39, 201, 63)]

BASE_COLORS = [
    0x000000, 0x800000, 0x008000, 0x808000, 0x000080, 0x800080, 0x008080,
    0xC0C0C0, 0x808080, 0xFF0000, 0x00FF00, 0xFFFF00, 0x0000FF, 0xFF00FF,
    0x00FFFF, 0xFFFFFF,
]

# CSI sequences ending in anything but 'm' are not colour codes (cursor
# moves, erases) and are dropped; 'm' is SGR and must be kept.
NON_SGR = re.compile(r"\x1b\[[0-9;?]*[A-La-ln-z]|\x1b[()][A-Z0-9]|\x1b[^\[]")
SGR = re.compile(r"\x1b\[([0-9;]*)m")


def xterm_color(index: int):
    """Map an xterm-256 colour index to an (r, g, b) tuple."""
    if index < 16:
        value = BASE_COLORS[index]
        return ((value >> 16) & 255, (value >> 8) & 255, value & 255)
    if index < 232:
        index -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return (steps[index // 36], steps[(index // 6) % 6], steps[index % 6])
    gray = 8 + (index - 232) * 10
    return (gray, gray, gray)


def parse_line(raw: str):
    """Split one captured line into (char, colour, bold) cells.

    ``colour`` is an xterm-256 index, a packed 0xRRGGBB int, or None.
    """
    cells = []
    fg = None
    bold = False
    pos = 0
    for match in SGR.finditer(raw):
        for char in NON_SGR.sub("", raw[pos:match.start()]):
            cells.append((char, fg, bold))
        codes = (match.group(1) or "0").split(";")
        i = 0
        while i < len(codes):
            code = codes[i] or "0"
            if code == "0":
                fg, bold = None, False
            elif code == "1":
                bold = True
            elif code == "22":
                bold = False
            elif code == "39":
                fg = None
            elif code == "38" and i + 1 < len(codes):
                if codes[i + 1] == "5" and i + 2 < len(codes):
                    fg = int(codes[i + 2])
                    i += 2
                elif codes[i + 1] == "2" and i + 4 < len(codes):
                    fg = (
                        int(codes[i + 2]) << 16
                        | int(codes[i + 3]) << 8
                        | int(codes[i + 4])
                    )
                    i += 4
            elif code.isdigit():
                value = int(code)
                if 30 <= value <= 37:
                    fg = value - 30
                elif 90 <= value <= 97:
                    fg = value - 90 + 8
            i += 1
        pos = match.end()
    for char in NON_SGR.sub("", raw[pos:]):
        cells.append((char, fg, bold))
    return cells


def _sgr_colour(value):
    if value is None:
        return FOREGROUND
    if value < 256:
        return xterm_color(value)
    return ((value >> 16) & 255, (value >> 8) & 255, value & 255)


def _parse_bg(text: str):
    text = text.lstrip("#")
    if len(text) != 6:
        raise argparse.ArgumentTypeError("expected a 6-digit hex colour")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def render(capture, output, title, font_size, chrome, background, scale):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(DEFAULT_FONT, font_size)
    font_bold = ImageFont.truetype(DEFAULT_FONT_BOLD, font_size)
    ascent, descent = font.getmetrics()
    cell_w = font.getlength("M")
    cell_h = ascent + descent + 3

    lines = [parse_line(line) for line in capture.split("\n")]
    while lines and not any(cell[0] != " " for cell in lines[-1]):
        lines.pop()
    for line in lines:
        while line and line[-1][0] == " ":
            line.pop()
    if not lines:
        sys.exit("error: capture is empty, nothing to render")

    cols = max(len(line) for line in lines)
    width = int(cols * cell_w) + PAD_X * 2
    top = TITLE_H if chrome else 0
    height = top + PAD_Y * 2 + int(len(lines) * cell_h)

    image = Image.new("RGB", (width * scale, height * scale), background)
    draw = ImageDraw.Draw(image)

    if chrome:
        draw.rectangle(
            [0, 0, width * scale - 1, height * scale - 1],
            outline=BORDER,
            width=scale,
        )
        draw.line(
            [1, TITLE_H * scale, width * scale - 2, TITLE_H * scale],
            fill=BORDER,
            width=scale,
        )
        for index, colour in enumerate(TRAFFIC):
            cx = (PAD_X + 9 + index * 19) * scale
            cy = (TITLE_H // 2) * scale
            radius = 5 * scale
            draw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius],
                fill=colour,
            )
        if title:
            draw.text(
                ((PAD_X + 9 + 3 * 19 + 10) * scale, (TITLE_H // 2) * scale),
                title,
                font=font,
                fill=MUTED,
                anchor="lm",
            )

    y = top + PAD_Y
    for line in lines:
        x = PAD_X
        for char, colour, bold in line:
            if char != " ":
                draw.text(
                    (x * scale, y * scale),
                    char,
                    font=font_bold if bold else font,
                    fill=_sgr_colour(colour),
                )
            x += cell_w
        y += cell_h

    image.save(output)
    return image.size


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="ANSI capture file, or - for stdin")
    parser.add_argument("-o", "--output", default="docs/screenshot.png")
    parser.add_argument(
        "-t",
        "--title",
        default="",
        help="text drawn in the window title bar",
    )
    parser.add_argument("--font-size", type=int, default=16)
    parser.add_argument(
        "--scale", type=int, default=1, help="pixel multiplier for HiDPI output"
    )
    parser.add_argument(
        "--no-chrome",
        action="store_true",
        help="omit the window frame and title bar",
    )
    parser.add_argument(
        "--bg", type=_parse_bg, default=(14, 14, 16),
        help="background colour as RRGGBB",
    )
    args = parser.parse_args(argv)

    if args.capture == "-":
        capture = sys.stdin.read()
    else:
        with open(args.capture, encoding="utf-8") as handle:
            capture = handle.read()
    width, height = render(
        capture,
        args.output,
        args.title,
        args.font_size,
        not args.no_chrome,
        args.bg,
        max(1, args.scale),
    )
    print(f"wrote {args.output} ({width}x{height})")


if __name__ == "__main__":
    main()
