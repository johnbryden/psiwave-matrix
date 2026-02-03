#!/usr/bin/env -S python3 -u

import time
import argparse
from dataclasses import dataclass
from typing import Optional, List
from rgbmatrix import RGBMatrix, RGBMatrixOptions

import sinwave
import simple_starfield


SWITCH_SECONDS = 30.0
TARGET_FPS = 60.0

# MIDI CC mapping (controller number -> function)
# 1: Speed of wave (0..2x current)
# 2: Colour of wave (existing morph)
# 3: Phase of wave
# 4: Speed of starfield (0.5..4x current)
# 5: Colour of starfield (white -> coloured)
CC_WAVE_SPEED = 43
CC_WAVE_COLOR = 44
CC_WAVE_PHASE = 16
CC_STARFIELD_SPEED = 17
CC_STARFIELD_COLOR = 25


def _cc_unit(v: int) -> float:
    """Map CC value (0..127) to 0..1."""
    if v <= 0:
        return 0.0
    if v >= 127:
        return 1.0
    return v / 127.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


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
    ap.add_argument(
        "--midi-log",
        choices=("mapped", "all", "none"),
        default="none",
        help="MIDI logging mode. 'none' (default) disables turn-by-turn logs; 'mapped' logs only mapped CCs; 'all' logs all CCs.",
    )
    ap.add_argument("--cc-wave-speed", type=int, default=CC_WAVE_SPEED, help="CC number for wave speed.")
    ap.add_argument("--cc-wave-color", type=int, default=CC_WAVE_COLOR, help="CC number for wave colour.")
    ap.add_argument("--cc-wave-phase", type=int, default=CC_WAVE_PHASE, help="CC number for wave phase.")
    ap.add_argument("--cc-starfield-speed", type=int, default=CC_STARFIELD_SPEED, help="CC number for starfield speed.")
    ap.add_argument("--cc-starfield-color", type=int, default=CC_STARFIELD_COLOR, help="CC number for starfield colour.")
    args = ap.parse_args()

    matrix = _build_matrix()
    canvas = matrix.CreateFrameCanvas()

    demos = [
        ("starfield", simple_starfield),
        ("sinwave", sinwave),
    ]

    midi = MidiCCIn(port_query=args.midi_port)

    def _clamp_cc(n: int) -> int:
        if n < 0:
            return 0
        if n > 127:
            return 127
        return n

    cc_wave_speed = _clamp_cc(args.cc_wave_speed)
    cc_wave_color = _clamp_cc(args.cc_wave_color)
    cc_wave_phase = _clamp_cc(args.cc_wave_phase)
    cc_starfield_speed = _clamp_cc(args.cc_starfield_speed)
    cc_starfield_color = _clamp_cc(args.cc_starfield_color)

    print(
        "[midi] CC map:"
        f" wave_speed={cc_wave_speed}"
        f" wave_color={cc_wave_color}"
        f" wave_phase={cc_wave_phase}"
        f" starfield_speed={cc_starfield_speed}"
        f" starfield_color={cc_starfield_color}"
        f" (log={args.midi_log})"
    )

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

            # Drain MIDI CC messages and route mapped controls.
            cc_msgs = midi.drain(now_t=t_point)
            if cc_msgs:
                mapped_controls = {
                    cc_wave_speed,
                    cc_wave_color,
                    cc_wave_phase,
                    cc_starfield_speed,
                    cc_starfield_color,
                }
                mapped_msgs = [cc for cc in cc_msgs if cc.control in mapped_controls]
                if args.midi_log == "all":
                    for cc in cc_msgs:
                        tag = "mapped" if cc.control in mapped_controls else "unmapped"
                        print(
                            f"[midi] {tag} t={cc.t:7.3f}s ch={cc.channel:2d} cc={cc.control:3d} val={cc.value:3d}"
                        )

                if mapped_msgs and args.midi_log == "mapped":
                    unique_controls = sorted({cc.control for cc in mapped_msgs})
                    print(
                        f"[midi] mapped CC detected ({len(mapped_msgs)} msg{'s' if len(mapped_msgs) != 1 else ''}) "
                        f"controls={unique_controls}"
                    )
                    for cc in mapped_msgs:
                        print(f"[midi] t={cc.t:7.3f}s ch={cc.channel:2d} cc={cc.control:3d} val={cc.value:3d}")

                for cc in mapped_msgs:
                    if cc.control == cc_wave_color:
                        # Prefer a dedicated setter so the CC number can be remapped freely.
                        setter = getattr(sinwave, "set_color_cc_value", None)
                        if setter is not None:
                            setter(cc.value)
                            if args.midi_log == "mapped":
                                print(f"[midi] wave color -> {_cc_unit(cc.value):.3f}")
                        else:
                            handler = getattr(sinwave, "handle_midi_cc", None)
                            if handler is not None:
                                handler(cc)
                                if args.midi_log == "mapped":
                                    print(f"[midi] wave color -> {_cc_unit(cc.value):.3f}")
                    elif cc.control == cc_wave_speed:
                        # 0..2x
                        mult = _lerp(0.0, 2.0, _cc_unit(cc.value))
                        setter = getattr(sinwave, "set_speed_mult", None)
                        if setter is not None:
                            setter(mult)
                            if args.midi_log == "mapped":
                                print(f"[midi] wave speed -> {mult:.3f}x")
                        else:
                            # Back-compat: allow older sinwave modules by mutating module state.
                            if hasattr(sinwave, "_speed_mult"):
                                try:
                                    setattr(sinwave, "_speed_mult", float(mult))
                                    if args.midi_log == "mapped":
                                        print(f"[midi] wave speed -> {mult:.3f}x (compat)")
                                except Exception:
                                    pass
                    elif cc.control == cc_wave_phase:
                        # 0..2π
                        import math
                        radians = _lerp(0.0, 2.0 * math.pi, _cc_unit(cc.value))
                        setter = getattr(sinwave, "set_phase_offset", None)
                        if setter is not None:
                            setter(radians)
                            if args.midi_log == "mapped":
                                print(f"[midi] wave phase -> {radians:.3f} rad")
                        else:
                            if hasattr(sinwave, "_phase_offset"):
                                try:
                                    setattr(sinwave, "_phase_offset", float(radians))
                                    if args.midi_log == "mapped":
                                        print(f"[midi] wave phase -> {radians:.3f} rad (compat)")
                                except Exception:
                                    pass
                    elif cc.control == cc_starfield_speed:
                        # 0.5..4x
                        mult = _lerp(0.5, 4.0, _cc_unit(cc.value))
                        setter = getattr(simple_starfield, "set_speed_mult", None)
                        if setter is not None:
                            setter(mult)
                            if args.midi_log == "mapped":
                                print(f"[midi] starfield speed -> {mult:.3f}x")
                        else:
                            if hasattr(simple_starfield, "_speed_mult"):
                                try:
                                    setattr(simple_starfield, "_speed_mult", float(mult))
                                    if args.midi_log == "mapped":
                                        print(f"[midi] starfield speed -> {mult:.3f}x (compat)")
                                except Exception:
                                    pass
                    elif cc.control == cc_starfield_color:
                        # 0..1 (white -> colored)
                        amt = _cc_unit(cc.value)
                        setter = getattr(simple_starfield, "set_color_amount", None)
                        if setter is not None:
                            setter(amt)
                            if args.midi_log == "mapped":
                                print(f"[midi] starfield color -> {amt:.3f}")
                        else:
                            if hasattr(simple_starfield, "_color_amount"):
                                try:
                                    setattr(simple_starfield, "_color_amount", float(amt))
                                    if args.midi_log == "mapped":
                                        print(f"[midi] starfield color -> {amt:.3f} (compat)")
                                except Exception:
                                    pass

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

