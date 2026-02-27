#!/usr/bin/env -S python3 -u

import sys
import time
import math
import random
import argparse
from dataclasses import dataclass
from typing import Optional, List, Tuple

import sinwave
import simple_starfield
import multi_sinwaves
import text_scroll


SWITCH_SECONDS = 30.0
TARGET_FPS = 60.0

# MIDI CC mapping (controller number -> function)
# 1: Speed of wave (0..2x current)
# 2: Colour of wave (existing morph)
# 3: Phase of wave
# 4: Wavelength of wave (1.0x .. 0.25x base)
# 4: Speed of starfield (0.5..4x current)
# 5: Colour of starfield (white -> coloured)
CC_WAVE_SPEED = -1
CC_WAVE_WAVELENGTH = 102
CC_WAVE_COLOR = 108
CC_WAVE_PHASE = 104
CC_STARFIELD_SPEED = 101
CC_STARFIELD_COLOR = 102
CC_TEXT_SPEED = 101
CC_TEXT_COLOR = 102

# Beat-synced starfield spawn palette (kept consistent with simple_starfield.py).
_STARFIELD_SPAWN_PALETTE = ("white", "blue", "cyan", "yellow", "orange", "red")


def _cc_unit(v: int) -> float:
    """Map CC value (0..127) to 0..1."""
    if v <= 0:
        return 0.0
    if v >= 127:
        return 1.0
    return v / 127.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp01(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return float(x)


def _sigmoid01(x: float, *, threshold: float = 0.5, steepness: float = 10.0) -> float:
    """
    Sigmoid curve mapping x∈[0,1] -> y∈[0,1].

    - threshold: the input value where the curve crosses 0.5 (before endpoint forcing)
    - steepness: curve sharpness; <=0 falls back to linear

    Note: endpoints are forced exactly: x=0 -> 0, x=1 -> 1.
    """
    x = _clamp01(x)
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    t = _clamp01(threshold)
    k = float(steepness)
    if not (k > 0.0):
        return x

    # Numerically-stable logistic.
    z = k * (x - t)
    if z >= 0.0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


@dataclass(frozen=True)
class MidiCC:
    """Minimal representation of a MIDI Control Change message."""
    channel: int  # 1-16
    control: int  # 0-127
    value: int    # 0-127
    t: float      # seconds (relative to program start)


@dataclass(frozen=True)
class MidiNote:
    """Minimal representation of a MIDI NoteOn/NoteOff message."""
    channel: int    # 1-16
    note: int       # 0-127
    velocity: int   # 0-127
    is_on: bool     # True for NoteOn (vel>0), False for NoteOff (or NoteOn vel=0)
    t: float        # seconds (relative to program start)


class MidiCCIn:
    """
    Non-blocking MIDI input using `python-rtmidi` directly.

    Default behavior is "any": first available MIDI input port, any channel.
    If MIDI isn't available, this becomes a no-op and rendering continues.
    """

    def __init__(self, port_query: Optional[str] = None, use_windows_mm: bool = False):
        self._midiin = None
        # MIDI clock tracking (24 PPQN standard).
        self._clock_ppqn = 24
        self._clock_running = False
        self._clock_last_tick_t: Optional[float] = None
        self._clock_tick_dts: List[float] = []
        self._clock_bpm: Optional[float] = None
        self._clock_start_pulse = False
        self._clock_tick_count = 0
        self._clock_last_dt: Optional[float] = None
        # Note event queue (drained by caller).
        self._note_queue: List[MidiNote] = []

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

        # When use_windows_mm is True (e.g. from main_screen on Windows), force Windows MM API
        # so virtual ports like loopMIDI are enumerated.
        midi_kwargs = {}
        if use_windows_mm:
            api = getattr(rtmidi, "API_WINDOWS_MM", None)
            get_apis = getattr(rtmidi, "get_compiled_api", None)
            if api is not None and get_apis is not None and callable(get_apis) and api in get_apis():
                midi_kwargs["rtapi"] = api

        try:
            self._midiin = MidiIn(**midi_kwargs) if midi_kwargs else MidiIn()
            if midi_kwargs:
                print("[midi] using Windows MM API for port enumeration (e.g. loopMIDI)")
            # We care about CC *and* MIDI clock; ignore sysex + active sense.
            if hasattr(self._midiin, "ignore_types"):
                try:
                    # Most common signature (python-rtmidi): active_sense
                    # IMPORTANT: timing=False so we receive MIDI clock (0xF8) + start/stop/continue.
                    self._midiin.ignore_types(sysex=True, timing=False, active_sense=True)
                except TypeError:
                    # Older builds sometimes use different names or only positional args.
                    try:
                        # Positional order is typically: sysex, timing, active_sense.
                        self._midiin.ignore_types(True, False, True)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[midi] disabled (could not initialize MIDI input): {e}")
            self._midiin = None
            return

        # Virtual drivers (e.g. loopMIDI) can register slightly late; retry a few times on Windows.
        ports = []
        last_error = None
        for attempt in range(4):
            try:
                ports = list(self._midiin.get_ports())
                if ports:
                    break
            except Exception as e:
                last_error = e
            if not ports and attempt < 3:
                time.sleep(0.6 if attempt < 2 else 1.0)

        if last_error is not None and not ports:
            print(f"[midi] disabled (could not list MIDI input ports): {last_error}")
            ports = []

        if not ports:
            print("[midi] disabled (no MIDI input ports found)")
            if sys.platform == "win32":
                apis = getattr(rtmidi, "get_compiled_api", None)
                if callable(apis):
                    print(f"[midi] compiled APIs: {apis()}")
                print("[midi] Ensure loopMIDI (or your device) is running and ports exist. Use main_screen.py on Windows for virtual port support.")
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
        print(f"[midi] listening on '{port_name}', any channel (CC + clock)")

    def _clock_on_start(self) -> None:
        self._clock_running = True
        self._clock_last_tick_t = None
        self._clock_tick_dts.clear()
        self._clock_bpm = None
        self._clock_start_pulse = True
        self._clock_tick_count = 0
        self._clock_last_dt = None

    def _clock_on_stop(self) -> None:
        self._clock_running = False
        self._clock_last_tick_t = None
        self._clock_tick_dts.clear()
        self._clock_bpm = None
        self._clock_tick_count = 0
        self._clock_last_dt = None

    def _clock_on_tick(self, now_t: float) -> None:
        # Some devices send clock without Start/Continue. Treat first observed clock as "running".
        if not self._clock_running:
            self._clock_running = True
        self._clock_tick_count += 1
        if self._clock_last_tick_t is not None:
            dt = now_t - self._clock_last_tick_t
            self._clock_last_dt = dt
            # MIDI clock at ~20..300 BPM is tick dt ~= 0.125s .. 0.0083s.
            # Filter obvious garbage/spikes.
            if 0.002 <= dt <= 0.25:
                self._clock_tick_dts.append(dt)
                # Keep a small rolling window for stability.
                if len(self._clock_tick_dts) > 96:
                    del self._clock_tick_dts[:-96]
                # Start estimating quickly, then stabilize with more samples.
                if len(self._clock_tick_dts) >= 4:
                    avg_dt = sum(self._clock_tick_dts) / len(self._clock_tick_dts)
                    if avg_dt > 0:
                        bps = 1.0 / (avg_dt * float(self._clock_ppqn))
                        self._clock_bpm = 60.0 * bps
        self._clock_last_tick_t = now_t

    def clock_state(self) -> Tuple[bool, Optional[float], bool]:
        """
        Returns (running, bpm, start_pulse).

        start_pulse is True exactly once right after receiving MIDI Start (0xFA).
        """
        sp = self._clock_start_pulse
        self._clock_start_pulse = False
        return self._clock_running, self._clock_bpm, sp

    def clock_debug_state(self) -> Tuple[bool, Optional[float], int, Optional[float], int]:
        """Returns (running, bpm, tick_count, last_dt, window_len)."""
        return self._clock_running, self._clock_bpm, int(self._clock_tick_count), self._clock_last_dt, len(self._clock_tick_dts)

    def drain(self, now_t: float) -> List[MidiCC]:
        """Drain pending MIDI messages without blocking; returns CCs and updates clock state."""
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
                if not data or len(data) < 1:
                    continue

                status = int(data[0]) & 0xFF

                # --- System real-time messages (single-byte, can occur anywhere) ---
                if status == 0xF8:  # MIDI Clock
                    self._clock_on_tick(now_t=now_t)
                    continue
                if status == 0xFA:  # Start
                    self._clock_on_start()
                    continue
                if status == 0xFB:  # Continue
                    self._clock_running = True
                    continue
                if status == 0xFC:  # Stop
                    self._clock_on_stop()
                    continue
                if status >= 0xF8:
                    # Active sense (0xFE) and other real-time: ignore.
                    continue

                # --- Channel voice messages (need at least 3 bytes) ---
                if len(data) < 3:
                    continue
                msg_type = status & 0xF0
                ch = (status & 0x0F) + 1  # 1-16

                # NoteOn/NoteOff
                # - NoteOn: 0x90..0x9F (velocity=0 is treated as NoteOff)
                # - NoteOff: 0x80..0x8F
                if msg_type == 0x90 or msg_type == 0x80:
                    note = int(data[1]) & 0x7F
                    vel = int(data[2]) & 0x7F
                    is_on = (msg_type == 0x90) and (vel > 0)
                    self._note_queue.append(
                        MidiNote(
                            channel=ch,
                            note=note,
                            velocity=vel,
                            is_on=is_on,
                            t=now_t,
                        )
                    )

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

    def drain_notes(self) -> List[MidiNote]:
        """
        Drain queued MIDI note events (NoteOn/NoteOff) without blocking.

        This is separate from `drain()` so existing CC call sites remain unchanged.
        """
        if not self._note_queue:
            return []
        out = self._note_queue
        self._note_queue = []
        return out

    def is_enabled(self) -> bool:
        return self._midiin is not None


def _build_matrix():
    from rgbmatrix import RGBMatrix, RGBMatrixOptions

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


def get_parser():
    ap = argparse.ArgumentParser(description="psiwave-matrix demos")
    demo_group = ap.add_mutually_exclusive_group()
    demo_group.add_argument(
        "--solo-starfield",
        action="store_true",
        help="Run only the starfield demo.",
    )
    demo_group.add_argument(
        "--solo-sinwave",
        action="store_true",
        help="Run only the sinwave demo.",
    )
    demo_group.add_argument(
        "--solo-multi-sinwaves",
        action="store_true",
        help="Run only the multi-sinwaves demo (12 stacked layers, MIDI note highlight).",
    )
    demo_group.add_argument(
        "--solo-text-scroll",
        action="store_true",
        help="Run only the text scroll demo (scrolling text, CC for speed/colour).",
    )
    ap.add_argument("--midi-port", default=None, help="MIDI input port name (substring match). Default: any.")
    ap.add_argument(
        "--midi-sync",
        choices=("off", "wavelength", "speed", "spatial", "both"),
        default="off",
        help=(
            "Sync wave parameters to MIDI clock. "
            "'speed' beat-locks animation phase to clock; "
            "'wavelength' is an alias for 'speed' (does NOT change spatial wavelength); "
            "'spatial' maps BPM to spatial wavelength multiplier; "
            "'both' applies speed+spatial; 'off' disables."
        ),
    )
    ap.add_argument("--midi-sync-ref-bpm", type=float, default=120.0, help="Reference BPM for wavelength mapping (ref/bpm).")
    ap.add_argument("--midi-sync-wavelength-min", type=float, default=0.25, help="Min wavelength multiplier when syncing.")
    ap.add_argument("--midi-sync-wavelength-max", type=float, default=2.00, help="Max wavelength multiplier when syncing.")
    ap.add_argument(
        "--midi-sync-beats-per-cycle",
        type=float,
        default=2.0,
        help="For speed sync: beats per full 2π cycle (default 2 = half-speed; 1=one beat per cycle, 4=one bar in 4/4).",
    )
    ap.add_argument(
        "--midi-sync-log",
        choices=("none", "bpm", "clock"),
        default="none",
        help="Log MIDI clock status occasionally (useful while experimenting).",
    )
    ap.add_argument(
        "--wave-speed-cc-mapping",
        choices=("auto", "on", "off"),
        default="auto",
        help=(
            "Wave-speed CC mapping mode. "
            "'auto' (default) disables wave-speed CC while --midi-sync is 'speed' or 'both'; "
            "'on' forces it on; 'off' forces it off."
        ),
    )
    ap.add_argument(
        "--midi-log",
        choices=("mapped", "all", "both", "none"),
        default="none",
        help=(
            "MIDI logging mode. 'none' (default) disables turn-by-turn logs; "
            "'mapped' logs only mapped CCs (+ derived parameter changes); "
            "'all' logs all CCs (mapped+unmapped); "
            "'both' logs all CCs AND the mapped summaries/derived changes."
        ),
    )
    ap.add_argument(
        "--midi-note-log",
        choices=("none", "all"),
        default="none",
        help=(
            "MIDI note logging mode. 'none' (default) disables note logs; "
            "'all' logs every NoteOn/NoteOff received."
        ),
    )
    ap.add_argument("--cc-wave-speed", type=int, default=CC_WAVE_SPEED, help="CC number for wave speed.")
    ap.add_argument("--cc-wave-wavelength", type=int, default=CC_WAVE_WAVELENGTH, help="CC number for wave wavelength.")
    ap.add_argument("--cc-wave-color", type=int, default=CC_WAVE_COLOR, help="CC number for wave colour.")
    ap.add_argument("--cc-wave-phase", type=int, default=CC_WAVE_PHASE, help="CC number for wave phase.")
    ap.add_argument("--cc-starfield-speed", type=int, default=CC_STARFIELD_SPEED, help="CC number for starfield speed.")
    ap.add_argument("--cc-starfield-color", type=int, default=CC_STARFIELD_COLOR, help="CC number for starfield colour.")
    ap.add_argument("--cc-text-speed", type=int, default=CC_TEXT_SPEED, help="CC number for text scroll speed.")
    ap.add_argument("--cc-text-color", type=int, default=CC_TEXT_COLOR, help="CC number for text colour (0=white, 127=hue cycle).")
    ap.add_argument(
        "--starfield-color-threshold",
        type=float,
        default=0.50,
        help="Sigmoid threshold for starfield color mapping (CC unit 0..1 where output is ~0.5).",
    )
    ap.add_argument(
        "--starfield-color-steepness",
        type=float,
        default=10.0,
        help="Sigmoid steepness for starfield color mapping (larger = sharper; <=0 disables sigmoid).",
    )
    ap.add_argument(
        "--target-fps",
        type=float,
        default=TARGET_FPS,
        help="Target frame rate cap. Use 0 or a negative value for uncapped rendering.",
    )
    return ap


def run(args, matrix, use_windows_mm_midi: bool = False):
    canvas = matrix.CreateFrameCanvas()

    demos = []
    if bool(getattr(args, "solo_multi_sinwaves", False)):
        demos.append(("multi_sinwaves", multi_sinwaves))
    elif bool(getattr(args, "solo_sinwave", False)):
        demos.append(("sinwave", sinwave))
    elif bool(getattr(args, "solo_starfield", False)):
        demos.append(("starfield", simple_starfield))
    elif bool(getattr(args, "solo_text_scroll", False)):
        demos.append(("text_scroll", text_scroll))
    else:
        demos.append(("starfield", simple_starfield))
        demos.append(("sinwave", sinwave))
        demos.append(("multi_sinwaves", multi_sinwaves))
        demos.append(("text_scroll", text_scroll))

    midi = MidiCCIn(port_query=args.midi_port, use_windows_mm=use_windows_mm_midi)
    midi_sync_enabled = args.midi_sync != "off"
    midi_sync_target = args.midi_sync
    if midi_sync_target == "wavelength":
        # Historical naming: user often means time-period "wavelength" (beat-locked), not spatial wavelength.
        midi_sync_target = "speed"
    if midi_sync_enabled and not midi.is_enabled():
        print("[midi] WARNING: --midi-sync enabled but MIDI input is disabled/unavailable (no ports?).")

    def _clamp_cc(n: int) -> int:
        if n < 0:
            return 0
        if n > 127:
            return 127
        return n

    cc_wave_speed = _clamp_cc(args.cc_wave_speed)
    cc_wave_wavelength = _clamp_cc(args.cc_wave_wavelength)
    cc_wave_color = _clamp_cc(args.cc_wave_color)
    cc_wave_phase = _clamp_cc(args.cc_wave_phase)
    cc_starfield_speed = _clamp_cc(args.cc_starfield_speed)
    cc_starfield_color = _clamp_cc(args.cc_starfield_color)
    cc_text_speed = _clamp_cc(args.cc_text_speed)
    cc_text_color = _clamp_cc(args.cc_text_color)

    sf_color_threshold = _clamp01(float(args.starfield_color_threshold))
    sf_color_steepness = float(args.starfield_color_steepness)

    # If we're syncing speed to MIDI clock, default to disabling wave-speed CC to avoid fighting sources.
    wave_speed_cc_mode = str(getattr(args, "wave_speed_cc_mapping", "auto")).lower()
    if wave_speed_cc_mode not in ("auto", "on", "off"):
        wave_speed_cc_mode = "auto"
    auto_enabled = midi_sync_target not in ("speed", "both")
    if wave_speed_cc_mode == "on":
        cc_wave_speed_enabled = True
    elif wave_speed_cc_mode == "off":
        cc_wave_speed_enabled = False
    else:
        cc_wave_speed_enabled = bool(auto_enabled)

    cc_map = {
        "wave_speed": cc_wave_speed if cc_wave_speed_enabled else None,
        "wave_wavelength": cc_wave_wavelength,
        "wave_color": cc_wave_color,
        "wave_phase": cc_wave_phase,
        "starfield_speed": cc_starfield_speed,
        "starfield_color": cc_starfield_color,
        "text_speed": cc_text_speed,
        "text_color": cc_text_color,
    }
    by_cc = {}
    for name, n in cc_map.items():
        if n is None:
            continue
        by_cc.setdefault(n, []).append(name)
    dupes = {n: names for n, names in by_cc.items() if len(names) > 1}

    print(
        "[midi] CC map:"
        f" wave_speed={'off' if not cc_wave_speed_enabled else cc_wave_speed}"
        f" wave_wavelength={cc_wave_wavelength}"
        f" wave_color={cc_wave_color}"
        f" wave_phase={cc_wave_phase}"
        f" starfield_speed={cc_starfield_speed}"
        f" starfield_color={cc_starfield_color}"
        f" text_speed={cc_text_speed}"
        f" text_color={cc_text_color}"
        f" (starfield_color_sigmoid=thr:{sf_color_threshold:.3f},k:{sf_color_steepness:.3f})"
        f" (log={args.midi_log})"
        f" (note_log={args.midi_note_log})"
    )
    if dupes:
        pretty = ", ".join([f"cc={n}: {names}" for n, names in sorted(dupes.items())])
        print(f"[midi] WARNING: duplicate CC assignments detected ({pretty}). All mapped actions will run.")

    # If we're doing MIDI logging, also enable demo-level debug logs where available.
    if args.midi_log != "none":
        sf_dbg = getattr(simple_starfield, "set_debug", None)
        if sf_dbg is not None:
            try:
                sf_dbg(True)
            except Exception:
                pass

    # Let each demo initialize any internal buffers/state.
    for _, demo in demos:
        if hasattr(demo, "setup"):
            demo.setup(matrix)

    target_fps = float(getattr(args, "target_fps", TARGET_FPS))
    start_time = time.time()
    active_idx = 0
    demos[active_idx][1].activate()
    print(f"Starting demo: {demos[active_idx][0]} (switch every {SWITCH_SECONDS:.0f}s). Press CTRL-C to stop.")

    try:
        last_clock_log_t = -1e9
        last_beat_index = None
        while True:
            frame_start = time.time()
            t_point = frame_start - start_time

            # Drain MIDI CC messages and route mapped controls.
            cc_msgs = midi.drain(now_t=t_point)
            note_msgs = midi.drain_notes()
            if note_msgs:
                if args.midi_note_log == "all":
                    for n in note_msgs:
                        state = "on" if bool(getattr(n, "is_on", False)) else "off"
                        note = int(getattr(n, "note", -1))
                        vel = int(getattr(n, "velocity", 0))
                        ch = int(getattr(n, "channel", 0))
                        pc = note % 12 if 0 <= note <= 127 else -1
                        print(
                            f"[midi] note t={getattr(n, 't', t_point):7.3f}s "
                            f"ch={ch:2d} note={note:3d} vel={vel:3d} "
                            f"pc={pc:2d} state={state}"
                        )
                active_demo = demos[active_idx][1]
                handler = getattr(active_demo, "handle_midi_note", None)
                if handler is not None:
                    for n in note_msgs:
                        try:
                            handler(n)
                        except Exception:
                            pass
                else:
                    handler_on = getattr(active_demo, "handle_midi_note_on", None)
                    handler_off = getattr(active_demo, "handle_midi_note_off", None)
                    if handler_on is not None or handler_off is not None:
                        for n in note_msgs:
                            try:
                                if n.is_on:
                                    if handler_on is not None:
                                        handler_on(n)
                                else:
                                    if handler_off is not None:
                                        handler_off(n)
                            except Exception:
                                pass

            # MIDI clock -> wave sync (optional).
            if midi_sync_enabled:
                running, bpm, start_pulse = midi.clock_state()
                if start_pulse:
                    # Align the wave on MIDI Start so the beat feels "locked".
                    try:
                        sinwave.activate()
                    except Exception:
                        pass
                    # Re-lock beat detection on transport start.
                    last_beat_index = None
                if running:
                    # Use MIDI clock ticks for both wave sync and beat-synced star respawn coloring.
                    _, _, ticks, _, _ = midi.clock_debug_state()

                    # Beat edge detection (24 PPQN -> one quarter-note beat every 24 ticks).
                    beat_index = int(ticks) // 24
                    if beat_index != last_beat_index:
                        last_beat_index = beat_index
                        new_color = random.choice(_STARFIELD_SPAWN_PALETTE)
                        setter = getattr(simple_starfield, "set_spawn_color_type", None)
                        if setter is not None:
                            try:
                                setter(new_color)
                            except Exception:
                                pass

                    # Text scroll: advance scroll phase with beats (8 pixels per beat).
                    text_scroll_phase_px = (float(ticks) / 24.0) * 8.0
                    setter = getattr(text_scroll, "set_scroll_phase", None)
                    if setter is not None:
                        try:
                            setter(text_scroll_phase_px)
                        except Exception:
                            pass

                    # Speed sync: hard-lock phase to MIDI clock tick count (no drift).
                    if midi_sync_target in ("speed", "both"):
                        beats_per_cycle = float(args.midi_sync_beats_per_cycle)
                        if beats_per_cycle <= 0.0:
                            beats_per_cycle = 1.0
                        # phase = 2π * beats / beats_per_cycle, where beats = ticks / PPQN
                        phase = (2.0 * math.pi) * ((float(ticks) / 24.0) / beats_per_cycle)
                        setter = getattr(sinwave, "set_external_phase", None)
                        if setter is not None:
                            try:
                                setter(phase)
                            except Exception:
                                pass

                    # Spatial wavelength sync: maps tempo to spatial wavelength multiplier (optional).
                    if midi_sync_target in ("spatial", "both") and isinstance(bpm, (int, float)) and bpm > 0.0:
                        ref = float(args.midi_sync_ref_bpm) if args.midi_sync_ref_bpm > 0 else 120.0
                        mult = ref / float(bpm)
                        if mult < float(args.midi_sync_wavelength_min):
                            mult = float(args.midi_sync_wavelength_min)
                        if mult > float(args.midi_sync_wavelength_max):
                            mult = float(args.midi_sync_wavelength_max)
                        setter = getattr(sinwave, "set_wavelength_mult", None)
                        if setter is not None:
                            try:
                                setter(mult)
                            except Exception:
                                pass
                else:
                    # When clock isn't running, release external phase so the wave returns to internal integration.
                    setter = getattr(sinwave, "set_external_phase", None)
                    if setter is not None:
                        try:
                            setter(None)
                        except Exception:
                            pass
                    # Text scroll: use time-based scroll again.
                    setter = getattr(text_scroll, "set_scroll_phase", None)
                    if setter is not None:
                        try:
                            setter(None)
                        except Exception:
                            pass

                    if args.midi_sync_log == "clock" and (t_point - last_clock_log_t) >= 1.0:
                        last_clock_log_t = t_point
                        r, b, ticks, last_dt, win = midi.clock_debug_state()
                        bpm_s = f"{float(b):.2f}" if isinstance(b, (int, float)) else "?"
                        dt_s = f"{float(last_dt):.4f}" if isinstance(last_dt, (int, float)) else "?"
                        print(f"[midi] clock running={r} bpm={bpm_s} ticks={ticks} last_dt={dt_s}s win={win} sync={midi_sync_target}")
                    elif args.midi_sync_log == "bpm" and (t_point - last_clock_log_t) >= 1.0:
                        last_clock_log_t = t_point
                        if isinstance(bpm, (int, float)) and bpm > 0.0:
                            print(f"[midi] clock running bpm={float(bpm):.2f} sync={midi_sync_target}")
                        else:
                            r, _, ticks, _, win = midi.clock_debug_state()
                            if r:
                                print(f"[midi] clock running (estimating...) ticks={ticks} win={win} sync={midi_sync_target}")
            if cc_msgs:
                log_all = args.midi_log in ("all", "both")
                log_mapped = args.midi_log in ("mapped", "both")

                mapped_controls = {
                    cc_wave_wavelength,
                    cc_wave_color,
                    cc_wave_phase,
                    cc_starfield_speed,
                    cc_starfield_color,
                    cc_text_speed,
                    cc_text_color,
                }
                if cc_wave_speed_enabled:
                    mapped_controls.add(cc_wave_speed)
                mapped_msgs = [cc for cc in cc_msgs if cc.control in mapped_controls]
                if log_all:
                    for cc in cc_msgs:
                        tag = "mapped" if cc.control in mapped_controls else "unmapped"
                        print(
                            f"[midi] {tag} t={cc.t:7.3f}s ch={cc.channel:2d} cc={cc.control:3d} val={cc.value:3d}"
                        )

                if mapped_msgs and log_mapped:
                    unique_controls = sorted({cc.control for cc in mapped_msgs})
                    print(
                        f"[midi] mapped CC detected ({len(mapped_msgs)} msg{'s' if len(mapped_msgs) != 1 else ''}) "
                        f"controls={unique_controls}"
                    )
                    for cc in mapped_msgs:
                        print(f"[midi] t={cc.t:7.3f}s ch={cc.channel:2d} cc={cc.control:3d} val={cc.value:3d}")

                for cc in mapped_msgs:
                    # NOTE: these are intentionally *not* elif, so if a CC number is
                    # assigned to multiple actions (by user config), everything still runs.
                    if cc.control == cc_wave_color:
                        # Prefer a dedicated setter so the CC number can be remapped freely.
                        setter = getattr(sinwave, "set_color_cc_value", None)
                        if setter is not None:
                            setter(cc.value)
                            if log_mapped:
                                print(f"[midi] wave color -> {_cc_unit(cc.value):.3f}")
                        else:
                            handler = getattr(sinwave, "handle_midi_cc", None)
                            if handler is not None:
                                handler(cc)
                                if log_mapped:
                                    print(f"[midi] wave color -> {_cc_unit(cc.value):.3f}")
                    if cc_wave_speed_enabled and cc.control == cc_wave_speed:
                        # 0..2x
                        mult = _lerp(0.0, 2.0, _cc_unit(cc.value))
                        setter = getattr(sinwave, "set_speed_mult", None)
                        if setter is not None:
                            setter(mult)
                            if log_mapped:
                                print(f"[midi] wave speed -> {mult:.3f}x")
                        else:
                            # Back-compat: allow older sinwave modules by mutating module state.
                            if hasattr(sinwave, "_speed_mult"):
                                try:
                                    setattr(sinwave, "_speed_mult", float(mult))
                                    if log_mapped:
                                        print(f"[midi] wave speed -> {mult:.3f}x (compat)")
                                except Exception:
                                    pass
                    if cc.control == cc_wave_wavelength:
                        # 1.0x at 0 .. 0.25x at 127
                        mult = _lerp(1.0, 0.25, _cc_unit(cc.value))
                        setter = getattr(sinwave, "set_wavelength_mult", None)
                        if setter is not None:
                            setter(mult)
                            if log_mapped:
                                print(f"[midi] wave wavelength -> {mult:.3f}x")
                        else:
                            # Back-compat: allow older sinwave modules by mutating module state.
                            if hasattr(sinwave, "_wavelength_mult"):
                                try:
                                    setattr(sinwave, "_wavelength_mult", float(mult))
                                    if log_mapped:
                                        print(f"[midi] wave wavelength -> {mult:.3f}x (compat)")
                                except Exception:
                                    pass
                    if cc.control == cc_wave_phase:
                        # 0..2π
                        radians = _lerp(0.0, 2.0 * math.pi, _cc_unit(cc.value))
                        setter = getattr(sinwave, "set_phase_offset", None)
                        if setter is not None:
                            setter(radians)
                            if log_mapped:
                                print(f"[midi] wave phase -> {radians:.3f} rad")
                        else:
                            if hasattr(sinwave, "_phase_offset"):
                                try:
                                    setattr(sinwave, "_phase_offset", float(radians))
                                    if log_mapped:
                                        print(f"[midi] wave phase -> {radians:.3f} rad (compat)")
                                except Exception:
                                    pass
                    if cc.control == cc_starfield_speed:
                        # 0.5..4x
                        mult = _lerp(0.5, 4.0, _cc_unit(cc.value))
                        setter = getattr(simple_starfield, "set_speed_mult", None)
                        if setter is not None:
                            setter(mult)
                            if log_mapped:
                                print(f"[midi] starfield speed -> {mult:.3f}x")
                        else:
                            if hasattr(simple_starfield, "_speed_mult"):
                                try:
                                    setattr(simple_starfield, "_speed_mult", float(mult))
                                    if log_mapped:
                                        print(f"[midi] starfield speed -> {mult:.3f}x (compat)")
                                except Exception:
                                    pass
                    if cc.control == cc_starfield_color:
                        # 0..1 (white -> colored)
                        raw = _cc_unit(cc.value)
                        amt = _sigmoid01(raw, threshold=sf_color_threshold, steepness=sf_color_steepness)
                        setter = getattr(simple_starfield, "set_color_amount", None)
                        if setter is not None:
                            setter(amt)
                            if log_mapped:
                                eff = getattr(simple_starfield, "_color_amount", None)
                                dz = getattr(simple_starfield, "_COLOR_DEADZONE", None)
                                if isinstance(eff, (int, float)):
                                    if isinstance(dz, (int, float)):
                                        print(
                                            f"[midi] starfield color -> raw={raw:.3f} amt={amt:.3f} "
                                            f"effective={float(eff):.3f} deadzone={float(dz):.3f}"
                                        )
                                    else:
                                        print(f"[midi] starfield color -> raw={raw:.3f} amt={amt:.3f} effective={float(eff):.3f}")
                                else:
                                    print(f"[midi] starfield color -> raw={raw:.3f} amt={amt:.3f}")
                        else:
                            if hasattr(simple_starfield, "_color_amount"):
                                try:
                                    setattr(simple_starfield, "_color_amount", float(amt))
                                    if log_mapped:
                                        eff = getattr(simple_starfield, "_color_amount", None)
                                        dz = getattr(simple_starfield, "_COLOR_DEADZONE", None)
                                        if isinstance(eff, (int, float)):
                                            if isinstance(dz, (int, float)):
                                                print(
                                                    f"[midi] starfield color -> raw={raw:.3f} amt={amt:.3f} "
                                                    f"effective={float(eff):.3f} deadzone={float(dz):.3f} (compat)"
                                                )
                                            else:
                                                print(
                                                    f"[midi] starfield color -> raw={raw:.3f} amt={amt:.3f} "
                                                    f"effective={float(eff):.3f} (compat)"
                                                )
                                        else:
                                            print(f"[midi] starfield color -> raw={raw:.3f} amt={amt:.3f} (compat)")
                                except Exception:
                                    pass
                    if cc.control == cc_text_speed:
                        # 0.5..2x scroll speed
                        mult = _lerp(0.5, 2.0, _cc_unit(cc.value))
                        setter = getattr(text_scroll, "set_speed_mult", None)
                        if setter is not None:
                            setter(mult)
                            if log_mapped:
                                print(f"[midi] text scroll speed -> {mult:.3f}x")
                    if cc.control == cc_text_color:
                        setter = getattr(text_scroll, "set_color_cc_value", None)
                        if setter is not None:
                            setter(cc.value)
                            if log_mapped:
                                print(f"[midi] text colour -> {_cc_unit(cc.value):.3f}")

            next_idx = int(t_point // SWITCH_SECONDS) % len(demos)
            if next_idx != active_idx:
                active_idx = next_idx
                demos[active_idx][1].activate()
                print(f"Switched to: {demos[active_idx][0]}")

            canvas.Clear()
            demos[active_idx][1].draw(canvas, matrix, t_point)
            canvas = matrix.SwapOnVSync(canvas)

            if target_fps > 0.0:
                frame_budget = 1.0 / target_fps
                elapsed = time.time() - frame_start
                if elapsed < frame_budget:
                    time.sleep(frame_budget - elapsed)

    except KeyboardInterrupt:
        print("\nExiting...")
        matrix.Clear()
    except Exception as e:
        if type(e).__name__ == "ScreenClosed":
            print("\nDisplay closed.")
        else:
            raise
        matrix.Clear()


def main():
    ap = get_parser()
    args = ap.parse_args()
    matrix = _build_matrix()
    run(args, matrix)


if __name__ == "__main__":
    main()

