#!/usr/bin/env -S python3 -u

import time
import math
import os
import random
from typing import Optional, Tuple, List

import numpy as np

from effect import Effect, Param
from midi import MidiNote


# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

try:
    _CENTER_CLEAR_RADIUS_PX = float(os.environ.get("PSIWAVE_STARFIELD_CENTER_CLEAR_PX", "2.0"))
except Exception:
    _CENTER_CLEAR_RADIUS_PX = 2.0
if _CENTER_CLEAR_RADIUS_PX < 0.0:
    _CENTER_CLEAR_RADIUS_PX = 0.0

_STAR_COLOR_PALETTE = ("white", "blue", "cyan", "yellow", "orange", "red")

_COLOR_DEADZONE = 0.03


# ---------------------------------------------------------------------------
# Star helper
# ---------------------------------------------------------------------------

def _spawn_near_center(
    matrix_width: int, matrix_height: int,
    *, x_span: float, y_span: float,
) -> Tuple[float, float]:
    center_x = matrix_width / 2
    center_y = matrix_height / 2
    r_min = max(_CENTER_CLEAR_RADIUS_PX, 0.03 * min(matrix_width, matrix_height))
    r2_min = r_min * r_min

    for _ in range(32):
        x = center_x + random.uniform(-x_span, x_span)
        y = center_y + random.uniform(-y_span, y_span)
        dx = x - center_x
        dy = y - center_y
        if (dx * dx + dy * dy) >= r2_min:
            return (x, y)

    a = random.uniform(0.0, 2.0 * math.pi)
    return (center_x + math.cos(a) * r_min, center_y + math.sin(a) * r_min)


class Star:
    __slots__ = ("x", "y", "brightness", "speed", "twinkle_speed", "twinkle_phase", "color_type")

    def __init__(self, matrix_width: int, matrix_height: int):
        self.x, self.y = _spawn_near_center(matrix_width, matrix_height, x_span=15, y_span=12)
        self.brightness = random.randint(50, 255)
        self.speed = random.uniform(1.5, 4.0)
        self.twinkle_speed = random.uniform(0.02, 0.08)
        self.twinkle_phase = random.uniform(0, 2 * math.pi)
        self.color_type: str = random.choice(_STAR_COLOR_PALETTE)

    def update(self, dt: float, matrix_width: int, matrix_height: int, spawn_color_type: Optional[str]) -> None:
        center_x = matrix_width / 2
        center_y = matrix_height / 2
        dx = self.x - center_x
        dy = self.y - center_y

        if abs(dx) > 0.1 or abs(dy) > 0.1:
            length = abs(dx) + abs(dy)
            if length > 0:
                self.x += (dx / length) * self.speed * dt * 2.5
                self.y += (dy / length) * self.speed * dt * 2.5

        if self.x < 0 or self.x >= matrix_width or self.y < 0 or self.y >= matrix_height:
            self.x, self.y = _spawn_near_center(matrix_width, matrix_height, x_span=8, y_span=6)
            if spawn_color_type is not None:
                self.color_type = spawn_color_type
            else:
                self.color_type = random.choice(_STAR_COLOR_PALETTE)

        self.twinkle_phase += self.twinkle_speed * 0.5

    def get_color(self, color_amount: float) -> Tuple[int, int, int]:
        twinkle = 0.5 + 0.5 * math.sin(self.twinkle_phase)
        brightness = int(self.brightness * twinkle)

        if self.color_type == "white":
            colored = (brightness, brightness, brightness)
        elif self.color_type == "blue":
            colored = (brightness // 3, brightness // 3, brightness)
        elif self.color_type == "cyan":
            colored = (brightness // 3, brightness, brightness)
        elif self.color_type == "yellow":
            colored = (brightness, brightness, brightness // 3)
        elif self.color_type == "orange":
            colored = (brightness, brightness // 2, brightness // 6)
        elif self.color_type == "red":
            colored = (brightness, brightness // 6, brightness // 6)
        else:
            colored = (brightness, brightness, brightness)

        a = color_amount
        if a <= _COLOR_DEADZONE:
            return (brightness, brightness, brightness)
        if a >= 1.0:
            return colored
        gray = (brightness, brightness, brightness)
        return (
            int(gray[0] + (colored[0] - gray[0]) * a),
            int(gray[1] + (colored[1] - gray[1]) * a),
            int(gray[2] + (colored[2] - gray[2]) * a),
        )


# ---------------------------------------------------------------------------
# StarfieldEffect
# ---------------------------------------------------------------------------

class StarfieldEffect(Effect):
    """
    Expanding starfield effect.

    Parameters (set via MidiRouter):
        speed        -- speed multiplier (0.5..4 typical, default 1.0)
        color_amount -- 0 = grayscale, 1 = colored (default 1.0)
    """

    speed = Param(default=1.0)
    color_amount = Param(default=1.0)

    def __init__(self, width: int, height: int, num_stars: int = 100):
        super().__init__(width, height)
        self._num_stars = num_stars
        self._stars: Optional[List[Star]] = None
        self._last_t_point: Optional[float] = None
        self._pixel_state: Optional[np.ndarray] = None
        self._spawn_color_type: Optional[str] = None
        self._debug = os.environ.get("PSIWAVE_DEBUG_STARFIELD", "").strip() not in ("", "0", "false", "False", "no", "NO")
        self._debug_last_draw_log_t = -1e9

    def set_debug(self, enabled: bool = True) -> None:
        self._debug = bool(enabled)

    def set_spawn_color_type(self, color_type) -> None:
        """Override the color assigned to newly-spawned stars (e.g. beat-synced)."""
        if color_type is None:
            self._spawn_color_type = None
            return
        ct = str(color_type)
        if ct in _STAR_COLOR_PALETTE:
            self._spawn_color_type = ct

    def setup(self, matrix) -> None:
        self.width = int(matrix.width)
        self.height = int(matrix.height)
        self._pixel_state = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        self._stars = [Star(self.width, self.height) for _ in range(self._num_stars)]
        self._last_t_point = None

    def activate(self) -> None:
        self._last_t_point = None

    def draw(self, canvas, matrix, t_point: float) -> None:
        w = int(matrix.width)
        h = int(matrix.height)

        if self._stars is None or self._pixel_state is None:
            self.setup(matrix)
            assert self._stars is not None and self._pixel_state is not None

        speed_mult = self.get_param("speed")
        color_amount = self.get_param("color_amount")

        if self._last_t_point is None:
            dt = 0.0
        else:
            dt = max(0.0, t_point - self._last_t_point) * speed_mult
        self._last_t_point = t_point

        self._pixel_state.fill(0)

        for star in self._stars:
            star.update(dt, w, h, self._spawn_color_type)

        for star in self._stars:
            sx, sy = int(star.x), int(star.y)
            if 0 <= sy < h and 0 <= sx < w:
                r, g, b = star.get_color(color_amount)
                self._pixel_state[sy, sx] = [r, g, b]
                canvas.SetPixel(sx, sy, r, g, b)

        if self._debug and (t_point - self._debug_last_draw_log_t) >= 1.0:
            self._debug_last_draw_log_t = t_point
            print(
                f"[starfield] draw t={t_point:.2f}s dt={dt:.4f}s speed_mult={speed_mult:.3f} "
                f"color_amount={color_amount:.3f} stars={len(self._stars)}"
            )


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from rgbmatrix import RGBMatrix, RGBMatrixOptions

    options = RGBMatrixOptions()
    options.rows = 40
    options.cols = 80
    options.hardware_mapping = 'adafruit-hat'
    options.gpio_slowdown = 1
    options.brightness = 50
    options.pwm_bits = 8
    options.pwm_lsb_nanoseconds = 250
    options.multiplexing = 20
    options.disable_hardware_pulsing = True

    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()

    effect = StarfieldEffect(matrix.width, matrix.height)
    effect.setup(matrix)
    effect.activate()

    start_time = time.time()
    print("Starting starfield animation... Press CTRL-C to stop.")
    try:
        while True:
            current_time = time.time()
            canvas.Clear()
            t_point = current_time - start_time
            effect.draw(canvas, matrix, t_point)
            canvas = matrix.SwapOnVSync(canvas)
            elapsed = time.time() - current_time
            frame_time = 1.0 / 60
            if elapsed < frame_time:
                time.sleep(frame_time - elapsed)
    except KeyboardInterrupt:
        print("\nExiting...")
        matrix.Clear()
