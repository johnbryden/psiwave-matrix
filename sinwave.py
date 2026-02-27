#!/usr/bin/env -S python3 -u

import time
import math
from typing import Optional

import numpy as np

from effect import Effect, Param
from midi import MidiNote


class SinwaveEffect(Effect):
    """
    Sine wave effect with vertical bar overlay.

    Parameters (set via MidiRouter):
        speed       -- speed multiplier (0..2 typical, default 1.0)
        wavelength  -- wavelength multiplier (default 1.0; lower = shorter)
        color       -- raw CC value 0..127 morphing between COLOR_1 and COLOR_2
        phase_offset -- manual phase offset in radians (0..2pi)
    """

    speed = Param(default=1.0)
    wavelength = Param(default=1.0)
    color = Param(default=0.0)
    phase_offset = Param(default=0.0)

    COLOR_1 = (50, 50, 255)
    COLOR_2 = (255, 50, 50)
    _BASE_SPEED = 5.0
    _BASE_FREQUENCY = 0.15
    _MAX_DT = 0.10

    def __init__(self, width: int, height: int):
        super().__init__(width, height)
        self.pixel_state: Optional[np.ndarray] = None
        self._phase_accum = 0.0
        self._last_t_point: Optional[float] = None
        self._external_phase: Optional[float] = None

    def setup(self, matrix) -> None:
        self.width = int(matrix.width)
        self.height = int(matrix.height)
        self.pixel_state = np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def activate(self) -> None:
        self._phase_accum = 0.0
        self._last_t_point = None
        self._external_phase = None

    def set_external_phase(self, phase: Optional[float]) -> None:
        """Override internal phase integrator (for MIDI clock sync)."""
        if phase is None:
            self._external_phase = None
            self._last_t_point = None
            return
        try:
            self._external_phase = float(phase)
        except Exception:
            return

    def set_wavelength_mult(self, mult: float) -> None:
        """Direct setter for MIDI clock spatial-wavelength sync."""
        try:
            m = float(mult)
        except Exception:
            return
        if m < 0.01:
            m = 0.01
        self.params["wavelength"].value = m

    # -- Rendering helpers ---------------------------------------------------

    def _clear_pixel_state(self) -> None:
        if self.pixel_state is not None:
            self.pixel_state.fill(0)

    @staticmethod
    def _fast_color_dim(color, dim_factor):
        return int(color[0] * dim_factor), int(color[1] * dim_factor), int(color[2] * dim_factor)

    def _draw_pixels(self, canvas, x, y, c1, c2, c3, blend=True):
        ps = self.pixel_state
        if blend and ps is not None:
            cur = ps[y, x]
            r = max(int(cur[0]), c1)
            g = max(int(cur[1]), c2)
            b = max(int(cur[2]), c3)
            ps[y, x] = [r, g, b]
            canvas.SetPixel(x, y, r, g, b)
            return (r, g, b)
        if ps is not None:
            ps[y, x] = [c1, c2, c3]
        canvas.SetPixel(x, y, c1, c2, c3)
        return (c1, c2, c3)

    @staticmethod
    def _lerp_color(c1, c2, t):
        return (
            int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t),
        )

    def _draw_sine_wave(
        self, canvas, matrix, t_point, colour=(255, 255, 255),
        amplitude=9, frequency=0.2, width=4, dim_factor=0.6,
        speed=5, phase_offset=0.0, blend=True,
    ):
        vertical_offset = matrix.height / 4 + width - 2
        phase = (t_point * speed) + phase_offset

        for x in range(matrix.width):
            y_center = round(amplitude * math.sin(frequency * x + phase) + vertical_offset)

            if 0 <= y_center < matrix.height:
                self._draw_pixels(canvas, x, y_center, colour[0], colour[1], colour[2], blend=blend)

            soft_colour = colour
            for w in range(1, width):
                soft_colour = self._fast_color_dim(soft_colour, dim_factor)
                if y_center - w >= 0:
                    self._draw_pixels(canvas, x, y_center - w, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
                if y_center + w < matrix.height:
                    self._draw_pixels(canvas, x, y_center + w, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)

    def _draw_vertical_bar(self, canvas, matrix, colour, x_centre_factor=0.3, width=4, dim_factor=0.6, blend=True):
        ps = self.pixel_state
        for y in range(matrix.height):
            x_centre = int(x_centre_factor * matrix.width)
            self._draw_pixels(canvas, x_centre, y, colour[0], colour[1], colour[2], blend=blend)

            soft_colour = colour
            for w in range(1, width):
                soft_colour = self._fast_color_dim(soft_colour, dim_factor)
                if x_centre + w < matrix.width:
                    self._draw_pixels(canvas, x_centre + w, y, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
                if x_centre - w >= 0:
                    self._draw_pixels(canvas, x_centre - w, y, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)

    # -- Main draw -----------------------------------------------------------

    def draw(self, canvas, matrix, t_point, colour=None) -> None:
        if self.pixel_state is None or self.pixel_state.shape[0] != matrix.height or self.pixel_state.shape[1] != matrix.width:
            self.setup(matrix)

        if colour is None:
            morph = self.get_param("color") / 127.0
            morph = max(0.0, min(1.0, morph))
            colour = self._lerp_color(self.COLOR_1, self.COLOR_2, morph)

        self._clear_pixel_state()

        if self._external_phase is None:
            if self._last_t_point is None:
                dt = 0.0
            else:
                dt = t_point - self._last_t_point
                if dt < 0.0:
                    dt = 0.0
                elif dt > self._MAX_DT:
                    dt = self._MAX_DT
            self._last_t_point = t_point

            speed = self._BASE_SPEED * self.get_param("speed")
            self._phase_accum += speed * dt
            phase_for_draw = self._phase_accum
        else:
            phase_for_draw = self._external_phase

        wl = self.get_param("wavelength")
        if wl < 0.0001:
            wl = 0.0001
        frequency = self._BASE_FREQUENCY / wl

        self._draw_sine_wave(
            canvas, matrix, phase_for_draw,
            colour=colour, frequency=frequency, speed=1.0,
            phase_offset=self.get_param("phase_offset"), blend=False,
        )
        self._draw_vertical_bar(canvas, matrix, colour, blend=True)


# ---------------------------------------------------------------------------
# Standalone execution (for testing on hardware without main.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from rgbmatrix import RGBMatrix, RGBMatrixOptions

    options = RGBMatrixOptions()
    options.rows = 40
    options.cols = 80
    options.hardware_mapping = 'adafruit-hat'
    options.gpio_slowdown = 2
    options.brightness = 70
    options.pwm_bits = 8
    options.pwm_lsb_nanoseconds = 250
    options.multiplexing = 20
    options.disable_hardware_pulsing = True

    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()

    effect = SinwaveEffect(matrix.width, matrix.height)
    effect.setup(matrix)
    effect.activate()

    start_time = time.time()
    print("Starting sine wave animation... Press CTRL-C to stop.")
    try:
        while True:
            canvas.Clear()
            t_point = time.time() - start_time
            effect.draw(canvas, matrix, t_point)
            canvas = matrix.SwapOnVSync(canvas)
    except KeyboardInterrupt:
        print("\nExiting...")
        matrix.Clear()
