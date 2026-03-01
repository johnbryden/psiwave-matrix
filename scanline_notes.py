#!/usr/bin/env -S python3 -u
"""
Scanline-notes effect: vertical line sweeps L->R->L once per bar.
Note-on trails horizontal lines at note rows from the point of impact until note-off.
"""

from __future__ import annotations
from typing import List, Optional, Tuple
from effect import Effect
from midi import MidiNote

# We will calculate these dynamically in setup() to fit the matrix
DEFAULT_ROWS_PER_SLOT = 2
# Full cycle = 8 beats (4 beats L→R, 4 beats R→L). Fallback at 120 BPM: 8 beats = 4 sec
BAR_DURATION = 4.0
# How long (ms) note bars persist after note-off, fading to black
TRAIL_SUSTAIN_MS = 1500

# Base colour per MIDI channel (1-16).
CHANNEL_COLORS = [
    (255, 80, 80),     # 1: red
    (255, 160, 60),    # 2: orange
    (255, 220, 60),    # 3: amber / gold
    (200, 255, 80),    # 4: lime
    (80, 255, 120),    # 5: green
    (60, 255, 200),    # 6: teal
    (60, 220, 255),    # 7: cyan
    (80, 140, 255),    # 8: sky blue
    (120, 100, 255),   # 9: blue-violet
    (200, 80, 255),    # 10: purple
    (255, 80, 200),    # 11: magenta
    (255, 100, 140),   # 12: pink
    (100, 100, 255),   # 13: periwinkle
    (200, 255, 100),   # 14: yellow-green
    (255, 100, 150),   # 15: rose
    (100, 200, 255),   # 16: light blue
]


def _color_for_note(
    channel: int, note: int, num_slots: int
) -> Tuple[Tuple[int, int, int], float]:
    """Base colour from channel; page = note // num_slots; pages 0,1 full sat, then decrease."""
    base = CHANNEL_COLORS[(channel - 1) % len(CHANNEL_COLORS)]
    page = note // num_slots
    # Pages 0 and 1 fully saturated; as page increases, decrease saturation
    if page <= 1:
        sat = 1.0
    else:
        sat = max(0.2, 1.0 - (page - 1) * 0.25)
    return (base, sat)

class ScanlineNotesEffect(Effect):
    def __init__(self, width: int, height: int, verbose: bool = False):
        super().__init__(width, height)
        self._verbose = verbose
        # Each slot: list of (phase_on, t_off_or_None, phase_off_or_None, channel, note). When released, phase_off = bar end (frozen).
        self._active_note_phases: List[List[Tuple[float, Optional[float], Optional[float], int, int]]] = []
        self._external_sweep_phase: Optional[float] = None
        self._last_t = 0.0
        self._n_slots = 0
        self._rows_per_slot = DEFAULT_ROWS_PER_SLOT
        self._trail_sustain_s = TRAIL_SUSTAIN_MS / 1000.0

    def setup(self, matrix) -> None:
        self.width = int(matrix.width)
        self.height = int(matrix.height)
        
        # Dynamically determine how many note slots fit on this screen
        self._rows_per_slot = DEFAULT_ROWS_PER_SLOT
        self._n_slots = self.height // self._rows_per_slot
        if self._n_slots == 0:
            self._n_slots = self.height
            self._rows_per_slot = 1
            
        self._active_note_phases = [[] for _ in range(self._n_slots)]
        if self._verbose:
            print(f"[Scanline] Setup complete. Matrix: {self.width}x{self.height}, Slots: {self._n_slots}")

    def activate(self) -> None:
        self._active_note_phases = [[] for _ in range(self._n_slots)]
        if self._verbose:
            print("[Scanline] Effect activated.")

    def handle_note(self, note: MidiNote) -> None:
        try:
            n = int(getattr(note, "note", -1))
            velocity = int(getattr(note, "velocity", 0))
            is_on = bool(getattr(note, "is_on", False))
            channel = int(getattr(note, "channel", 1))  # 1-16

            # Standard MIDI handling: velocity 0 often means note off
            if velocity == 0:
                is_on = False
                
            if not (0 <= n <= 127):
                return

            slot = n % self._n_slots
            current_phase = self._get_current_phase(self._last_t)
            
            status_str = "ON" if is_on else "OFF"
            if self._verbose:
                print(f"[Scanline] Note {n} ({status_str}) ch={channel} -> Slot {slot} (Phase: {current_phase:.3f})")

            if is_on:
                self._active_note_phases[slot].append((current_phase, None, None, channel, n))
            else:
                # Mark the first still-held note as released (freeze bar end at current phase, start fade)
                for i, (p, t_off, _, ch, note) in enumerate(self._active_note_phases[slot]):
                    if t_off is None:
                        self._active_note_phases[slot][i] = (p, self._last_t, current_phase, ch, note)
                        break
                else:
                    if self._verbose:
                        print(f"[Scanline] Warning: Received OFF for slot {slot} but no active notes found.")
                    
        except Exception as e:
            if self._verbose:
                print(f"[Scanline] Error handling note: {e}")

    def set_sweep_phase(self, phase: Optional[float]) -> None:
        self._external_sweep_phase = phase

    def _get_current_phase(self, t_point: float) -> float:
        if self._external_sweep_phase is not None:
            return self._external_sweep_phase % 1.0
        return (t_point / BAR_DURATION) % 1.0

    def _phase_to_x(self, phase: float) -> int:
        p = phase % 1.0
        w = max(1, self.width - 1)
        # 0 -> 0.5 is L -> R (0 to w)
        # 0.5 -> 1.0 is R -> L (w to 0)
        if p <= 0.5:
            return int(2.0 * p * w)
        return int(2.0 * (1.0 - p) * w)

    def _draw_row_segment(self, canvas, x0: int, x1: int, y_start: int, y_end: int, base_rgb: Tuple[int, int, int], brightness: float = 1.0):
        lo, hi = (x0, x1) if x0 < x1 else (x1, x0)
        lo = max(0, min(self.width - 1, lo))
        hi = max(0, min(self.width - 1, hi))
        brightness = max(0.0, min(1.0, brightness))
        r, g, b = (int(c * brightness) for c in base_rgb)
        for y in range(y_start, y_end):
            for x in range(lo, hi + 1):
                canvas.SetPixel(x, y, r, g, b)

    def draw(self, canvas, matrix, t_point: float) -> None:
        # Update last_t so handle_note uses correct phase
        self._last_t = t_point
        canvas.Clear()

        current_phase = self._get_current_phase(t_point)
        x_scan = self._phase_to_x(current_phase)

        for s in range(self._n_slots):
            # Drop expired trails (released and past sustain time)
            self._active_note_phases[s] = [
                (p, t, po, ch, note) for (p, t, po, ch, note) in self._active_note_phases[s]
                if t is None or (t_point - t) < self._trail_sustain_s
            ]
            if not self._active_note_phases[s]:
                continue

            # Calculate rows (higher slots = higher on screen)
            y0 = (self._n_slots - 1 - s) * self._rows_per_slot
            y1 = min(y0 + self._rows_per_slot, self.height)

            # Draw lower notes first so higher notes appear in the foreground
            slot_notes = sorted(self._active_note_phases[s], key=lambda nt: nt[4])  # nt[4] = note
            for phase_on, t_off, phase_off, channel, note in slot_notes:
                # Brightness: full while held; linear fade to black over sustain period after release
                if t_off is None:
                    release_brightness = 1.0
                else:
                    age = t_point - t_off
                    release_brightness = max(0.0, 1.0 - age / self._trail_sustain_s)

                base_rgb, page_sat = _color_for_note(channel, note, self._n_slots)
                brightness = release_brightness * page_sat

                # Bar end: keep growing while held; freeze at release so it becomes a hanging block
                if t_off is None:
                    end_phase = current_phase
                else:
                    end_phase = phase_off  # frozen at note-off

                # Phase span of the bar (may wrap or span > 0.5 if held across bounce)
                phase_diff = (end_phase - phase_on) % 1.0
                if phase_diff >= 0.5:
                    # Bar spans full width (or hit both boundaries)
                    self._draw_row_segment(canvas, 0, self.width - 1, y0, y1, base_rgb, brightness)
                else:
                    x_on = self._phase_to_x(phase_on)
                    x_end = self._phase_to_x(end_phase)
                    on_increasing = (phase_on % 1.0) < 0.5
                    end_increasing = (end_phase % 1.0) < 0.5

                    if on_increasing == end_increasing:
                        # Single segment (no bounce in between)
                        self._draw_row_segment(canvas, x_on, x_end, y0, y1, base_rgb, brightness)
                    else:
                        # Bounced once between phase_on and end_phase
                        edge_x = (self.width - 1) if on_increasing else 0
                        self._draw_row_segment(canvas, x_on, edge_x, y0, y1, base_rgb, brightness)
                        self._draw_row_segment(canvas, edge_x, x_end, y0, y1, base_rgb, brightness)

        # Draw Scanline playhead (Bright White/Cyan)
        for y in range(self.height):
            canvas.SetPixel(x_scan, y, 200, 255, 255)