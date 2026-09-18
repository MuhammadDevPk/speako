"""HUD event types.

Kept free of any GUI-toolkit imports so producers (the audio callback,
the runtime worker) can construct events without pulling Tkinter into
non-UI modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class HudState(Enum):
    HIDDEN = "hidden"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    PASTED = "pasted"


@dataclass(frozen=True, slots=True)
class HudStateEvent:
    state: HudState


@dataclass(frozen=True, slots=True)
class HudLevelEvent:
    level: float  # normalized [0.0, 1.0]


@dataclass(frozen=True, slots=True)
class HudShutdown:
    """Sentinel: tear down the HUD and exit the Tk mainloop."""


HudEvent = HudStateEvent | HudLevelEvent | HudShutdown
