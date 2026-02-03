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
    Non-blocking MIDI CC input using `python-rtmidi` directly.

    Default behavior is "any": first available MIDI input port, any channel.
    If MIDI isn't available, this becomes a no-op and rendering continues.
    """

    def __init__(self, port_query: Optional[str] = None):
        self._midiin = None

        try:
            import rtmidi  # type: ignore
        except Exception as e:
            print(f"[midi] disabled (could not import python-rtmidi): {e}")
            return

        MidiIn = getattr(rtmidi, "MidiIn", None)
        if MidiIn is None:
            # Common on systems that accidentally installed the wrong `rtmidi` package.
            print(
                "[midi] disabled (rtmidi.MidiIn not found).\n"
                "[midi] Fix on Pi: `sudo apt install python3-rtmidi` or `pip install -U python-rtmidi`.\n"
                "[midi] If you installed a package literally named 'rtmidi', uninstall it (it can shadow python-rtmidi)."
            )
            return

        try:
            self._midiin = MidiIn()
            # We only care about channel voice messages; ignore sysex/timing/active sense.
            if hasattr(self._midiin, "ignore_types"):
                try:
                    # Most common signature (python-rtmidi): active_sense
                    self._midiin.ignore_types(sysex=True, timing=True, active_sense=True)
                except TypeError:
                    # Older builds sometimes use different names or only positional args.
                    try:
                        self._midiin.ignore_types(True, True, True)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[midi] disabled (could not initialize MIDI input): {e}")
            self._midiin = None
            return

        try:
            ports = list(self._midiin.get_ports())
        except Exception as e:
            print(f"[midi] disabled (could not list MIDI input ports): {e}")
            ports = []

        if not ports:
            print("[midi] disabled (no MIDI input ports found)")
            self._midiin = None
            return

        chosen_idx = 0
        if port_query:
            q = port_query.strip().lower()
            for i, n in enumerate(ports):
                if q in n.lower():
                    chosen_idx = i
                    break
            else:
                print(f"[midi] port query '{port_query}' not found; using first input instead")
        else:
            # Heuristic default: avoid the usually-useless "Midi Through" and prefer a real "Midi In".
            def _score_port(name: str) -> int:
                s = name.lower()
                score = 0
                if "midi in" in s:
                    score += 100
                if "through" in s:
                    score -= 100
                else:
                    score += 10
                # Mild preference for typical USB MIDI names.
                if "usb" in s or "controller" in s or "keyboard" in s:
                    score += 5
                return score

            best_i = 0
            best_score = _score_port(ports[0])
            for i in range(1, len(ports)):
                sc = _score_port(ports[i])
                if sc > best_score:
                    best_score = sc
                    best_i = i
            chosen_idx = best_i

        print("[midi] available inputs:")
        for i, n in enumerate(ports):
            marker = " <==" if i == chosen_idx else ""
            print(f"[midi]   {i:2d}: {n}{marker}")

        try:
            self._midiin.open_port(chosen_idx)
        except Exception as e:
            print(f"[midi] disabled (could not open MIDI input port): {e}")
            self._midiin = None
            return

        port_name = ports[chosen_idx]
        print(f"[midi] listening on '{port_name}', any channel (CC only)")

    def drain(self, now_t: float) -> List[MidiCC]:
        """Drain pending CC messages without blocking."""
        if self._midiin is None:
            return []

        out: List[MidiCC] = []
        try:
            # rtmidi polling: returns (message_bytes, delta_time) or None.
            while True:
                msg = self._midiin.get_message()
                if not msg:
                    break
                data = msg[0]
                if not data or len(data) < 3:
                    continue

                status = int(data[0])
                msg_type = status & 0xF0
                ch = (status & 0x0F) + 1  # 1-16

                # Control Change = 0xB0..0xBF
                if msg_type != 0xB0:
                    continue

                out.append(
                    MidiCC(
                        channel=ch,
                        control=int(data[1]) & 0x7F,
                        value=int(data[2]) & 0x7F,
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
    args = ap.parse_args()

    matrix = _build_matrix()
    canvas = matrix.CreateFrameCanvas()

    demos = [
        ("starfield", simple_starfield),
        ("sinwave", sinwave),
    ]

    midi = MidiCCIn(port_query=args.midi_port)

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
                for cc in cc_msgs:
                    print(f"[midi] t={cc.t:7.3f}s ch={cc.channel:2d} cc={cc.control:3d} val={cc.value:3d}")
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

