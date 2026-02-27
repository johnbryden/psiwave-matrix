#!/usr/bin/env -S python3 -u
"""
Text scroll effect: scrolls a message across the matrix.
On Pi: uses rgbmatrix.graphics + BDF font (pixel fonts from rpi-rgb-led-matrix).
On screen / no BDF: falls back to PIL.
"""

import math
import os
import sys
from typing import Optional, Tuple

_message = "ψ~ PsiWave ψ~"
_speed_mult = 1.0
_color_cc_value = 0  # 0..127: white -> hue cycle
_external_scroll_phase: Optional[float] = None
_matrix_width = 80
_matrix_height = 40

# Cached PIL render when using fallback
_cached_msg: Optional[str] = None
_cached_img = None
_cached_w = 0
_cached_h = 0

# rgbmatrix.graphics path (Pi): BDF font + DrawText
_native_font = None
_use_native_font = False
_cached_native_msg_w: Optional[int] = None
_native_font_height = 13  # 7x13 default

_FONT_SIZE = 14
_RENDER_SCALE = 3


def _try_load_native_font() -> bool:
    """Load BDF font from rgbmatrix (Pi). Returns True if we can use native DrawText."""
    global _native_font, _use_native_font, _native_font_height
    try:
        from rgbmatrix import graphics
    except ImportError:
        return False
    font = graphics.Font()
    # Prefer 9x15 (readable), then 7x13; look in project fonts/ then env
    candidates = []
    if os.environ.get("PSIWAVE_BDF_FONT"):
        candidates.append(os.environ.get("PSIWAVE_BDF_FONT"))
    _dir = os.path.dirname(os.path.abspath(__file__))
    candidates.extend([
        os.path.join(_dir, "fonts", "9x15.bdf"),
        os.path.join(_dir, "fonts", "7x13.bdf"),
        os.path.join(_dir, "fonts", "10x20.bdf"),
    ])
    if "rgbmatrix" in sys.modules:
        try:
            rmbase = os.path.dirname(os.path.abspath(sys.modules["rgbmatrix"].__file__))
            candidates.extend([
                os.path.join(rmbase, "..", "..", "..", "fonts", "9x15.bdf"),
                os.path.join(rmbase, "..", "..", "..", "fonts", "7x13.bdf"),
            ])
        except Exception:
            pass
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                font.LoadFont(path)
                _native_font = font
                _use_native_font = True
                if "9x15" in path or "9x18" in path:
                    _native_font_height = 15
                elif "10x20" in path:
                    _native_font_height = 20
                else:
                    _native_font_height = 13
                return True
            except Exception:
                continue
    return False


def _get_font(size: int):
    """Load a font that supports Latin + Greek (Ψ, ψ, ~)."""
    try:
        from PIL import ImageFont
        for name in ("DejaVuSans.ttf", "DejaVu Sans", "Arial.ttf", "Arial"):
            try:
                return ImageFont.truetype(name, size)
            except (OSError, IOError):
                continue
        return ImageFont.load_default()
    except ImportError:
        return None


def _render_message() -> None:
    """Render _message at high res, then downsample for smooth (antialiased) text."""
    global _cached_msg, _cached_img, _cached_w, _cached_h
    if _cached_msg == _message and _cached_img is not None:
        return
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        _cached_img = None
        _cached_msg = _message
        return
    render_size = _FONT_SIZE * _RENDER_SCALE
    font = _get_font(render_size)
    if font is None:
        _cached_img = None
        _cached_msg = _message
        return
    # Render at scale x size (white on black)
    img = Image.new("RGB", (2000, render_size + 16), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((0, 0), _message, fill=(255, 255, 255), font=font)
    bbox = img.getbbox()
    if bbox:
        img = img.crop((0, 0, bbox[2] + 4, bbox[3] + 4))
    # Downsample with high-quality resampling for smooth edges (reduces aliasing)
    target_h = _FONT_SIZE
    w, h = img.size
    if h > 0:
        target_w = max(1, int(w * target_h / h))
        resample = getattr(Image.Resampling, "LANCZOS", None) or getattr(Image, "LANCZOS", Image.BICUBIC)
        img = img.resize((target_w, target_h), resample)
    _cached_img = img
    _cached_w, _cached_h = img.size
    _cached_msg = _message
    return


def _hue_to_rgb(h: float) -> Tuple[int, int, int]:
    h = h - math.floor(h)
    i = int(h * 6.0) % 6
    f = h * 6.0 - int(h * 6.0)
    if i == 0:
        return (255, int(255 * f), 0)
    if i == 1:
        return (int(255 * (1 - f)), 255, 0)
    if i == 2:
        return (0, 255, int(255 * f))
    if i == 3:
        return (0, int(255 * (1 - f)), 255)
    if i == 4:
        return (int(255 * f), 0, 255)
    return (255, 0, int(255 * (1 - f)))


def _text_color(t_point: float) -> Tuple[int, int, int]:
    u = max(0.0, min(1.0, _color_cc_value / 127.0))
    white = (255, 255, 255)
    hue = (t_point * 0.5 + _color_cc_value / 127.0) % 1.0
    r = int(white[0] * (1 - u) + _hue_to_rgb(hue)[0] * u)
    g = int(white[1] * (1 - u) + _hue_to_rgb(hue)[1] * u)
    b = int(white[2] * (1 - u) + _hue_to_rgb(hue)[2] * u)
    return (r, g, b)


def _scroll_phase_px(t_point: float) -> float:
    if _external_scroll_phase is not None:
        return _external_scroll_phase
    return t_point * 2.4 * _speed_mult


def setup(matrix) -> None:
    global _matrix_width, _matrix_height, _cached_native_msg_w
    _matrix_width = int(matrix.width)
    _matrix_height = int(matrix.height)
    _cached_native_msg_w = None
    _try_load_native_font()


def activate() -> None:
    pass


def set_text(msg: str) -> None:
    global _message, _cached_native_msg_w
    _message = str(msg) if msg else " "
    _cached_native_msg_w = None


def set_speed_mult(mult: float) -> None:
    global _speed_mult
    _speed_mult = float(mult)


def set_color_cc_value(value: int) -> None:
    global _color_cc_value
    _color_cc_value = max(0, min(127, int(value)))


def set_scroll_phase(phase: Optional[float]) -> None:
    global _external_scroll_phase
    _external_scroll_phase = phase


def draw(canvas, matrix, t_point: float) -> None:
    global _cached_native_msg_w
    w = matrix.width
    h = matrix.height
    r, g, b = _text_color(t_point)
    phase_px = _scroll_phase_px(t_point)

    # Pi: use rgbmatrix.graphics + BDF (pixel font)
    if _use_native_font and _native_font is not None and not hasattr(canvas, "_buffer"):
        try:
            from rgbmatrix import graphics
            msg_w = _cached_native_msg_w or (w * 2)
            cycle = msg_w + w
            px_offset = w - (int(phase_px) % cycle)
            y0 = max(0, (h - _native_font_height) // 2)
            color = graphics.Color(r, g, b)
            text_w = graphics.DrawText(canvas, _native_font, px_offset, y0 + _native_font_height - 1, color, _message)
            if _cached_native_msg_w is None:
                _cached_native_msg_w = text_w
            return
        except Exception:
            pass

    # Fallback: PIL (screen or no BDF)
    _render_message()
    if _cached_img is None:
        return
    msg_w = _cached_w
    cycle = msg_w + w
    src_x = int(phase_px) % cycle
    img = _cached_img
    iw, ih = img.size
    y0 = max(0, (h - ih) // 2)
    for dy in range(ih):
        y = y0 + dy
        if y < 0 or y >= h:
            continue
        for dx in range(w):
            virtual = src_x + dx
            if virtual < msg_w:
                pixel = img.getpixel((virtual, dy))
            else:
                pixel = (0, 0, 0)
            br = pixel[0] / 255.0
            canvas.SetPixel(dx, y, int(r * br), int(g * br), int(b * br))
