#!/usr/bin/env -S python3 -u
"""
Base effect class for psiwave-matrix visual effects.

Subclasses declare parameters via class-level Param instances.
The MidiRouter pushes resolved CC values into params via set_param().
Effects never need to know CC numbers -- only parameter names.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from midi import MidiNote


class Param:
    """
    Descriptor for a named effect parameter.

    Declare at the class level; the Effect metaclass collects them
    into each instance's ``params`` dict.
    """

    def __init__(self, default: float = 0.0):
        self.default = default
        self.value = default
        self.name: Optional[str] = None

    def reset(self) -> None:
        self.value = self.default

    def __repr__(self) -> str:
        return f"Param({self.name!r}, value={self.value}, default={self.default})"


class _EffectMeta(type):
    """Metaclass that collects Param descriptors into a params registry."""

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        instance = super().__call__(*args, **kwargs)
        params: Dict[str, Param] = {}
        for klass in reversed(type(instance).__mro__):
            for attr_name, attr_val in vars(klass).items():
                if isinstance(attr_val, Param):
                    p = Param(default=attr_val.default)
                    p.name = attr_name
                    params[attr_name] = p
        instance.params = params
        return instance


class Effect(metaclass=_EffectMeta):
    """
    Base class for visual effects.

    Lifecycle (called by the main loop):
        setup(matrix)           -- once, before first frame
        activate()              -- each time the effect is switched to
        draw(canvas, matrix, t) -- every frame
        handle_note(note)       -- for each incoming MIDI note event

    The MidiRouter updates parameters by calling set_param(name, value).
    Subclasses read parameter values via self.params[name].value.
    """

    params: Dict[str, Param]

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height

    def setup(self, matrix: Any) -> None:
        """Called once before the first frame. Override to allocate buffers etc."""

    def activate(self) -> None:
        """Called each time this effect becomes the active demo."""

    def draw(self, canvas: Any, matrix: Any, t: float) -> None:
        """Render one frame at time *t* (seconds since start)."""
        raise NotImplementedError

    def handle_note(self, note: MidiNote) -> None:
        """Handle a MIDI note event. Override if the effect reacts to notes."""

    def set_param(self, name: str, value: float) -> None:
        """Push a resolved parameter value (called by MidiRouter)."""
        p = self.params.get(name)
        if p is not None:
            p.value = value

    def get_param(self, name: str) -> float:
        """Read the current value of a named parameter."""
        p = self.params.get(name)
        if p is not None:
            return p.value
        return 0.0
