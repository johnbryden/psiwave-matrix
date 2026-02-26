#!/usr/bin/env -S python3 -u

import math
from dataclasses import dataclass
from typing import List


N_LAYERS = 12

# Fixed 12-color palette (layer index == pitch class == note % 12)
_PALETTE: List[tuple[int, int, int]] = [
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


def _scale_color(rgb: tuple[int, int, int], s: float) -> tuple[int, int, int]:
    if s <= 0.0:
        return (0, 0, 0)
    if s >= 1.0:
        return rgb
    r, g, b = rgb
    return (int(r * s), int(g * s), int(b * s))


@dataclass(frozen=True, slots=True)
class MidiNote:
    """
    Local mirror of main.MidiNote shape so this module can be imported standalone.
    main.py will pass its MidiNote instances; we only require compatible attributes.
    """

    channel: int
    note: int
    velocity: int
    is_on: bool
    t: float


_held_pc_counts: List[int] = [0] * N_LAYERS
_last_matrix_size: tuple[int, int] | None = None


def setup(matrix) -> None:
    global _last_matrix_size
    _last_matrix_size = (int(matrix.width), int(matrix.height))


def activate() -> None:
    global _held_pc_counts
    _held_pc_counts = [0] * N_LAYERS


def handle_midi_note(note_msg) -> None:
    """
    Handle MIDI note events from main.py.

    Mapping: layer_index = note % 12 (pitch class).
    Behavior: highlight any pitch class currently held (supports chords and octave stacking).
    """
    global _held_pc_counts
    try:
        n = int(getattr(note_msg, "note", -1))
        is_on = bool(getattr(note_msg, "is_on", False))
    except Exception:
        return
    if not (0 <= n <= 127):
        return
    pc = n % N_LAYERS
    if is_on:
        _held_pc_counts[pc] = int(_held_pc_counts[pc]) + 1
    else:
        c = int(_held_pc_counts[pc]) - 1
        if c < 0:
            c = 0
        _held_pc_counts[pc] = c


def draw(canvas, matrix, t_point: float, colour=None) -> None:
    """
    Render 12 stacked 1-pixel sinewave layers across the display width.

    - Higher layers: dimmer + shorter wavelength + smaller amplitude (perspective)
    - Undulation: second sine wave added on top of the first
    - Highlight: any held pitch class (note % 12) draws at full palette color
    """
    global _last_matrix_size
    w = int(matrix.width)
    h = int(matrix.height)
    if _last_matrix_size != (w, h):
        setup(matrix)

    # Vertical placement (perspective): cluster more layers toward the top.
    y_bottom = h - 2
    y_top = max(1, h // 8)

    # Base wave parameters tuned for 80x40-ish matrices.
    base_freq = 0.12
    top_freq_mult = 2.8
    base_amp = max(1.0, h * 0.11)      # ~4.4 at h=40
    top_amp_mult = 0.35

    # Animation speeds (phase domain).
    speed1 = 2.6
    speed2 = 1.2

    for i in range(N_LAYERS):
        d = 0.0 if N_LAYERS <= 1 else (i / (N_LAYERS - 1))

        # Perspective distribution: exponent < 1 => more layers nearer the top.
        t = d ** 0.6
        y_base = _lerp(float(y_bottom), float(y_top), t)

        freq1 = base_freq * _lerp(1.0, top_freq_mult, d)
        amp1 = base_amp * _lerp(1.0, top_amp_mult, d)

        # Second wave for undulation: smaller amplitude and slower drift.
        freq2 = freq1 * 0.55
        amp2 = amp1 * 0.35

        phase_layer = d * 1.7
        phase1 = (t_point * speed1) + phase_layer
        phase2 = (t_point * speed2) - (phase_layer * 0.6)

        highlighted = _held_pc_counts[i] > 0
        if highlighted:
            rgb = _PALETTE[i]
        else:
            # Depth dimming: top layers are dimmer.
            dim = _lerp(0.95, 0.18, d)
            rgb = _scale_color(_PALETTE[i], dim)

        r, g, b = rgb
        if r == 0 and g == 0 and b == 0:
            continue

        for x in range(w):
            y = y_base + (amp1 * math.sin(freq1 * x + phase1)) + (amp2 * math.sin(freq2 * x + phase2))
            yi = int(round(y))
            if 0 <= yi < h:
                canvas.SetPixel(x, yi, r, g, b)


if __name__ == "__main__":
    # Minimal standalone smoke test (no MIDI). Useful on dev machines.
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
    setup(matrix)
    activate()
    start = time.time()
    try:
        while True:
            t = time.time() - start
            canvas.Clear()
            draw(canvas, matrix, t)
            canvas = matrix.SwapOnVSync(canvas)
    except KeyboardInterrupt:
        matrix.Clear()
