#!/usr/bin/env -S python3 -u

import time
import argparse
from dataclasses import dataclass
from typing import Optional, List
from rgbmatrix import RGBMatrix, RGBMatrixOptions

import sinwave
import simple_starfield


SWITCH_SECONDS = 10.0
TARGET_FPS = 60.0


@dataclass(frozen=True, slots=True)
class MidiCC:
    """Minimal representation of a MIDI Control Change message."""
    channel: int  # 1-16
    control: int  # 0-127
    value: int    # 0-127
    t: float      # seconds (relative to program start)


class MidiCCIn:
    """
    Non-blocking MIDI CC input using `mido` if available.

    Default behavior is "any": first available MIDI input port, any channel.
    If MIDI isn't available, this becomes a no-op and rendering continues.
    """

    def __init__(self, port_query: Optional[str] = None, channel: Optional[int] = None):
        self._channel = channel  # 1-16 or None
        self._port = None

        try:
            import mido  # type: ignore
        except Exception as e:
            print(f"[midi] disabled (could not import mido): {e}")
            return

        try:
            names = list(mido.get_input_names())
        except Exception as e:
            print(f"[midi] disabled (could not list MIDI inputs): {e}")
            return

        if not names:
            print("[midi] disabled (no MIDI input ports found)")
            return

        chosen = None
        if port_query:
            q = port_query.strip().lower()
            for n in names:
                if q in n.lower():
                    chosen = n
                    break
            if chosen is None:
                print(f"[midi] port query '{port_query}' not found; using default input instead")

        try:
            self._port = mido.open_input(chosen) if chosen else mido.open_input()
        except Exception as e:
            print(f"[midi] disabled (could not open MIDI input): {e}")
            self._port = None
            return

        port_name = getattr(self._port, "name", chosen or "(default)")
        ch_txt = "any" if channel is None else str(channel)
        print(f"[midi] listening on '{port_name}', channel {ch_txt} (CC only)")

    def drain(self, now_t: float) -> List[MidiCC]:
        """Drain pending CC messages without blocking."""
        if self._port is None:
            return []

        out: List[MidiCC] = []
        try:
            for msg in self._port.iter_pending():
                if getattr(msg, "type", None) != "control_change":
                    continue
                ch = int(getattr(msg, "channel", 0)) + 1  # mido uses 0-15
                if self._channel is not None and ch != self._channel:
                    continue
                out.append(
                    MidiCC(
                        channel=ch,
                        control=int(getattr(msg, "control", 0)),
                        value=int(getattr(msg, "value", 0)),
                        t=now_t,
                    )
                )
        except Exception:
            # Don't take down the render loop if the backend hiccups.
            return out
        return out


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
    ap = argparse.ArgumentParser(description="psiwave-matrix demos")
    ap.add_argument("--midi-port", default=None, help="MIDI input port name (substring match). Default: any.")
    ap.add_argument("--midi-channel", type=int, default=None, help="MIDI channel 1-16. Default: any.")
    args = ap.parse_args()
    if args.midi_channel is not None and not (1 <= args.midi_channel <= 16):
        raise SystemExit("--midi-channel must be 1-16 (or omit for any)")

    matrix = _build_matrix()
    canvas = matrix.CreateFrameCanvas()

    demos = [
        ("starfield", simple_starfield),
        ("sinwave", sinwave),
    ]

    midi = MidiCCIn(port_query=args.midi_port, channel=args.midi_channel)

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

            # Drain MIDI CC messages and forward them to both demos (active or not).
            cc_msgs = midi.drain(now_t=t_point)
            if cc_msgs:
                for _, demo in demos:
                    handler = getattr(demo, "handle_midi_cc", None)
                    if handler is None:
                        continue
                    for cc in cc_msgs:
                        handler(cc)

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

