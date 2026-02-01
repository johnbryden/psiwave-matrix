#!/usr/bin/env -S python3 -u

import time
import math
import numpy as np
from rgbmatrix import RGBMatrix, RGBMatrixOptions

# Try to import numba for JIT compilation, fall back gracefully if not available
try:
    from numba import jit, njit
    NUMBA_AVAILABLE = True
    print("Numba available - compiling for speed!")
except ImportError:
    NUMBA_AVAILABLE = False
    print("Numba not available - running in interpreted mode")

# Global pixel state as a numpy array for fast access
# Shape: (height, width, 3) for RGB values
pixel_state = None

def init_pixel_state(height, width):
    """Initialize the pixel state array"""
    global pixel_state
    pixel_state = np.zeros((height, width, 3), dtype=np.uint8)

# Compile the core pixel manipulation function if numba is available
if NUMBA_AVAILABLE:
    @njit(cache=True)
    def _fast_pixel_blend(current_color, c1, c2, c3):
        """Fast compiled pixel blending"""
        blended_r = max(current_color[0], c1)
        blended_g = max(current_color[1], c2)
        blended_b = max(current_color[2], c3)
        return blended_r, blended_g, blended_b
    
    @njit(cache=True)
    def _fast_color_dim(color, dim_factor):
        """Fast compiled color dimming"""
        return int(color[0] * dim_factor), int(color[1] * dim_factor), int(color[2] * dim_factor)
else:
    def _fast_pixel_blend(current_color, c1, c2, c3):
        """Fallback pixel blending"""
        blended_r = max(current_color[0], c1)
        blended_g = max(current_color[1], c2)
        blended_b = max(current_color[2], c3)
        return blended_r, blended_g, blended_b
    
    def _fast_color_dim(color, dim_factor):
        """Fallback color dimming"""
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
        blend=True
):
    """
    Draws a solid, oscillating sine wave on the top half of the LED matrix.
    """
    
    # --- Parameters for the sine wave ---
    vertical_offset = matrix.height / 4 + width-2

    phase = t_point * speed
 
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
