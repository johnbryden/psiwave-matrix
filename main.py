#!/usr/bin/env -S python3 -u

import sys
import time
import math
import random
import argparse

# Non-blocking keyboard check for space-to-next-effect (optional)
def _read_key_nonblock():
    """Return a single character if one is available, else None. Works on Windows and Unix."""
    if sys.platform == "win32":
        try:
            import msvcrt
            if msvcrt.kbhit():
                return msvcrt.getch().decode("utf-8", errors="replace")
        except Exception:
            pass
        return None
    # Unix: use select + read (terminal must be in cbreak mode for single-key)
    try:
        import select
        if select.select([sys.stdin], [], [], 0.0)[0]:
            return sys.stdin.read(1)
    except Exception:
        pass
    return None


def _stdin_cbreak_enter():
    """Put stdin in cbreak mode on Unix so keys are read immediately. Returns (True, restore_fn) or (False, noop)."""
    if sys.platform != "win32" and sys.stdin.isatty():
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            def restore():
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                except Exception:
                    pass
            return True, restore
        except Exception:
            pass
    return False, lambda: None


def _stdin_cbreak_exit(restore_fn):
    """Restore terminal after cbreak mode."""
    restore_fn()

from midi import (
    MidiInput, MidiRouter, CCBinding, LinearTransform, SigmoidTransform,
    RawCCTransform, Strategy,
)
from sinwave import SinwaveEffect
from simple_starfield import StarfieldEffect
from multi_sinwaves import MultiSinwavesEffect
from text_scroll import TextScrollEffect
from scanline_notes import ScanlineNotesEffect


SWITCH_SECONDS = 30.0
TARGET_FPS = 60.0

# Default CC assignments (overridable via CLI)
CC_WAVE_SPEED = -1
CC_WAVE_WAVELENGTH = 102
CC_WAVE_COLOR = 108
CC_WAVE_PHASE = -1
CC_STARFIELD_SPEED = 101
CC_STARFIELD_COLOR = 102
CC_TEXT_SPEED = 101
CC_TEXT_COLOR = 102

_STARFIELD_SPAWN_PALETTE = ("white", "blue", "cyan", "yellow", "orange", "red")


def _build_matrix():
    from rgbmatrix import RGBMatrix, RGBMatrixOptions

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
    demo_group.add_argument("--solo-starfield", action="store_true", help="Run only the starfield demo.")
    demo_group.add_argument("--solo-sinwave", action="store_true", help="Run only the sinwave demo.")
    demo_group.add_argument("--solo-multi-sinwaves", action="store_true", help="Run only the multi-sinwaves demo.")
    demo_group.add_argument("--solo-text-scroll", action="store_true", help="Run only the text scroll demo.")
    demo_group.add_argument("--solo-scanline-notes", action="store_true", help="Run only the scanline-notes demo.")
    ap.add_argument("--midi-port", default=None, help="MIDI input port name (substring match).")
    ap.add_argument(
        "--midi-sync",
        choices=("off", "wavelength", "speed", "spatial", "both"),
        default="speed",
        help=(
            "Sync wave parameters to MIDI clock (default: speed). "
            "'speed' beat-locks animation phase; "
            "'wavelength' is an alias for 'speed'; "
            "'spatial' maps BPM to spatial wavelength multiplier; "
            "'both' applies speed+spatial; 'off' disables."
        ),
    )
    ap.add_argument("--midi-sync-ref-bpm", type=float, default=120.0, help="Reference BPM for wavelength mapping.")
    ap.add_argument("--midi-sync-wavelength-min", type=float, default=0.25, help="Min wavelength multiplier when syncing.")
    ap.add_argument("--midi-sync-wavelength-max", type=float, default=2.00, help="Max wavelength multiplier when syncing.")
    ap.add_argument("--midi-sync-beats-per-cycle", type=float, default=2.0, help="Beats per full 2pi cycle for speed sync.")
    ap.add_argument("--midi-sync-log", choices=("none", "bpm", "clock"), default="none", help="Log MIDI clock status.")
    ap.add_argument(
        "--wave-speed-cc-mapping",
        choices=("auto", "on", "off"),
        default="auto",
        help="Wave-speed CC mapping mode. 'auto' disables when --midi-sync is 'speed'/'both'.",
    )
    ap.add_argument(
        "--midi-log",
        choices=("mapped", "all", "both", "none"),
        default="none",
        help="MIDI logging mode.",
    )
    ap.add_argument(
        "--midi-note-log",
        choices=("none", "all"),
        default="none",
        help="MIDI note logging mode.",
    )
    ap.add_argument("--cc-wave-speed", type=int, default=CC_WAVE_SPEED, help="CC number for wave speed.")
    ap.add_argument("--cc-wave-wavelength", type=int, default=CC_WAVE_WAVELENGTH, help="CC number for wave wavelength.")
    ap.add_argument("--cc-wave-color", type=int, default=CC_WAVE_COLOR, help="CC number for wave colour.")
    ap.add_argument("--cc-wave-phase", type=int, default=CC_WAVE_PHASE, help="CC number for wave phase.")
    ap.add_argument("--cc-starfield-speed", type=int, default=CC_STARFIELD_SPEED, help="CC number for starfield speed.")
    ap.add_argument("--cc-starfield-color", type=int, default=CC_STARFIELD_COLOR, help="CC number for starfield colour.")
    ap.add_argument("--cc-text-speed", type=int, default=CC_TEXT_SPEED, help="CC number for text scroll speed.")
    ap.add_argument("--cc-text-color", type=int, default=CC_TEXT_COLOR, help="CC number for text colour.")
    ap.add_argument("--starfield-color-threshold", type=float, default=0.50, help="Sigmoid threshold for starfield color.")
    ap.add_argument("--starfield-color-steepness", type=float, default=10.0, help="Sigmoid steepness for starfield color.")
    ap.add_argument("--target-fps", type=float, default=TARGET_FPS, help="Target frame rate cap (0 = uncapped).")
    return ap


# ---------------------------------------------------------------------------
# CC binding builder
# ---------------------------------------------------------------------------

def _clamp_cc(n: int) -> int:
    if n < 0:
        return n
    if n > 127:
        return 127
    return n


def _build_bindings(args, sinwave_fx, starfield_fx, text_fx):
    """Create CCBinding list from CLI args. This is the ONE place CC numbers live."""
    bindings = []

    midi_sync_target = args.midi_sync
    if midi_sync_target == "wavelength":
        midi_sync_target = "speed"

    wave_speed_mode = str(getattr(args, "wave_speed_cc_mapping", "auto")).lower()
    auto_enabled = midi_sync_target not in ("speed", "both")
    if wave_speed_mode == "on":
        cc_wave_speed_enabled = True
    elif wave_speed_mode == "off":
        cc_wave_speed_enabled = False
    else:
        cc_wave_speed_enabled = bool(auto_enabled)

    cc_wave_speed = _clamp_cc(args.cc_wave_speed)
    cc_wave_wavelength = _clamp_cc(args.cc_wave_wavelength)
    cc_wave_color = _clamp_cc(args.cc_wave_color)
    cc_wave_phase = _clamp_cc(args.cc_wave_phase)
    cc_starfield_speed = _clamp_cc(args.cc_starfield_speed)
    cc_starfield_color = _clamp_cc(args.cc_starfield_color)
    cc_text_speed = _clamp_cc(args.cc_text_speed)
    cc_text_color = _clamp_cc(args.cc_text_color)

    if cc_wave_speed_enabled and cc_wave_speed >= 0:
        bindings.append(CCBinding(
            ccs=[cc_wave_speed], target=sinwave_fx, param="speed",
            transform=LinearTransform(0.0, 2.0),
        ))

    if cc_wave_wavelength >= 0:
        bindings.append(CCBinding(
            ccs=[cc_wave_wavelength], target=sinwave_fx, param="wavelength",
            transform=LinearTransform(1.0, 0.25),
        ))

    if cc_wave_color >= 0:
        bindings.append(CCBinding(
            ccs=[cc_wave_color], target=sinwave_fx, param="color",
            transform=RawCCTransform(),
        ))

    if cc_wave_phase >= 0:
        bindings.append(CCBinding(
            ccs=[cc_wave_phase], target=sinwave_fx, param="phase_offset",
            transform=LinearTransform(0.0, 2.0 * math.pi),
        ))

    if cc_starfield_speed >= 0:
        bindings.append(CCBinding(
            ccs=[cc_starfield_speed], target=starfield_fx, param="speed",
            transform=LinearTransform(0.5, 4.0),
        ))

    if cc_starfield_color >= 0:
        sf_thr = max(0.0, min(1.0, float(args.starfield_color_threshold)))
        sf_k = float(args.starfield_color_steepness)
        bindings.append(CCBinding(
            ccs=[cc_starfield_color], target=starfield_fx, param="color_amount",
            transform=SigmoidTransform(0.0, 1.0, threshold=sf_thr, steepness=sf_k),
        ))

    if cc_text_speed >= 0:
        bindings.append(CCBinding(
            ccs=[cc_text_speed], target=text_fx, param="speed",
            transform=LinearTransform(0.5, 2.0),
        ))

    if cc_text_color >= 0:
        bindings.append(CCBinding(
            ccs=[cc_text_color], target=text_fx, param="color",
            transform=RawCCTransform(),
        ))

    return bindings


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------

def run(args, matrix, use_windows_mm_midi: bool = False):
    canvas = matrix.CreateFrameCanvas()
    w, h = int(matrix.width), int(matrix.height)

    # Instantiate effects
    sinwave_fx = SinwaveEffect(w, h)
    starfield_fx = StarfieldEffect(w, h)
    multi_sin_fx = MultiSinwavesEffect(w, h)
    text_fx = TextScrollEffect(w, h)
    scanline_fx = ScanlineNotesEffect(w, h)

    all_effects = {
        "sinwave": sinwave_fx,
        "starfield": starfield_fx,
        "multi_sinwaves": multi_sin_fx,
        "text_scroll": text_fx,
        "scanline_notes": scanline_fx,
    }

    # Determine which demos to cycle through
    demos = []
    if bool(getattr(args, "solo_multi_sinwaves", False)):
        demos.append(("multi_sinwaves", multi_sin_fx))
    elif bool(getattr(args, "solo_sinwave", False)):
        demos.append(("sinwave", sinwave_fx))
    elif bool(getattr(args, "solo_starfield", False)):
        demos.append(("starfield", starfield_fx))
    elif bool(getattr(args, "solo_text_scroll", False)):
        demos.append(("text_scroll", text_fx))
    elif bool(getattr(args, "solo_scanline_notes", False)):
        demos.append(("scanline_notes", scanline_fx))
    else:
        demos.append(("starfield", starfield_fx))
        demos.append(("sinwave", sinwave_fx))
        demos.append(("multi_sinwaves", multi_sin_fx))
        demos.append(("text_scroll", text_fx))
        demos.append(("scanline_notes", scanline_fx))

    # MIDI input
    midi = MidiInput(port_query=args.midi_port, use_windows_mm=use_windows_mm_midi)

    midi_sync_target = args.midi_sync
    if midi_sync_target == "wavelength":
        midi_sync_target = "speed"
    midi_sync_enabled = midi_sync_target != "off"
    if midi_sync_enabled and not midi.is_enabled():
        print("[midi] WARNING: --midi-sync enabled but MIDI input is disabled/unavailable.")
    if midi_sync_enabled:
        print(f"[midi] Sync mode: {midi_sync_target} (debug: --midi-sync-log clock or bpm)")

    # Build the declarative CC routing
    bindings = _build_bindings(args, sinwave_fx, starfield_fx, text_fx)
    router = MidiRouter(log_mode=args.midi_log)
    for b in bindings:
        router.add(b)

    print(f"[midi] CC bindings: {router.describe()}")
    print(f"[midi] log={args.midi_log} note_log={args.midi_note_log}")

    # Setup all effects
    for fx in all_effects.values():
        fx.setup(matrix)

    # If we're doing MIDI logging, enable starfield debug
    if args.midi_log != "none":
        starfield_fx.set_debug(True)

    target_fps = float(getattr(args, "target_fps", TARGET_FPS))
    start_time = time.time()
    active_idx = 0
    demos[active_idx][1].activate()

    # Optional: cbreak mode on Unix so keys are read without Enter
    cbreak_ok, restore_stdin = _stdin_cbreak_enter()
    print(
        f"Starting demo: {demos[active_idx][0]} (switch every {SWITCH_SECONDS:.0f}s). "
        f"Press 'n' for next effect. CTRL-C to stop."
    )

    try:
        last_clock_log_t = -1e9
        last_beat_index = None

        while True:
            frame_start = time.time()
            t_point = frame_start - start_time

            # Drain MIDI
            cc_msgs = midi.drain(now_t=t_point)
            note_msgs = midi.drain_notes()

            # Note logging + dispatch to active effect
            if note_msgs:
                if args.midi_note_log == "all":
                    for n in note_msgs:
                        state = "on" if n.is_on else "off"
                        pc = n.note % 12 if 0 <= n.note <= 127 else -1
                        print(
                            f"[midi] note t={n.t:7.3f}s ch={n.channel:2d} "
                            f"note={n.note:3d} vel={n.velocity:3d} pc={pc:2d} state={state}"
                        )
                active_fx = demos[active_idx][1]
                for n in note_msgs:
                    try:
                        active_fx.handle_note(n)
                    except Exception:
                        pass

            # MIDI clock sync (orchestration between clock and effects)
            if midi_sync_enabled:
                running, bpm, start_pulse = midi.clock_state()

                if start_pulse:
                    try:
                        sinwave_fx.activate()
                    except Exception:
                        pass
                    last_beat_index = None

                if running:
                    _, _, ticks, last_dt, win = midi.clock_debug_state()

                    # Debug: log when clock is running (so you can confirm sync is active)
                    if args.midi_sync_log == "clock" and (t_point - last_clock_log_t) >= 2.0:
                        last_clock_log_t = t_point
                        bpm_s = f"{float(bpm):.2f}" if isinstance(bpm, (int, float)) else "?"
                        dt_s = f"{float(last_dt):.4f}" if isinstance(last_dt, (int, float)) else "?"
                        print(f"[midi] clock running=True bpm={bpm_s} ticks={ticks} last_dt={dt_s}s win={win} sync={midi_sync_target}")

                    # Beat edge: 24 PPQN -> one beat every 24 ticks
                    beat_index = int(ticks) // 24
                    if beat_index != last_beat_index:
                        last_beat_index = beat_index
                        new_color = random.choice(_STARFIELD_SPAWN_PALETTE)
                        starfield_fx.set_spawn_color_type(new_color)

                    # Text scroll phase from beats (8 pixels per beat)
                    text_scroll_phase_px = (float(ticks) / 24.0) * 8.0
                    text_fx.set_scroll_phase(text_scroll_phase_px)

                    # Scanline: one sweep per bar (4 beats)
                    beats = float(ticks) / 24.0
                    bar_phase = (beats % 4.0) / 4.0
                    scanline_fx.set_sweep_phase(bar_phase)

                    # Speed sync: lock sinwave phase to clock
                    if midi_sync_target in ("speed", "both"):
                        beats_per_cycle = float(args.midi_sync_beats_per_cycle)
                        if beats_per_cycle <= 0.0:
                            beats_per_cycle = 1.0
                        phase = (2.0 * math.pi) * ((float(ticks) / 24.0) / beats_per_cycle)
                        sinwave_fx.set_external_phase(phase)

                    # Spatial wavelength sync
                    if midi_sync_target in ("spatial", "both") and isinstance(bpm, (int, float)) and bpm > 0.0:
                        ref = float(args.midi_sync_ref_bpm) if args.midi_sync_ref_bpm > 0 else 120.0
                        mult = ref / float(bpm)
                        mult = max(float(args.midi_sync_wavelength_min), min(float(args.midi_sync_wavelength_max), mult))
                        sinwave_fx.set_wavelength_mult(mult)
                else:
                    # Clock not running -- release overrides
                    sinwave_fx.set_external_phase(None)
                    text_fx.set_scroll_phase(None)
                    scanline_fx.set_sweep_phase(None)

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

            # Route CC messages through the declarative bindings
            router.process(cc_msgs)

            # Keyboard: switch to next effect and reset timer
            key = _read_key_nonblock()
            if key in ("n", "N", " "):
                active_idx = (active_idx + 1) % len(demos)
                demos[active_idx][1].activate()
                start_time = time.time()
                print(f"Switched to: {demos[active_idx][0]}")

            # Demo switching (time-based)
            next_idx = int(t_point // SWITCH_SECONDS) % len(demos)
            if next_idx != active_idx:
                active_idx = next_idx
                demos[active_idx][1].activate()
                print(f"Switched to: {demos[active_idx][0]}")

            # Render
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
    finally:
        _stdin_cbreak_exit(restore_stdin)


def main():
    ap = get_parser()
    args = ap.parse_args()
    matrix = _build_matrix()
    run(args, matrix)


if __name__ == "__main__":
    main()
