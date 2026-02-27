#!/usr/bin/env -S python3 -u
"""
Text scroll effect: scrolls a message across the matrix.
On Pi: uses rgbmatrix.graphics + BDF font.
On screen / no BDF: falls back to PIL.
"""

import math
import os
import sys
from typing import Optional, Tuple

from effect import Effect, Param
from midi import MidiNote

_FONT_SIZE = 14
_RENDER_SCALE = 3


class TextScrollEffect(Effect):
    """
    Scrolling text effect.

    Parameters (set via MidiRouter):
        speed -- scroll speed multiplier (default 1.0)
        color -- raw CC value 0..127; 0 = white, 127 = hue cycle
    """

    speed = Param(default=1.0)
    color = Param(default=0.0)

    def __init__(self, width: int, height: int, message: str = "\u03c8~ PsiWave \u03c8~"):
        super().__init__(width, height)
        self._message = message
        self._external_scroll_phase: Optional[float] = None

        # PIL fallback state
        self._cached_msg: Optional[str] = None
        self._cached_img = None
        self._cached_w = 0
        self._cached_h = 0

        # rgbmatrix.graphics (Pi) state
        self._native_font = None
        self._use_native_font = False
        self._cached_native_msg_w: Optional[int] = None
        self._native_font_height = 13

    # -- Font loading --------------------------------------------------------

    def _try_load_native_font(self) -> bool:
        try:
            from rgbmatrix import graphics  # type: ignore
        except ImportError:
            return False
        font = graphics.Font()
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
                    self._native_font = font
                    self._use_native_font = True
                    if "9x15" in path or "9x18" in path:
                        self._native_font_height = 15
                    elif "10x20" in path:
                        self._native_font_height = 20
                    else:
                        self._native_font_height = 13
                    return True
                except Exception:
                    continue
        return False

    @staticmethod
    def _get_font(size: int):
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

    def _render_message(self) -> None:
        if self._cached_msg == self._message and self._cached_img is not None:
            return
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self._cached_img = None
            self._cached_msg = self._message
            return
        render_size = _FONT_SIZE * _RENDER_SCALE
        font = self._get_font(render_size)
        if font is None:
            self._cached_img = None
            self._cached_msg = self._message
            return
        img = Image.new("RGB", (2000, render_size + 16), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.text((0, 0), self._message, fill=(255, 255, 255), font=font)
        bbox = img.getbbox()
        if bbox:
            img = img.crop((0, 0, bbox[2] + 4, bbox[3] + 4))
        target_h = _FONT_SIZE
        w, h = img.size
        if h > 0:
            target_w = max(1, int(w * target_h / h))
            resample = getattr(Image.Resampling, "LANCZOS", None) or getattr(Image, "LANCZOS", Image.BICUBIC)
            img = img.resize((target_w, target_h), resample)
        self._cached_img = img
        self._cached_w, self._cached_h = img.size
        self._cached_msg = self._message

    # -- Color helpers -------------------------------------------------------

    @staticmethod
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

    def _text_color(self, t_point: float) -> Tuple[int, int, int]:
        cc_val = self.get_param("color")
        u = max(0.0, min(1.0, cc_val / 127.0))
        white = (255, 255, 255)
        hue = (t_point * 0.5 + cc_val / 127.0) % 1.0
        hr, hg, hb = self._hue_to_rgb(hue)
        r = int(white[0] * (1 - u) + hr * u)
        g = int(white[1] * (1 - u) + hg * u)
        b = int(white[2] * (1 - u) + hb * u)
        return (r, g, b)

    def _scroll_phase_px(self, t_point: float) -> float:
        if self._external_scroll_phase is not None:
            return self._external_scroll_phase
        return t_point * 2.4 * self.get_param("speed")

    # -- Public interface for clock sync -------------------------------------

    def set_scroll_phase(self, phase: Optional[float]) -> None:
        """Override time-based scroll with an external phase (e.g. MIDI clock)."""
        self._external_scroll_phase = phase

    def set_text(self, msg: str) -> None:
        self._message = str(msg) if msg else " "
        self._cached_native_msg_w = None

    # -- Lifecycle -----------------------------------------------------------

    def setup(self, matrix) -> None:
        self.width = int(matrix.width)
        self.height = int(matrix.height)
        self._cached_native_msg_w = None
        self._try_load_native_font()

    def activate(self) -> None:
        pass

    # -- Draw ----------------------------------------------------------------

    def draw(self, canvas, matrix, t_point: float) -> None:
        w = matrix.width
        h = matrix.height
        r, g, b = self._text_color(t_point)
        phase_px = self._scroll_phase_px(t_point)

        # Pi: use rgbmatrix.graphics + BDF
        if self._use_native_font and self._native_font is not None and not hasattr(canvas, "_buffer"):
            try:
                from rgbmatrix import graphics
                msg_w = self._cached_native_msg_w or (w * 2)
                cycle = msg_w + w
                px_offset = w - (int(phase_px) % cycle)
                y0 = max(0, (h - self._native_font_height) // 2)
                color = graphics.Color(r, g, b)
                text_w = graphics.DrawText(canvas, self._native_font, px_offset, y0 + self._native_font_height - 1, color, self._message)
                if self._cached_native_msg_w is None:
                    self._cached_native_msg_w = text_w
                return
            except Exception:
                pass

        # Fallback: PIL
        self._render_message()
        if self._cached_img is None:
            return
        msg_w = self._cached_w
        cycle = msg_w + w
        src_x = int(phase_px) % cycle
        img = self._cached_img
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
