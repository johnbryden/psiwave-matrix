#!/usr/bin/env -S python3 -u
"""
Run psiwave-matrix demos on a window (Windows, WSL2, or Linux desktop)
instead of the LED matrix. Same CLI as main.py.
"""

import sys

from main import get_parser, run
from screen_wrapper import ScreenMatrix


def main():
    args = get_parser().parse_args()
    matrix = ScreenMatrix(width=80, height=40, scale=8)
    # On Windows, use Windows MM API so virtual MIDI ports (e.g. loopMIDI) are enumerated.
    use_windows_mm_midi = sys.platform == "win32"
    run(args, matrix, use_windows_mm_midi=use_windows_mm_midi)


if __name__ == "__main__":
    main()
