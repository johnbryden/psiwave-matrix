#!/usr/bin/env -S python3 -u

from __future__ import annotations

import math
import random
from typing import List, Optional, Tuple

from effect import Effect, Param
from midi import MidiNote


N_LAYERS = 12

_PALETTE: List[Tuple[int, int, int]] = [
    (255, 40, 40),    # 0
    (255, 140, 0),    # 1
    (255, 220, 0),    # 2
    (160, 255, 0),    # 3
    (0, 255, 70),     # 4
    (0, 255, 180),    # 5
    (0, 220, 255),    # 6
    (0, 120, 255),    # 7
    (40, 40, 255),    # 8
    (140, 0, 255),    # 9
    (255, 0, 200),    # 10
    (255, 0, 90),     # 11
]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp01(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return float(x)


def _scale_color(rgb: Tuple[int, int, int], s: float) -> Tuple[int, int, int]:
    if s <= 0.0:
        return (0, 0, 0)
    if s >= 1.0:
        return rgb
    r, g, b = rgb
    return (int(r * s), int(g * s), int(b * s))


class MultiSinwavesEffect(Effect):
    """
    12 stacked sinewave layers with MIDI note highlighting.

    Layer index == pitch class (note % 12). Held notes highlight their layer.
    No CC parameters -- driven purely by MIDI notes.
    """

    def __init__(self, width: int, height: int):
        super().__init__(width, height)
        self._held_pc_counts: List[int] = [0] * N_LAYERS
        self._last_matrix_size: Optional[Tuple[int, int]] = None
        self._layer_drift: List[Tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 0.0)] * N_LAYERS

    def _reset_layer_drift(self) -> None:
        self._layer_drift = []
        for i in range(N_LAYERS):
            rng = random.Random((i + 1) * 15485863)
            self._layer_drift.append((
                rng.uniform(-0.35, 0.35),
                rng.uniform(0.08, 0.24),
                rng.uniform(0.03, 0.09),
                rng.uniform(0.02, 0.06),
            ))

    def setup(self, matrix) -> None:
        self.width = int(matrix.width)
        self.height = int(matrix.height)
        self._last_matrix_size = (self.width, self.height)
        self._reset_layer_drift()

    def activate(self) -> None:
        self._held_pc_counts = [0] * N_LAYERS
        self._reset_layer_drift()

    def handle_note(self, note: MidiNote) -> None:
        try:
            n = int(getattr(note, "note", -1))
            is_on = bool(getattr(note, "is_on", False))
        except Exception:
            return
        if not (0 <= n <= 127):
            return
        pc = n % N_LAYERS
        if is_on:
            self._held_pc_counts[pc] += 1
        else:
            c = self._held_pc_counts[pc] - 1
            if c < 0:
                c = 0
            self._held_pc_counts[pc] = c

    def draw(self, canvas, matrix, t_point: float, colour=None) -> None:
        w = int(matrix.width)
        h = int(matrix.height)
        if self._last_matrix_size != (w, h):
            self.setup(matrix)

        y_bottom = h - 2
        y_top = max(1, h // 8)

        base_freq = 0.12
        top_freq_mult = 2.8
        base_amp = max(1.0, h * 0.11)
        top_amp_mult = 0.35

        speed1 = 2.6
        speed2 = 1.6
        center_x = 0.5 * float(w - 1)
        min_perspective_scale = 0.28

        for i in range(N_LAYERS - 1, -1, -1):
            d = 0.0 if N_LAYERS <= 1 else (i / (N_LAYERS - 1))
            t = d ** 0.6
            y_base = _lerp(float(y_bottom), float(y_top), t)

            freq1 = base_freq * _lerp(1.0, top_freq_mult, d)
            amp1 = base_amp * _lerp(1.0, top_amp_mult, d)
            freq2 = freq1 * 2.2
            amp2 = amp1 * 0.22

            phase_layer = d * 1.7
            drift_phase, drift_speed, drift_f1, drift_f2 = self._layer_drift[i]
            drift = math.sin((t_point * drift_speed) + drift_phase)
            freq1 *= (1.0 + (drift_f1 * drift))
            freq2 *= (1.0 - (drift_f2 * drift))
            phase1 = (t_point * speed1) + phase_layer + (0.25 * drift)
            phase2 = (t_point * speed2) - (phase_layer * 0.6) - (0.15 * drift)

            highlighted = self._held_pc_counts[i] > 0
            if highlighted:
                rgb = _PALETTE[i]
            else:
                dim = _lerp(0.22, 0.05, d)
                rgb = _scale_color(_PALETTE[i], dim)

            r, g, b = rgb
            if r == 0 and g == 0 and b == 0:
                continue

            perspective_scale = _lerp(1.0, min_perspective_scale, d)
            inv_scale = 1.0 / perspective_scale
            for x_screen in range(w):
                x_projected = center_x + ((float(x_screen) - center_x) * inv_scale)
                y = y_base + (
                    amp1 * math.sin((freq1 * x_projected) + phase1)
                ) + (
                    amp2 * math.sin((freq2 * x_projected) + phase2)
                )
                yi = int(round(y))
                if 0 <= yi < h:
                    canvas.SetPixel(x_screen, yi, r, g, b)


# ---------------------------------------------------------------------------
# Standalone execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time
    from rgbmatrix import RGBMatrix, RGBMatrixOptions

    options = RGBMatrixOptions()
    options.rows = 40
    options.cols = 80
    options.hardware_mapping = "adafruit-hat"
    options.gpio_slowdown = 2
    options.brightness = 60
    options.pwm_bits = 8
    options.pwm_lsb_nanoseconds = 250
    options.multiplexing = 20
    options.disable_hardware_pulsing = True

    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()

    effect = MultiSinwavesEffect(matrix.width, matrix.height)
    effect.setup(matrix)
    effect.activate()
    start = time.time()
    try:
        while True:
            t = time.time() - start
            canvas.Clear()
            effect.draw(canvas, matrix, t)
            canvas = matrix.SwapOnVSync(canvas)
    except KeyboardInterrupt:
        matrix.Clear()
