import time
from rgbmatrix import RGBMatrix, RGBMatrixOptions

# --- Main execution block ---
if __name__ == "__main__":
    # 1. Set up the same options you were using.
    # We need to test if these options are correct for your hardware.
    options = RGBMatrixOptions()
    options.rows = 40
    options.cols = 80
    options.hardware_mapping = 'adafruit-hat'
    options.gpio_slowdown = 4  # For Pi 4/5, try 1 or 2. For Pi 3 and older, 4 is fine.
    options.brightness = 50    # Lowered brightness for testing
    options.pwm_bits = 8
    options.pwm_lsb_nanoseconds = 250
    options.disable_hardware_pulsing = True # Good setting for Adafruit HAT

    # 2. Create the matrix object. If this fails, there's a fundamental library/hardware issue.
    try:
        matrix = RGBMatrix(options=options)
    except Exception as e:
        print("Error initializing matrix. Check your hardware connections and options.")
        print(e)
        exit(1)

    # Create a canvas to draw on
    canvas = matrix.CreateFrameCanvas()

    try:
        print("--- Starting Diagnostic Test ---")

        # --- TEST 1: Fill screen with a solid color ---
        print("1. Filling screen with dim red for 3 seconds...")
        canvas.Fill(50, 0, 0)
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(3)
        
        # --- TEST 2: Draw a simple cross ---
        print("2. Drawing a white cross in the center for 3 seconds...")
        canvas.Clear()
        # Horizontal line across the middle (y=19 for a 40px tall panel)
        for x in range(matrix.width):
            canvas.SetPixel(x, 19, 255, 255, 255)
        # Vertical line down the middle (x=39 for an 80px wide panel)
        for y in range(matrix.height):
            canvas.SetPixel(39, y, 255, 255, 255)
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(3)

        # --- TEST 3: Draw corner pixels ---
        print("3. Adding corner pixels. Display will hold here. Press CTRL-C to exit.")
        # Top-Left: Red
        canvas.SetPixel(0, 0, 255, 0, 0)
        # Top-Right: Green
        canvas.SetPixel(matrix.width - 1, 0, 0, 255, 0)
        # Bottom-Left: Blue
        canvas.SetPixel(0, matrix.height - 1, 0, 0, 255)
        # Bottom-Right: Yellow
        canvas.SetPixel(matrix.width - 1, matrix.height - 1, 255, 255, 0)
        canvas = matrix.SwapOnVSync(canvas)
        
        # Keep script alive to display the final result
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        # Always clear the matrix when the script is finished.
        matrix.Clear()