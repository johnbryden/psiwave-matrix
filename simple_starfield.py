#!/usr/bin/env -S python3 -u

import time
import math
import numpy as np
import random
from rgbmatrix import RGBMatrix, RGBMatrixOptions

# Global pixel state as a numpy array for fast access
# Shape: (height, width, 3) for RGB values
pixel_state = None

# Optional module-level state so other code can call draw(canvas, matrix, t_point)
_stars = None
_last_t_point = None
_is_setup = False

def init_pixel_state(height, width):
    """Initialize the pixel state array"""
    global pixel_state
    pixel_state = np.zeros((height, width, 3), dtype=np.uint8)

def merge_pixels(canvas, x, y, c1, c2, c3, blend=True):
    """
    Blend the new pixel color with existing color by taking the maximum value of each RGB component.
    Set blend=False to skip blending and just set the pixel directly.
    Returns the blended color tuple.
    """
    global pixel_state
    
    if blend:
        # Get current pixel color from our numpy array
        current_color = pixel_state[y, x]
        
        # Blend by taking the maximum of each RGB component
        blended_r = max(current_color[0], c1)
        blended_g = max(current_color[1], c2)
        blended_b = max(current_color[2], c3)
        
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

def clear_pixel_state():
    """Reset the pixel state array to all zeros"""
    global pixel_state
    if pixel_state is not None:
        pixel_state.fill(0)

class Star:
    def __init__(self, matrix_width, matrix_height):
        # Start stars near center
        center_x = matrix_width / 2
        center_y = matrix_height / 2
        self.x = center_x + random.uniform(-15, 15)
        self.y = center_y + random.uniform(-12, 12)
        self.brightness = random.randint(50, 255)
        self.speed = random.uniform(1.5, 4.0)  # Increased speeds for faster movement
        self.twinkle_speed = random.uniform(0.02, 0.08)
        self.twinkle_phase = random.uniform(0, 2 * math.pi)
        
        # Add color variety to stars
        self.color_type = random.choice(['white', 'blue', 'cyan', 'yellow', 'orange', 'red'])
        
    def update(self, dt, matrix_width, matrix_height):
        """Update star position and twinkling - optimized for speed"""
        # Move star outward from center (simulating movement toward viewer)
        center_x = matrix_width / 2
        center_y = matrix_height / 2
        
        # Calculate direction from center
        dx = self.x - center_x
        dy = self.y - center_y
        
        # Move outward from center - simplified math for speed
        if abs(dx) > 0.1 or abs(dy) > 0.1:  # Avoid division by zero
            # Use faster approximation instead of sqrt
            length = abs(dx) + abs(dy)  # Manhattan distance approximation
            if length > 0:
                # Smoother movement with velocity-based updates
                self.x += (dx / length) * self.speed * dt * 2.5  # Increased speed
                self.y += (dy / length) * self.speed * dt * 2.5
        
        # Reset star if it goes off screen
        if (self.x < 0 or self.x >= matrix_width or 
            self.y < 0 or self.y >= matrix_height):
            # Place star randomly near center
            self.x = center_x + random.uniform(-15, 15)
            self.y = center_y + random.uniform(-12, 12)
            
        # Update twinkling less frequently for speed
        self.twinkle_phase += self.twinkle_speed * 0.5
        
    def get_color(self):
        """Get current star color with twinkling effect"""
        twinkle = 0.5 + 0.5 * math.sin(self.twinkle_phase)
        brightness = int(self.brightness * twinkle)
        
        # Apply color based on star type
        if self.color_type == 'white':
            return (brightness, brightness, brightness)
        elif self.color_type == 'blue':
            return (brightness//3, brightness//3, brightness)
        elif self.color_type == 'cyan':
            return (brightness//3, brightness, brightness)
        elif self.color_type == 'yellow':
            return (brightness, brightness, brightness//3)
        elif self.color_type == 'orange':
            return (brightness, brightness//2, brightness//6)
        elif self.color_type == 'red':
            return (brightness, brightness//6, brightness//6)
        else:
            return (brightness, brightness, brightness)  # fallback to white
    


def create_starfield(num_stars, matrix_width, matrix_height):
    """Create a collection of stars"""
    return [Star(matrix_width, matrix_height) for _ in range(num_stars)]

def update_starfield(stars, dt, matrix_width, matrix_height):
    """Update all stars"""
    for star in stars:
        star.update(dt, matrix_width, matrix_height)

def draw_starfield(canvas, stars):
    """Draw all stars with optimized rendering and motion blur"""
    # Pre-calculate colors for all stars to avoid repeated calculations
    colors = []
    for star in stars:
        if 0 <= star.y < canvas.height and 0 <= star.x < canvas.width:
            colors.append((int(star.x), int(star.y), star.get_color()))
    
    # Batch draw all visible stars with motion blur
    for x, y, color in colors:
        # Main star
        merge_pixels(canvas, x, y, color[0], color[1], color[2], blend=False)


def setup(matrix, num_stars=100):
    """
    Initialize module-level starfield state so callers can use draw().
    Safe to call multiple times.
    """
    global _stars, _last_t_point, _is_setup
    init_pixel_state(matrix.height, matrix.width)
    _stars = create_starfield(num_stars, matrix.width, matrix.height)
    _last_t_point = None
    _is_setup = True


def activate():
    """Called when switching back to this demo (prevents a large dt jump)."""
    global _last_t_point
    _last_t_point = None


def draw(canvas, matrix, t_point):
    """
    Draw a frame of the starfield at time t_point (seconds).
    Caller should clear the canvas before calling if desired.
    """
    global _stars, _last_t_point, _is_setup

    if not _is_setup or _stars is None:
        setup(matrix)

    if _last_t_point is None:
        dt = 0.0
    else:
        dt = max(0.0, t_point - _last_t_point)
    _last_t_point = t_point

    clear_pixel_state()
    update_starfield(_stars, dt, matrix.width, matrix.height)
    draw_starfield(canvas, _stars)

# --- Main execution block ---
if __name__ == "__main__":
    options = RGBMatrixOptions()
    options.rows = 40
    options.cols = 80
    options.hardware_mapping = 'adafruit-hat'
    options.gpio_slowdown = 1  # Fastest possible
    options.brightness = 50     # Lower brightness for better performance
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
    
    # Create starfield
    num_stars = 100  # Adjust this for more/fewer stars
    stars = create_starfield(num_stars, matrix.width, matrix.height)

    start_time = time.time()
    last_time = start_time

    print("Starting starfield animation... Press CTRL-C to stop.")
    try:
        while True:
            current_time = time.time()
            dt = current_time - last_time
            last_time = current_time
            
            # Clear canvas and pixel state
            canvas.Clear()
            clear_pixel_state()
            
            # Update and draw stars
            update_starfield(stars, dt, matrix.width, matrix.height)
            draw_starfield(canvas, stars)
            
            # Swap buffers
            canvas = matrix.SwapOnVSync(canvas)
            
            # Adaptive frame timing for smooth animation
            target_fps = 60
            frame_time = 1.0 / target_fps
            elapsed = time.time() - current_time
            if elapsed < frame_time:
                time.sleep(frame_time - elapsed)

    except KeyboardInterrupt:
        print("\nExiting...")
        matrix.Clear()
