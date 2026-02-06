#!/usr/bin/env -S python3 -u

import time
import math
import numpy as np
from rgbmatrix import RGBMatrix, RGBMatrixOptions

# MIDI-driven color morph (defaults)
COLOR_1 = (50, 50, 255)
COLOR_2 = (255, 50, 50)
_last_cc_value = 0  # 0-127

# Optional MIDI-driven motion controls (set by main.py)
_BASE_SPEED = 5.0
_speed_mult = 1.0        # 0..2 (typical)
_phase_offset = 0.0      # radians, 0..2π

# Optional MIDI-driven wavelength control (set by main.py)
# This is implemented as a multiplier on the existing "frequency" coefficient used in:
#   sin(frequency * x + phase)
# We interpret the control as a *wavelength* multiplier:
#   smaller multiplier -> shorter wavelength
# To achieve that while this function consumes a frequency coefficient, we invert it:
#   effective_frequency = _BASE_FREQUENCY / wavelength_mult
_BASE_FREQUENCY = 0.15
_wavelength_mult = 1.0    # 1.0 at CC=0 .. 0.25 at CC=127

# Motion integrator state:
# We integrate phase as ∫(speed dt) so speed changes don't cause a phase jump.
_phase_accum = 0.0
_last_t_point = None  # type: float | None
_MAX_DT = 0.10  # seconds; clamp to avoid huge jumps after stalls/switches
_external_phase = None  # type: float | None  # when set, overrides internal phase integrator (for MIDI clock sync)

# Global pixel state as a numpy array for fast access
# Shape: (height, width, 3) for RGB values
pixel_state = None

def init_pixel_state(height, width):
    """Initialize the pixel state array"""
    global pixel_state
    pixel_state = np.zeros((height, width, 3), dtype=np.uint8)

def _fast_pixel_blend(current_color, c1, c2, c3):
    """Pixel blending (no numba dependency)."""
    blended_r = max(current_color[0], c1)
    blended_g = max(current_color[1], c2)
    blended_b = max(current_color[2], c3)
    return blended_r, blended_g, blended_b


def _fast_color_dim(color, dim_factor):
    """Color dimming helper (no numba dependency)."""
    return int(color[0] * dim_factor), int(color[1] * dim_factor), int(color[2] * dim_factor)

def draw_pixels(canvas, x, y, c1, c2, c3, blend=True, buffer=True):
    """
    Draw pixels with optional blending and buffering.
    Set blend=False to skip blending and just set the pixel directly.
    Set buffer=False to skip buffering and just call SetPixel directly.
    Returns the final color tuple.
    """
    global pixel_state
    
    # Fail if buffer=False but blend=True (blending requires buffering)
    if not buffer and blend:
        raise ValueError("Cannot blend without buffering. Set buffer=True or blend=False")
    
    if buffer:
        if blend:
            # Get current pixel color from our numpy array
            current_color = pixel_state[y, x]
            
            # Use compiled blending if available
            blended_r, blended_g, blended_b = _fast_pixel_blend(current_color, c1, c2, c3)
            
            # Update our pixel state array
            pixel_state[y, x] = [blended_r, blended_g, blended_b]
            
            # Set the blended pixel on the canvas
            canvas.SetPixel(x, y, blended_r, blended_g, blended_b)
            
            return (blended_r, blended_g, blended_b)
        else:
            # Skip blending, just set the pixel directly
            pixel_state[y, x] = [c1, c2, c3]
            canvas.SetPixel(x, y, c1, c2, c3)
            return (c1, c2, c3)
    else:
        # No buffering, just call SetPixel directly
        canvas.SetPixel(x, y, c1, c2, c3)
        return (c1, c2, c3)

def set_pixel_fast(canvas, x, y, r, g, b):
    """Fast pixel setting without blending - for performance"""
    canvas.SetPixel(x, y, r, g, b)

def clear_pixel_state():
    """Reset the pixel state array to all zeros"""
    global pixel_state
    if pixel_state is not None:
        pixel_state.fill(0)

def draw_vertical_bar (canvas, matrix, colour, x_centre_factor = 0.3, width=4, dim_factor = 0.6, blend=True):
    for y in range (matrix.height):
        x_centre = int(x_centre_factor * matrix.width)
        
        # Check if there's overlap with sine wave
        if pixel_state[y, x_centre].any():  # If pixel has any non-zero values
            # Blend with existing pixel
            draw_pixels(canvas, x_centre, y, colour[0], colour[1], colour[2], blend=blend)
        else:
            # No overlap, just set directly
            draw_pixels(canvas, x_centre, y, colour[0], colour[1], colour[2], blend=blend)
        
        # Soft edges - check for overlap
        soft_colour = colour
        for w in range(1, width):
            soft_colour = _fast_color_dim(soft_colour, dim_factor)
            # Right edge
            if x_centre + w < matrix.width:
                if pixel_state[y, x_centre + w].any():
                    # Blend with existing pixel
                    draw_pixels(canvas, x_centre + w, y, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
                else:
                    # No overlap, just set directly
                    draw_pixels(canvas, x_centre + w, y, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
            
            # Left edge
            if x_centre - w >= 0:
                if pixel_state[y, x_centre - w].any():
                    # Blend with existing pixel
                    draw_pixels(canvas, x_centre - w, y, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
                else:
                    # No overlap, just set directly
                    draw_pixels(canvas, x_centre - w, y, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
    return


def draw_sine_wave(
        canvas,
        matrix,
        t_point,
        colour = (255,255,255),
        amplitude = 9,
        frequency = 0.2,
        width = 4,
        dim_factor= 0.6,
        speed=5,
        phase_offset=0.0,
        blend=True
):
    """
    Draws a solid, oscillating sine wave on the top half of the LED matrix.
    """
    
    # --- Parameters for the sine wave ---
    vertical_offset = matrix.height / 4 + width-2

    phase = (t_point * speed) + phase_offset
 
    # Iterate through every column (x-coordinate)
    for x in range(matrix.width):
        # Calculate the y-coordinate using round() for a smoother curve
        y_center = round(amplitude * math.sin(frequency * x + phase) + vertical_offset)

        # --- Draw a thicker, more solid line ---
        # Center pixel (brightest) - populate the buffer
        if 0 <= y_center < matrix.height:
            draw_pixels(canvas, x, y_center, colour[0], colour[1], colour[2], blend=blend)

        # Soft edges for anti-aliasing - populate the buffer
        soft_colour = colour
        for w in range(1, width):
            soft_colour = _fast_color_dim(soft_colour, dim_factor)
            # Pixel above center (dimmer)
            if y_center - w >= 0:
                draw_pixels(canvas, x, y_center - w, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)
            # Pixel above center (dimmer)
            if y_center + w < matrix.height:
                draw_pixels(canvas, x, y_center + w, soft_colour[0], soft_colour[1], soft_colour[2], blend=blend)


def setup(matrix):
    """
    Initialize module-level pixel buffer so callers can use draw().
    Safe to call multiple times.
    """
    init_pixel_state(matrix.height, matrix.width)


def activate():
    """Hook for demo switching; reset motion integrator for continuity."""
    global _phase_accum, _last_t_point, _external_phase
    _phase_accum = 0.0
    _last_t_point = None
    _external_phase = None


def handle_midi_cc(cc):
    """
    Called by main.py when MIDI CC messages arrive.
    We use the most recent CC value to morph the wave color.
    """
    global _last_cc_value
    try:
        v = int(getattr(cc, "value", 0))
    except Exception:
        return
    if v < 0:
        v = 0
    elif v > 127:
        v = 127
    _last_cc_value = v


def set_color_cc_value(v: int) -> None:
    """Set the color morph amount using a raw CC value (0..127)."""
    global _last_cc_value
    try:
        iv = int(v)
    except Exception:
        return
    if iv < 0:
        iv = 0
    elif iv > 127:
        iv = 127
    _last_cc_value = iv


def set_speed_mult(mult: float) -> None:
    """Set sine-wave speed multiplier (0..2 typical)."""
    global _speed_mult
    try:
        m = float(mult)
    except Exception:
        return
    if m < 0.0:
        m = 0.0
    _speed_mult = m


def set_phase_offset(radians: float) -> None:
    """Set a phase offset in radians (0..2π typical)."""
    global _phase_offset
    try:
        r = float(radians)
    except Exception:
        return
    # Keep it bounded to avoid unbounded growth if someone feeds large values.
    two_pi = 2.0 * math.pi
    if two_pi > 0:
        r = r % two_pi
    _phase_offset = r


def set_external_phase(phase: float | None) -> None:
    """
    Override the internal phase integrator.

    When set, draw() will use this as the wave phase directly (radians-ish domain),
    which is useful for hard-locking visuals to MIDI clock ticks.
    """
    global _external_phase, _last_t_point
    if phase is None:
        _external_phase = None
        # Reset integrator timing so returning to internal integration doesn't jump.
        _last_t_point = None
        return
    try:
        _external_phase = float(phase)
    except Exception:
        return


def set_wavelength_mult(mult: float) -> None:
    """
    Set sine-wave wavelength multiplier.

    Implemented as: effective_frequency = _BASE_FREQUENCY / mult
    Typical mapping is 1.0 at CC=0 and 0.25 at CC=127 (shorter wavelength).
    """
    global _wavelength_mult
    try:
        m = float(mult)
    except Exception:
        return
    # Avoid zero/negative wavelength; keep a small floor.
    if m < 0.01:
        m = 0.01
    _wavelength_mult = m


def _lerp_color(c1, c2, t):
    """Fast-ish integer lerp between two RGB tuples."""
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def draw(canvas, matrix, t_point, colour=None):
    """
    Draw a frame at time t_point (seconds).
    Caller should clear the canvas before calling if desired.
    """
    if pixel_state is None or pixel_state.shape[0] != matrix.height or pixel_state.shape[1] != matrix.width:
        setup(matrix)

    # If caller didn't override colour, compute it from MIDI (0->COLOR_1, 127->COLOR_2).
    if colour is None:
        morph = _last_cc_value / 127.0
        colour = _lerp_color(COLOR_1, COLOR_2, morph)

    clear_pixel_state()
    # Integrate phase over time so changing speed doesn't "teleport" the wave,
    # unless an external phase source (e.g. MIDI clock) is driving it.
    global _phase_accum, _last_t_point, _external_phase
    if _external_phase is None:
        if _last_t_point is None:
            dt = 0.0
        else:
            dt = t_point - _last_t_point
            if dt < 0.0:
                dt = 0.0
            elif dt > _MAX_DT:
                dt = _MAX_DT
        _last_t_point = t_point

        speed = _BASE_SPEED * _speed_mult
        _phase_accum += speed * dt
        phase_for_draw = _phase_accum
    else:
        phase_for_draw = _external_phase
    denom = _wavelength_mult
    if denom < 0.0001:
        denom = 0.0001
    frequency = _BASE_FREQUENCY / denom

    draw_sine_wave(
        canvas,
        matrix,
        phase_for_draw,
        colour=colour,
        frequency=frequency,
        speed=1.0,
        phase_offset=_phase_offset,
        blend=False,
    )
    draw_vertical_bar(canvas, matrix, colour, blend=True)

# --- Main execution block ---
if __name__ == "__main__":
    options = RGBMatrixOptions()
    options.rows = 40
    options.cols = 80
    options.hardware_mapping = 'adafruit-hat'
    options.gpio_slowdown = 2
    options.brightness = 70
    options.pwm_bits = 8
    options.pwm_lsb_nanoseconds = 250
    options.multiplexing = 20    
    # Add this line if you see a lot of "ghosting" or flickering.
    # It's a good default for Adafruit HATs.
    options.disable_hardware_pulsing = True

    matrix = RGBMatrix(options=options)
    canvas = matrix.CreateFrameCanvas()
    
    # Initialize the pixel state array
    init_pixel_state(matrix.height, matrix.width)

    start_time = time.time()

    print("Starting sine wave animation... Press CTRL-C to stop.")
    try:
        while True:
            colour = [50,50,255]
            canvas.Clear()
            clear_pixel_state()  # Reset our pixel state when clearing canvas
            t_point = time.time() - start_time
            draw_sine_wave(canvas, matrix, t_point, colour=colour, frequency=0.15, blend=False)
            draw_vertical_bar(canvas, matrix, colour, blend=True)
            
            # Remove the unnecessary draw_pixels call
            canvas = matrix.SwapOnVSync(canvas)

#            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\nExiting...")
        matrix.Clear()
