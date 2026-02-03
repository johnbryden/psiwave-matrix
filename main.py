#!/usr/bin/env -S python3 -u

import time
from rgbmatrix import RGBMatrix, RGBMatrixOptions

import sinwave
import simple_starfield


SWITCH_SECONDS = 10.0
TARGET_FPS = 60.0


def _build_matrix():
    # One shared matrix config for both demos.
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
    return RGBMatrix(options=options)


def main():
    matrix = _build_matrix()
    canvas = matrix.CreateFrameCanvas()

    demos = [
        ("starfield", simple_starfield),
        ("sinwave", sinwave),
    ]

    # Let each demo initialize any internal buffers/state.
    for _, demo in demos:
        if hasattr(demo, "setup"):
            demo.setup(matrix)

    start_time = time.time()
    active_idx = 0
    demos[active_idx][1].activate()
    print(f"Starting demo: {demos[active_idx][0]} (switch every {SWITCH_SECONDS:.0f}s). Press CTRL-C to stop.")

    try:
        while True:
            frame_start = time.time()
            t_point = frame_start - start_time

            next_idx = int(t_point // SWITCH_SECONDS) % len(demos)
            if next_idx != active_idx:
                active_idx = next_idx
                demos[active_idx][1].activate()
                print(f"Switched to: {demos[active_idx][0]}")

            canvas.Clear()
            demos[active_idx][1].draw(canvas, matrix, t_point)
            canvas = matrix.SwapOnVSync(canvas)

            frame_budget = 1.0 / TARGET_FPS
            elapsed = time.time() - frame_start
            if elapsed < frame_budget:
                time.sleep(frame_budget - elapsed)

    except KeyboardInterrupt:
        print("\nExiting...")
        matrix.Clear()


if __name__ == "__main__":
    main()

