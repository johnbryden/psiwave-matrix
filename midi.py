#!/usr/bin/env -S python3 -u
"""
MIDI infrastructure for psiwave-matrix.

Contains:
- MidiCC / MidiNote dataclasses
- MidiInput: non-blocking MIDI input with clock tracking
- Transform functions for mapping 0..1 to parameter ranges
- CCResolver: aggregation strategies for multi-CC inputs
- CCBinding: declarative CC-to-parameter link
- MidiRouter: processes incoming CCs and dispatches to effect params
"""

from __future__ import annotations

import sys
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Tuple, Callable, Dict, Any, Union


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def cc_unit(v: int) -> float:
    """Map CC value (0..127) to 0..1."""
    if v <= 0:
        return 0.0
    if v >= 127:
        return 1.0
    return v / 127.0


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp01(x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return float(x)


def sigmoid01(x: float, *, threshold: float = 0.5, steepness: float = 10.0) -> float:
    """
    Sigmoid curve mapping x in [0,1] -> y in [0,1].

    - threshold: the input value where the curve crosses 0.5
    - steepness: curve sharpness; <=0 falls back to linear
    - endpoints forced: x=0 -> 0, x=1 -> 1
    """
    x = clamp01(x)
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    t = clamp01(threshold)
    k = float(steepness)
    if not (k > 0.0):
        return x

    z = k * (x - t)
    if z >= 0.0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


# ---------------------------------------------------------------------------
# MIDI message types
# ---------------------------------------------------------------------------

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
    is_on: bool     # True for NoteOn (vel>0), False for NoteOff
    t: float        # seconds (relative to program start)


# ---------------------------------------------------------------------------
# MidiInput (non-blocking MIDI, was MidiCCIn)
# ---------------------------------------------------------------------------

class MidiInput:
    """
    Non-blocking MIDI input using python-rtmidi.

    Default: first available MIDI input port, any channel.
    If MIDI isn't available, becomes a no-op so rendering continues.
    """

    def __init__(self, port_query: Optional[str] = None, use_windows_mm: bool = False):
        self._midiin = None
        self._clock_ppqn = 24
        self._clock_running = False
        self._clock_last_tick_t: Optional[float] = None
        self._clock_tick_dts: List[float] = []
        self._clock_bpm: Optional[float] = None
        self._clock_start_pulse = False
        self._clock_tick_count = 0
        self._clock_last_dt: Optional[float] = None
        self._note_queue: List[MidiNote] = []
        self._clock_first_tick_logged = False

        try:
            import rtmidi  # type: ignore
        except Exception as e:
            print(f"[midi] disabled (could not import python-rtmidi): {e}")
            return

        MidiIn = getattr(rtmidi, "MidiIn", None)
        if MidiIn is None:
            print(
                "[midi] disabled (rtmidi.MidiIn not found).\n"
                "[midi] Fix on Pi: `sudo apt install python3-rtmidi` or `pip install -U python-rtmidi`.\n"
                "[midi] If you installed a package literally named 'rtmidi', uninstall it."
            )
            return

        midi_kwargs: dict = {}
        if use_windows_mm:
            api = getattr(rtmidi, "API_WINDOWS_MM", None)
            get_apis = getattr(rtmidi, "get_compiled_api", None)
            if api is not None and get_apis is not None and callable(get_apis) and api in get_apis():
                midi_kwargs["rtapi"] = api

        try:
            self._midiin = MidiIn(**midi_kwargs) if midi_kwargs else MidiIn()
            if midi_kwargs:
                print("[midi] using Windows MM API for port enumeration (e.g. loopMIDI)")
            if hasattr(self._midiin, "ignore_types"):
                try:
                    self._midiin.ignore_types(sysex=True, timing=False, active_sense=True)
                except TypeError:
                    try:
                        self._midiin.ignore_types(True, False, True)
                    except Exception:
                        pass
        except Exception as e:
            print(f"[midi] disabled (could not initialize MIDI input): {e}")
            self._midiin = None
            return

        # Virtual drivers can register late; retry a few times on Windows.
        ports: list = []
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
                print("[midi] Ensure loopMIDI (or your device) is running. Use main_screen.py on Windows.")
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
            def _score_port(name: str) -> int:
                s = name.lower()
                score = 0
                if "midi in" in s:
                    score += 100
                if "through" in s:
                    score -= 100
                else:
                    score += 10
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

    # -- Clock handling ------------------------------------------------------

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
        if not self._clock_first_tick_logged:
            self._clock_first_tick_logged = True
            print("[midi] first clock tick received (MIDI clock sync active)")
        if not self._clock_running:
            self._clock_running = True
        self._clock_tick_count += 1
        if self._clock_last_tick_t is not None:
            dt = now_t - self._clock_last_tick_t
            self._clock_last_dt = dt
            if 0.002 <= dt <= 0.25:
                self._clock_tick_dts.append(dt)
                if len(self._clock_tick_dts) > 96:
                    del self._clock_tick_dts[:-96]
                if len(self._clock_tick_dts) >= 4:
                    avg_dt = sum(self._clock_tick_dts) / len(self._clock_tick_dts)
                    if avg_dt > 0:
                        bps = 1.0 / (avg_dt * float(self._clock_ppqn))
                        self._clock_bpm = 60.0 * bps
        self._clock_last_tick_t = now_t

    def clock_state(self) -> Tuple[bool, Optional[float], bool]:
        """Returns (running, bpm, start_pulse). start_pulse is True once after MIDI Start."""
        sp = self._clock_start_pulse
        self._clock_start_pulse = False
        return self._clock_running, self._clock_bpm, sp

    def clock_debug_state(self) -> Tuple[bool, Optional[float], int, Optional[float], int]:
        """Returns (running, bpm, tick_count, last_dt, window_len)."""
        return (
            self._clock_running,
            self._clock_bpm,
            int(self._clock_tick_count),
            self._clock_last_dt,
            len(self._clock_tick_dts),
        )

    # -- Message draining ----------------------------------------------------

    def drain(self, now_t: float) -> List[MidiCC]:
        """Drain pending MIDI messages; returns CCs and updates clock state."""
        if self._midiin is None:
            return []

        out: List[MidiCC] = []
        try:
            while True:
                msg = self._midiin.get_message()
                if not msg:
                    break
                data = msg[0]
                if not data or len(data) < 1:
                    continue

                status = int(data[0]) & 0xFF

                if status == 0xF8:
                    self._clock_on_tick(now_t=now_t)
                    continue
                if status == 0xFA:
                    self._clock_on_start()
                    continue
                if status == 0xFB:
                    self._clock_running = True
                    continue
                if status == 0xFC:
                    self._clock_on_stop()
                    continue
                if status >= 0xF8:
                    continue

                if len(data) < 3:
                    continue
                msg_type = status & 0xF0
                ch = (status & 0x0F) + 1

                if msg_type == 0x90 or msg_type == 0x80:
                    note = int(data[1]) & 0x7F
                    vel = int(data[2]) & 0x7F
                    is_on = (msg_type == 0x90) and (vel > 0)
                    self._note_queue.append(
                        MidiNote(channel=ch, note=note, velocity=vel, is_on=is_on, t=now_t)
                    )

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
            return out
        return out

    def drain_notes(self) -> List[MidiNote]:
        """Drain queued note events."""
        if not self._note_queue:
            return []
        out = self._note_queue
        self._note_queue = []
        return out

    def is_enabled(self) -> bool:
        return self._midiin is not None


# ---------------------------------------------------------------------------
# Transforms: map a 0..1 unit value to a parameter range
# ---------------------------------------------------------------------------

class LinearTransform:
    """Maps 0..1 to [low, high] linearly."""
    __slots__ = ("low", "high")

    def __init__(self, low: float, high: float):
        self.low = low
        self.high = high

    def __call__(self, u: float) -> float:
        return lerp(self.low, self.high, u)

    def __repr__(self) -> str:
        return f"LinearTransform({self.low}, {self.high})"


class SigmoidTransform:
    """Applies a sigmoid curve then maps to [low, high]."""
    __slots__ = ("threshold", "steepness", "low", "high")

    def __init__(self, low: float, high: float, threshold: float = 0.5, steepness: float = 10.0):
        self.low = low
        self.high = high
        self.threshold = threshold
        self.steepness = steepness

    def __call__(self, u: float) -> float:
        s = sigmoid01(u, threshold=self.threshold, steepness=self.steepness)
        return lerp(self.low, self.high, s)

    def __repr__(self) -> str:
        return f"SigmoidTransform({self.low}, {self.high}, thr={self.threshold}, k={self.steepness})"


class IdentityTransform:
    """Passes the 0..1 value through unchanged."""
    def __call__(self, u: float) -> float:
        return u

    def __repr__(self) -> str:
        return "IdentityTransform()"


class RawCCTransform:
    """Passes the raw CC value (0..127) instead of the 0..1 unit value."""
    def __call__(self, u: float) -> float:
        return u * 127.0

    def __repr__(self) -> str:
        return "RawCCTransform()"


# ---------------------------------------------------------------------------
# CC Resolution strategies
# ---------------------------------------------------------------------------

class Strategy(Enum):
    MOST_RECENT_OF_ANY = "most_recent_of_any"
    AVERAGE_OF_LAST_PER_CHANNEL = "average_of_last_per_channel"


class CCResolver:
    """
    Maintains per-channel CC state for a set of CC numbers and resolves
    them to a single 0..1 unit value using a configurable strategy.
    """

    def __init__(
        self,
        strategy: Union[Strategy, str, Callable[[Dict[int, int]], float]] = Strategy.MOST_RECENT_OF_ANY,
    ):
        if isinstance(strategy, str):
            strategy = Strategy(strategy)
        self._strategy = strategy
        # {channel: raw_value} -- most recent value per channel across all watched CCs
        self._per_channel: Dict[int, int] = {}
        self._last_value: Optional[int] = None
        self._last_t: float = -1e9

    def feed(self, cc: MidiCC) -> None:
        """Ingest a CC message that matched one of the watched CC numbers."""
        self._per_channel[cc.channel] = cc.value
        if cc.t >= self._last_t:
            self._last_value = cc.value
            self._last_t = cc.t

    def resolve(self) -> Optional[float]:
        """Compute a 0..1 unit value from accumulated state. None if no data yet."""
        if self._last_value is None:
            return None

        if callable(self._strategy) and not isinstance(self._strategy, Strategy):
            return clamp01(self._strategy(dict(self._per_channel)))

        if self._strategy == Strategy.MOST_RECENT_OF_ANY:
            return cc_unit(self._last_value)

        if self._strategy == Strategy.AVERAGE_OF_LAST_PER_CHANNEL:
            if not self._per_channel:
                return None
            avg = sum(self._per_channel.values()) / len(self._per_channel)
            return cc_unit(int(round(avg)))

        return cc_unit(self._last_value)

    def reset(self) -> None:
        self._per_channel.clear()
        self._last_value = None
        self._last_t = -1e9


# ---------------------------------------------------------------------------
# CCBinding + MidiRouter
# ---------------------------------------------------------------------------

class CCBinding:
    """
    Declarative link: a set of CC numbers -> one (effect, param_name) via a transform.

    Supports many-to-many: multiple bindings can watch the same CC, and one
    binding can watch multiple CCs.
    """

    def __init__(
        self,
        ccs: List[int],
        target: Any,
        param: str,
        transform: Any = None,
        strategy: Union[Strategy, str, Callable] = Strategy.MOST_RECENT_OF_ANY,
    ):
        self.ccs = set(ccs)
        self.target = target
        self.param = param
        self.transform = transform or IdentityTransform()
        self.resolver = CCResolver(strategy)

    def __repr__(self) -> str:
        return f"CCBinding(ccs={sorted(self.ccs)}, param={self.param!r}, transform={self.transform})"


class MidiRouter:
    """
    Central CC dispatcher.

    Holds a list of CCBindings, processes incoming CCs, resolves values,
    and pushes them to effect params via effect.set_param().
    """

    def __init__(self, log_mode: str = "none"):
        self._bindings: List[CCBinding] = []
        self._cc_to_bindings: Dict[int, List[CCBinding]] = {}
        self.log_mode = log_mode

    def add(self, binding: CCBinding) -> None:
        self._bindings.append(binding)
        for cc_num in binding.ccs:
            self._cc_to_bindings.setdefault(cc_num, []).append(binding)

    @property
    def mapped_ccs(self) -> set:
        """All CC numbers that have at least one binding."""
        return set(self._cc_to_bindings.keys())

    def process(self, cc_msgs: List[MidiCC]) -> None:
        """Feed CC messages through bindings and push resolved values to effects."""
        if not cc_msgs:
            return

        log_all = self.log_mode in ("all", "both")
        log_mapped = self.log_mode in ("mapped", "both")
        mapped_controls = self.mapped_ccs

        touched_bindings: set = set()

        for cc in cc_msgs:
            is_mapped = cc.control in mapped_controls
            if log_all:
                tag = "mapped" if is_mapped else "unmapped"
                print(f"[midi] {tag} t={cc.t:7.3f}s ch={cc.channel:2d} cc={cc.control:3d} val={cc.value:3d}")

            if is_mapped:
                for binding in self._cc_to_bindings[cc.control]:
                    binding.resolver.feed(cc)
                    touched_bindings.add(id(binding))

        if log_mapped and touched_bindings:
            count = sum(1 for cc in cc_msgs if cc.control in mapped_controls)
            controls = sorted({cc.control for cc in cc_msgs if cc.control in mapped_controls})
            print(
                f"[midi] mapped CC detected ({count} msg{'s' if count != 1 else ''}) "
                f"controls={controls}"
            )

        for binding in self._bindings:
            if id(binding) not in touched_bindings:
                continue
            unit = binding.resolver.resolve()
            if unit is None:
                continue
            value = binding.transform(unit)
            binding.target.set_param(binding.param, value)
            if log_mapped:
                print(f"[midi] {binding.param} -> {value:.3f}")

    def describe(self) -> str:
        """Human-readable summary of all bindings (for startup logging)."""
        parts = []
        for b in self._bindings:
            parts.append(f"{b.param}=cc{sorted(b.ccs)}")
        return " ".join(parts)
